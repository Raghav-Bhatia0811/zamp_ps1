"""Runs one invoice through every stage and records each step as it happens.

The run record is the audit trail: every stage's timing, output and the checks
it ran are saved, and the UI streams it live while the run executes.

    Receive → Clean up image → Find regions → Read → Verify fields
            → Identify vendor → Validate → Match PO → Controls → Decide

If the document is not legible even after clean-up, nothing is decided on
values that could not be read: validation, matching and controls are skipped
and the invoice is held with a request to the vendor for a clearer copy.
"""
from __future__ import annotations

import hashlib
import os
import time
import traceback
from datetime import date
from pathlib import Path

from . import db, extract, knowledge, rules, verify, vision

PACING_MS = int(os.environ.get("DEMO_PACING_MS", "450"))  # minimum visible time per stage; 0 to disable

STAGES = [
    ("intake", "Receive document"),
    ("vision", "Clean up image"),
    ("layout", "Find regions"),
    ("extract", "Read invoice"),
    ("verify", "Verify fields"),
    ("vendor", "Identify vendor"),
    ("validate", "Validate invoice"),
    ("match", "Match purchase order"),
    ("controls", "Run controls"),
    ("decide", "Decide and route"),
]
SOURCE_LABEL = {"text": "Digital PDF", "scan": "Scanned PDF", "photo": "Photo / image"}


def new_run(run_id: str, filename: str, path: Path, sample: str | None, engine: str, kind: str = "pdf") -> dict:
    run = {
        "id": run_id, "created_at": db.now_iso(), "filename": filename, "sample": sample, "kind": kind,
        "path": str(path), "engine_requested": engine, "status": "queued",
        "stages": [{"key": k, "label": l, "status": "pending", "summary": "", "ms": None, "log": []} for k, l in STAGES],
        "fields": None, "vendor": None, "po_match": None, "controls": None, "checks": [], "result": None,
        "review": None, "version": 0, "artifacts": [],
    }
    db.save_run(run)
    return run


class Stage:
    def __init__(self, run, key):
        self.run, self.st = run, next(s for s in run["stages"] if s["key"] == key)

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.st["status"] = "running"
        self.st["started_at"] = db.now_iso()
        self.run["status"] = "running"
        self._save()
        return self

    def log(self, line):
        self.st["log"].append(line)

    def finish(self, status, summary, detail=None, checks=None):
        self.st["status"], self.st["summary"] = status, summary
        if detail is not None:
            self.st["detail"] = detail
        if checks is not None:
            self.st["checks"] = [c["id"] for c in checks]

    def __exit__(self, et, ev, tb):
        elapsed = (time.perf_counter() - self.t0) * 1000
        if PACING_MS and elapsed < PACING_MS:
            time.sleep((PACING_MS - elapsed) / 1000)
        self.st["ms"] = round(elapsed)
        if et:
            self.st["status"] = "error"
            self.st["summary"] = f"{et.__name__}: {ev}"
        elif self.st["status"] == "running":
            self.st["status"] = "done"
        self._save()
        return False

    def _save(self):
        self.run["version"] += 1
        db.save_run(self.run)


def skip(run, key, why):
    st = next(s for s in run["stages"] if s["key"] == key)
    st.update(status="skipped", summary=why, ms=0)
    run["version"] += 1
    db.save_run(run)


def stage_status(checks):
    if any(c["status"] == "fail" and c["severity"] == "reject" for c in checks):
        return "fail"
    if any(c["status"] == "fail" for c in checks):
        return "hold"
    if any(c["status"] == "warn" for c in checks):
        return "warn"
    return "done"


def _art(run, name, img, long_edge=1400):
    p = db.ARTIFACTS / run["id"] / name
    vision.save_preview(img, p, long_edge)
    if name not in run["artifacts"]:
        run["artifacts"].append(name)
    return name


def _near(box, dets, classes=("totals", "bank_details", "line_items"), margin=40):
    """Which money region a handwriting box sits on or next to."""
    x0, y0, x1, y1 = box
    best, area = None, 0
    for d in dets:
        if d["cls"] not in classes:
            continue
        a0, b0, a1, b1 = d["box"]
        ix = max(0, min(x1, a1 + margin) - max(x0, a0 - margin))
        iy = max(0, min(y1, b1 + margin) - max(y0, b0 - margin))
        if ix * iy > area:
            best, area = d["cls"], ix * iy
    return {"totals": "totals", "bank_details": "bank details", "line_items": "line items"}.get(best)


def execute(run_id: str):
    run = db.get_run(run_id)
    m = db.master()
    path = Path(run["path"])
    t_start = time.perf_counter()
    try:
        # ------------------------------------------------ 1. receive
        with Stage(run, "intake") as s:
            data = path.read_bytes()
            run["file_hash"] = hashlib.sha256(data).hexdigest()
            kind = run.get("kind") or vision.sniff(data) or "pdf"
            if kind == "pdf":
                info = extract.inspect_pdf(path)
                source = "text" if info["has_text_layer"] else "scan"
            else:
                from PIL import Image
                im = Image.open(path)
                info = {"pages": getattr(im, "n_frames", 1), "has_text_layer": False, "size_kb": round(len(data) / 1024, 1),
                        "kind": f"Photo / image ({kind.upper()}, {im.width}×{im.height})"}
                source = "photo"
            info["source"] = source
            run["source"] = source
            s.log(f"SHA-256 {run['file_hash'][:16]}…")
            s.finish("done", f"{info['kind']} · {info['pages']} page{'s' if info['pages'] != 1 else ''} · {info['size_kb']} KB", detail=info)

        # ------------------------------------------------ 2. clean up image
        with Stage(run, "vision") as s:
            pages = vision.load_pages(path, kind, dpi=200)
            clean, colour, page_reports, ocr_pages = [], [], [], []
            if source == "text":
                for i, p in enumerate(pages, 1):
                    _art(run, f"p{i}-original.jpg", p)
                clean, colour = [vision.gray(p) for p in pages], pages
                vis = {"source": "text", "source_label": SOURCE_LABEL[source], "grade": "digital", "score": 100,
                       "pages": [{"page": i + 1, "original": f"p{i + 1}-original.jpg"} for i in range(len(pages))]}
                s.log("Digital PDF: the text layer is read directly, so the page needs no clean-up")
            else:
                for i, p in enumerate(pages, 1):
                    g, c, rep = vision.clean_page(p, source)
                    o = vision.ocr(g, psm=6)
                    grade, score = vision.legibility_grade(o["mean_conf"], o["n_words"])
                    ocr_pages.append(o)
                    clean.append(g)
                    colour.append(c)
                    rep.update(page=i, ocr_conf=o["mean_conf"], words=o["n_words"], grade=grade, score=score,
                               original=_art(run, f"p{i}-original.jpg", p), cleaned=_art(run, f"p{i}-clean.jpg", g))
                    page_reports.append(rep)
                    for st in rep["steps"]:
                        s.log(f"Page {i}: {st}")
                words = sum(o["n_words"] for o in ocr_pages)
                conf = round(sum(o["mean_conf"] * o["n_words"] for o in ocr_pages) / words, 1) if words else 0.0
                grade, score = vision.legibility_grade(conf, words)
                vis = {"source": source, "source_label": SOURCE_LABEL[source], "grade": grade, "score": score,
                       "ocr_conf": conf, "words": words, "pages": page_reports}
                s.log(f"Legibility after clean-up: {score}/100 ({grade}); OCR confidence {conf}% over {words} words")
            run["vision"] = vis
            r00 = rules.legibility_check(vis)
            run["checks"].append(r00)
            st_ = {"pass": "done", "warn": "warn", "fail": "hold"}[r00["status"]]
            if source == "text":
                summ = "Digital PDF · no clean-up needed"
            else:
                n_steps = sum(len([x for x in p["steps"] if "nothing to change" not in x]) for p in page_reports)
                summ = f"{SOURCE_LABEL[source]} · {n_steps} fix{'es' if n_steps != 1 else ''} applied · legibility {score}/100 ({grade})"
            s.finish(st_, summ, detail=vis, checks=[r00])
        legible = r00["status"] != "fail"

        # ------------------------------------------------ 3. find regions (YOLO)
        with Stage(run, "layout") as s:
            lay = {"available": vision.layout_available(), "detections": [], "pages": []}
            regions_text, region_words = {}, []
            if not lay["available"]:
                s.finish("warn", "Layout model not installed · reading the whole page", detail=lay)
            else:
                t0 = time.perf_counter()
                for i, (g, c) in enumerate(zip(clean, colour), 1):
                    dets = vision.detect_layout(c)
                    for k, d in enumerate(dets):
                        d["page"] = i
                        if d["cls"] == "stamp":
                            d["read"] = vision.read_stamp(c, d["box"])
                            d["crop"] = _art(run, f"p{i}-stamp-{k}.jpg", vision.crop(c, d["box"]), 500)
                        elif d["cls"] in ("handwriting", "signature"):
                            d["crop"] = _art(run, f"p{i}-{d['cls']}-{k}.jpg", vision.crop(c, d["box"]), 500)
                            if d["cls"] == "handwriting":
                                d["near"] = _near(d["box"], dets)
                        elif source != "text" and vision.REGION_OCR and d["cls"] in ("totals", "bank_details", "invoice_meta", "vendor_block", "line_items"):
                            rr = vision.read_region(g, d["box"])
                            d["ocr_conf"] = rr["mean_conf"]
                            region_words += rr.get("words") or []
                            regions_text[d["cls"]] = (regions_text.get(d["cls"], "") + "\n" + rr["text"]).strip()
                    lay["detections"] += dets
                    lay["pages"].append({"page": i, "overlay": _art(run, f"p{i}-layout.jpg", vision.draw_layout(c, dets))})
                lay["ms_model"] = round((time.perf_counter() - t0) * 1000)
                lay["regions_read"] = sorted(regions_text)
                found = {}
                for d in lay["detections"]:
                    found[d["label"]] = found.get(d["label"], 0) + 1
                for d in lay["detections"]:
                    if d["cls"] == "stamp":
                        s.log(f"Stamp on page {d['page']}: read as “{d['read'].get('text') or '?'}”"
                              + (f" → {d['read']['keyword']}" if d["read"].get("keyword") else ""))
                    if d["cls"] == "handwriting":
                        s.log(f"Handwriting on page {d['page']}" + (f", on the {d['near']}" if d.get("near") else ""))
                if regions_text:
                    s.log("Re-read on their own at higher scale: " + ", ".join(vision.LAYOUT_LABEL[k] for k in sorted(regions_text)))
                core = sum(1 for d in lay["detections"] if d["cls"] in ("vendor_block", "line_items", "totals", "bank_details"))
                marks = [d for d in lay["detections"] if d["cls"] in ("stamp", "handwriting")]
                summ = f"{len(lay['detections'])} regions found" + (f" · {', '.join(f'{v} {k.lower()}' for k, v in found.items() if k in ('Stamp', 'Handwriting', 'Signature'))}" if marks or found.get("Signature") else "")
                s.finish("warn" if marks else ("done" if core >= 2 else "warn"), summ, detail=lay)
            run["layout"] = lay

        # ------------------------------------------------ 4. read
        with Stage(run, "extract") as s:
            page_text = "\n\f\n".join(o["text"] for o in ocr_pages) if ocr_pages else None
            fields, log = extract.extract(path, source == "text", run["engine_requested"], pages=clean,
                                          ocr_text=page_text, regions=regions_text)
            for ln in log:
                s.log(ln)
            meta = fields.get("_meta", {})
            run["engine"] = meta.get("engine")
            run["engine_meta"] = meta
            second = None
            if meta.get("engine") == "claude" and source != "text" and page_text:
                second = extract.extract_locally(path, False, page_text, regions_text)
                s.log("Second, independent read by OCR kept for cross-checking (dual read)")
            n_items = len(fields.get("line_items") or [])
            got = sum(1 for k in ("vendor_name", "invoice_number", "invoice_date", "po_reference", "subtotal", "total") if fields.get(k) is not None)
            reader = "Claude" if meta.get("engine") == "claude" else ("local parser" if source == "text" else "OCR")
            s.finish("warn" if fields.get("low_confidence_fields") else "done",
                     f"{got}/6 header fields and {n_items} line item{'s' if n_items != 1 else ''} read by {reader}",
                     detail={"raw": {k: v for k, v in fields.items() if k != '_meta'}})

        # ------------------------------------------------ 5. verify fields
        with Stage(run, "verify") as s:
            # region re-reads first: where a field was read on its own, that read's confidence is the one that counts
            words = region_words + [w for o in ocr_pages for w in o["words"]]
            ver = verify.verify(fields, {"source": source, "ocr_words": words, "second_read": second}, m)
            run["verification"] = ver
            for ln in ver["log"]:
                s.log(ln)
            r08 = rules.confidence_check(ver)
            run["checks"].append(r08)
            fs = ver["fields"]
            proven = sum(1 for v in fs.values() if v["status"] in ("verified", "agreed"))
            present = sum(1 for v in fs.values() if v["status"] != "missing")
            summ = f"{proven} of {present} fields proven · confidence {ver['score']}%"
            if ver["repairs"]:
                summ += f" · {len(ver['repairs'])} OCR slip{'s' if len(ver['repairs']) != 1 else ''} repaired"
            if ver["critical_issues"]:
                summ += f" · {len(ver['critical_issues'])} need{'s' if len(ver['critical_issues']) == 1 else ''} a person"
            s.finish({"pass": "done", "warn": "warn", "fail": "hold"}[r08["status"]], summ, detail=ver, checks=[r08])

        # ------------------------------------------------ 6. vendor
        with Stage(run, "vendor") as s:
            n, vendor, how = rules.normalise(fields, m)
            run["fields"], run["vendor"] = n, vendor
            s.log(how)
            if vendor:
                s.finish("done", f"{vendor['name']} · {vendor['id']} · {how}")
            else:
                s.finish("hold", f"“{n.get('vendor_name') or 'Unknown vendor'}”: not in the vendor master")

        if not legible:
            why = "Skipped: the document is not legible, so nothing is checked on values that could not be read"
            for k in ("validate", "match", "controls"):
                skip(run, k, why)
            run["po_match"] = {"method": "none", "po_id": None}
        else:
            # -------------------------------------------- 7. validate
            with Stage(run, "validate") as s:
                v_checks = rules.validate(n, vendor, m, date.today())
                sig = rules.signature_check(run["layout"], source, page_text or "")
                if sig:
                    v_checks.append(sig)
                run["checks"] += v_checks
                fails = [c for c in v_checks if c["status"] != "pass"]
                s.finish(stage_status(v_checks),
                         f"{len(v_checks) - len(fails)} of {len(v_checks)} checks passed" + (f" · {', '.join(rules.short(c) for c in fails)}" if fails else ""),
                         checks=v_checks)

            ledger = db.ledger()
            # -------------------------------------------- 8. match PO
            with Stage(run, "match") as s:
                match, m_checks = rules.match_po(n, vendor, m, ledger)
                run["po_match"] = match
                run["checks"] += m_checks
                if match.get("method") == "explicit" and match.get("po_id"):
                    summ = f"{match['po_id']} · printed on invoice"
                elif match.get("method") == "inferred":
                    summ = f"No PO printed · inferred {match['po_id']} at {match['confidence']:.0%} confidence"
                else:
                    summ = "No matching purchase order"
                s.finish(stage_status(m_checks), summ, detail=match, checks=m_checks)

            # -------------------------------------------- 9. controls
            with Stage(run, "controls") as s:
                seen = {r["file_hash"]: r["id"] for r in db.list_runs()
                        if r["id"] != run["id"] and r.get("file_hash") and _effective_decision(r) in ("approved", "hold")}
                c_detail, c_checks = rules.controls(n, vendor, match, m, ledger, run["file_hash"], seen)
                if run["layout"].get("available"):
                    c_checks.append(rules.markings_check(run["layout"]))
                run["controls"] = c_detail
                run["checks"] += c_checks
                fails = [c for c in c_checks if c["status"] not in ("pass", "warn")]
                s.finish(stage_status(c_checks),
                         ("Duplicate, price, PO-balance and markings checks clear" if not fails else "Found: " + ", ".join(rules.short(c) for c in fails)),
                         detail=c_detail, checks=c_checks)

        # ------------------------------------------------ 10. decide
        with Stage(run, "decide") as s:
            result = rules.decide(run["checks"], n, vendor)
            for c in run["checks"]:
                if c["status"] != "pass":
                    c["policy"] = knowledge.cite(c["id"], c.get("detail", ""))
            if result["decision"] != "approved":
                try:
                    result["similar"] = knowledge.similar_cases(run)
                    if result["similar"]:
                        s.log(f"Found {len(result['similar'])} similar past decision{'s' if len(result['similar']) != 1 else ''} "
                              f"({knowledge.retrieval_mode()})")
                except Exception as e:  # retrieval is advisory; never block a decision on it
                    s.log(f"Precedent search unavailable ({type(e).__name__})")
            run["result"] = result
            if result["decision"] == "approved":
                db.post_to_ledger(run)
                s.log("Posted to AP ledger")
            s.finish({"approved": "done", "hold": "hold", "rejected": "fail"}[result["decision"]],
                     f"{result['label']} · routed to {result['queue']}")
        run["status"] = "done"
    except Exception as e:
        run["status"] = "error"
        run["error"] = f"{type(e).__name__}: {e}"
        run["traceback"] = traceback.format_exc()[-2000:]
    run["duration_ms"] = round((time.perf_counter() - t_start) * 1000)
    run["finished_at"] = db.now_iso()
    run["version"] += 1
    db.save_run(run)


def _effective_decision(run):
    if run.get("review"):
        return "approved" if run["review"]["action"] == "approve" else "rejected"
    return (run.get("result") or {}).get("decision")


def review(run_id: str, action: str, note: str, reviewer: str) -> dict:
    run = db.get_run(run_id)
    if not run or not run.get("result"):
        raise ValueError("Run not found or not finished")
    if run["result"]["decision"] != "hold" or run.get("review"):
        raise ValueError("Only runs on hold can be reviewed, once")
    run["review"] = {"action": action, "note": note, "by": reviewer, "at": db.now_iso()}
    if action == "approve":
        db.post_to_ledger(run, status="approved")
    run["version"] += 1
    db.save_run(run)
    # the decision becomes a precedent for similar cases later
    blocking = [c for c in run.get("checks", []) if c["status"] == "fail"]
    if blocking and note:
        f = run.get("fields") or {}
        db.add_precedent({
            "id": f"P-{run['id']}", "run_id": run["id"], "date": run["review"]["at"][:10],
            "vendor": (run.get("vendor") or {}).get("name") or f.get("vendor_name") or "Unknown vendor",
            "check": blocking[0]["id"], "decision": "approved" if action == "approve" else "rejected", "amount": f.get("total"),
            "facts": " ".join(c["detail"] for c in blocking[:2]), "note": note,
        })
    return run
