"""Runs one invoice through every stage and records each step as it happens.

The run record is the audit trail: every stage's timing, output and the checks
it ran are saved, and the UI streams it live while the run executes.
"""
from __future__ import annotations

import hashlib
import os
import time
import traceback
from datetime import date
from pathlib import Path

from . import db, extract, rules

PACING_MS = int(os.environ.get("DEMO_PACING_MS", "450"))  # minimum visible time per stage; 0 to disable

STAGES = [
    ("intake", "Receive document"),
    ("extract", "Read invoice"),
    ("vendor", "Identify vendor"),
    ("validate", "Validate invoice"),
    ("match", "Match purchase order"),
    ("controls", "Run controls"),
    ("decide", "Decide and route"),
]


def new_run(run_id: str, filename: str, path: Path, sample: str | None, engine: str) -> dict:
    run = {
        "id": run_id, "created_at": db.now_iso(), "filename": filename, "sample": sample,
        "path": str(path), "engine_requested": engine, "status": "queued",
        "stages": [{"key": k, "label": l, "status": "pending", "summary": "", "ms": None, "log": []} for k, l in STAGES],
        "fields": None, "vendor": None, "po_match": None, "controls": None, "checks": [], "result": None,
        "review": None, "version": 0,
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


def stage_status(checks):
    if any(c["status"] == "fail" and c["severity"] == "reject" for c in checks):
        return "fail"
    if any(c["status"] == "fail" for c in checks):
        return "hold"
    if any(c["status"] == "warn" for c in checks):
        return "warn"
    return "done"


def execute(run_id: str):
    run = db.get_run(run_id)
    m = db.master()
    path = Path(run["path"])
    t_start = time.perf_counter()
    try:
        with Stage(run, "intake") as s:
            info = extract.inspect_pdf(path)
            run["file_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
            s.log(f"SHA-256 {run['file_hash'][:16]}…")
            s.finish("done", f"{info['kind']} · {info['pages']} page{'s' if info['pages'] != 1 else ''} · {info['size_kb']} KB", detail=info)

        with Stage(run, "extract") as s:
            fields, log = extract.extract(path, info["has_text_layer"], run["engine_requested"])
            for ln in log:
                s.log(ln)
            meta = fields.get("_meta", {})
            run["engine"] = meta.get("engine")
            run["engine_meta"] = meta
            n_items = len(fields.get("line_items") or [])
            got = sum(1 for k in ("vendor_name", "invoice_number", "invoice_date", "po_reference", "subtotal", "total") if fields.get(k) is not None)
            s.finish("warn" if fields.get("low_confidence_fields") else "done",
                     f"{got}/6 header fields and {n_items} line item{'s' if n_items != 1 else ''} read by "
                     f"{'Claude' if meta.get('engine') == 'claude' else ('local OCR' if not info['has_text_layer'] else 'local parser')}", detail={"raw": {k: v for k, v in fields.items() if k != '_meta'}})

        with Stage(run, "vendor") as s:
            n, vendor, how = rules.normalise(fields, m)
            run["fields"], run["vendor"] = n, vendor
            s.log(how)
            if vendor:
                s.finish("done", f"{vendor['name']} · {vendor['id']} · {how}")
            else:
                s.finish("hold", f"“{n.get('vendor_name') or 'Unknown vendor'}”: not in the vendor master")

        with Stage(run, "validate") as s:
            v_checks = rules.validate(n, vendor, m, date.today())
            run["checks"] += v_checks
            fails = [c for c in v_checks if c["status"] != "pass"]
            s.finish(stage_status(v_checks),
                     f"{len(v_checks) - len(fails)} of {len(v_checks)} checks passed" + (f" · {', '.join(rules.short(c) for c in fails)}" if fails else ""),
                     checks=v_checks)

        ledger = db.ledger()
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

        with Stage(run, "controls") as s:
            seen = {r["file_hash"]: r["id"] for r in db.list_runs()
                    if r["id"] != run["id"] and r.get("file_hash") and _effective_decision(r) in ("approved", "hold")}
            c_detail, c_checks = rules.controls(n, vendor, match, m, ledger, run["file_hash"], seen)
            run["controls"] = c_detail
            run["checks"] += c_checks
            fails = [c for c in c_checks if c["status"] != "pass"]
            s.finish(stage_status(c_checks),
                     ("Duplicate, price and PO-balance checks clear" if not fails else "Found: " + ", ".join(rules.short(c) for c in fails)),
                     detail=c_detail, checks=c_checks)

        with Stage(run, "decide") as s:
            result = rules.decide(run["checks"], n, vendor)
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
    return run
