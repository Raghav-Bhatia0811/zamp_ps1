"""Measure accuracy: extracted fields and final decisions vs the known truth.

The truth comes from the same data that generated the sample invoices, so every
field has a correct answer. Test sets:
  original    the 10 samples as shipped (7 digital, 2 scans, 1 phone photo)
  scan-light  all 10 re-rendered and scanned (170 dpi, slight skew, light noise)
  scan-heavy  all 10 as poor scans (120 dpi, more skew, heavy noise, blur)
  photo       all 10 as phone photos (angled, shadowed, on a desk, JPG)

Ablation (--ablation): scan-heavy and photo again with the vision layer off
(no clean-up, no region re-reads), to show what computer vision is worth.

Run:  .venv/bin/python scripts/evaluate.py [--engine local|claude|auto] [--ablation]
Runs in a scratch state directory; your demo database is not touched.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ["DEMO_PACING_MS"] = "0"
ROOT = Path(__file__).resolve().parent.parent
_tmp_state = tempfile.mkdtemp(prefix="ledgerline-eval-")
os.environ["STATE_DIR"] = _tmp_state
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image, ImageFilter  # noqa: E402

import make_samples  # noqa: E402
from app import db, pipeline, rules, vision  # noqa: E402

EXPECTED = {
    "01": "approved", "02": "approved", "03": "hold", "04": "rejected", "05": "hold",
    "06": "hold", "07": "hold", "08": "approved", "09": "hold", "10": "hold",
}
REASON = {"03": "R13", "04": "R10", "05": "R09", "06": "R12", "07": "R06", "09": "R00", "10": "R14"}
# in re-rendered sets the fax (09) is a clean page again, so it should go through
RERENDER_OVERRIDE = {"09": ("approved", None)}
FIELDS = ["vendor_gstin", "invoice_number", "invoice_date", "due_date", "po_reference",
          "subtotal", "tax_total", "total", "bank_account", "bank_ifsc", "line_items"]


def iso(s):
    for fmt in ("%d %b %Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def truth(inv):
    sub, tax, tot = make_samples.totals(inv)
    return {
        "vendor_gstin": inv["vendor"]["gstin"], "invoice_number": inv["number"],
        "invoice_date": iso(inv["date"]), "due_date": iso(inv["due"]), "po_reference": inv.get("po"),
        "subtotal": sub, "tax_total": tax, "total": tot,
        "bank_account": inv["bank"]["account"], "bank_ifsc": inv["bank"]["ifsc"],
        "line_items": [(q, r, round(q * r, 2)) for _, _, q, r in inv["lines"]],
    }


def same(field, got, want):
    if field in ("subtotal", "tax_total", "total"):
        return got is not None and want is not None and abs(got - want) < 0.5
    if field == "line_items":
        got_l = [(li.get("quantity"), li.get("unit_price"), li.get("amount")) for li in got or []]
        return len(got_l) == len(want) and all(
            g[0] is not None and abs(g[0] - w[0]) < 0.01 and g[1] is not None and abs(g[1] - w[1]) < 0.5
            and g[2] is not None and abs(g[2] - w[2]) < 0.5 for g, w in zip(got_l, want))
    if field == "invoice_number":
        return rules.canon_number(got) == rules.canon_number(want)
    return (got or None) == (want or None)


def scan(src: Path, dst: Path, dpi: int, angle: float, noise_div: int, blur: float, seed: int):
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1", str(src), f"{td}/p"], check=True)
        img = Image.open(next(Path(td).glob("p*.png"))).convert("RGB")
        img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
        rnd, px = random.Random(seed), img.load()
        w, h = img.size
        for _ in range(w * h // noise_div):
            x, y = rnd.randrange(w), rnd.randrange(h)
            v = max(0, px[x, y][0] - rnd.randint(40, 120))
            px[x, y] = (v, v, v)
        img.filter(ImageFilter.GaussianBlur(blur)).save(dst, "PDF", resolution=dpi)


def run_set(name, files, engine, out, rerendered=False):
    db.init(reset=True)
    field_hits, n_field_docs = {f: 0 for f in FIELDS}, 0
    rows = []
    for i, (path, inv) in enumerate(files, 1):
        rid = f"EVAL-{name}-{i}"
        kind = vision.sniff(path.read_bytes())
        dest = Path(_tmp_state) / "uploads" / f"{rid}.{kind}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, dest)
        pipeline.new_run(rid, path.name, dest, None, engine, kind)
        pipeline.execute(rid)
        r = db.get_run(rid)
        t = truth(inv)
        f = r.get("fields") or {}
        key = inv["file"][:2]
        want, reason = EXPECTED[key], REASON.get(key)
        if rerendered and key in RERENDER_OVERRIDE:
            want, reason = RERENDER_OVERRIDE[key]
        hits = {k: same(k, f.get(k), t[k]) for k in FIELDS}
        if reason != "R00":     # an illegible-by-design page has no fields to score
            n_field_docs += 1
            for k, v in hits.items():
                field_hits[k] += v
        decision = (r.get("result") or {}).get("decision", "error")
        blocking = [c["id"] for c in r.get("checks", []) if c["status"] == "fail"]
        reason_ok = (not blocking) if reason is None else (reason in blocking)
        vis = r.get("vision") or {}
        rows.append({"sample": inv["file"], "engine": r.get("engine"), "fields_correct": sum(hits.values()),
                     "fields_total": len(FIELDS), "wrong_fields": [k for k, v in hits.items() if not v],
                     "decision": decision, "expected": want, "decision_ok": decision == want,
                     "blocking_checks": blocking, "reason_ok": decision == want and reason_ok,
                     "legibility": vis.get("score"), "grade": vis.get("grade"),
                     "repairs": len((r.get("verification") or {}).get("repairs") or []), "ms": r.get("duration_ms"),
                     "error": r.get("error")})
    n = len(files)
    out[name] = {
        "field_accuracy": round(sum(field_hits.values()) / max(1, n_field_docs * len(FIELDS)) * 100, 1),
        "per_field": {k: f"{v}/{n_field_docs}" for k, v in field_hits.items()},
        "decision_accuracy": f"{sum(r['decision_ok'] for r in rows)}/{n}",
        "decision_and_reason_accuracy": f"{sum(r['reason_ok'] for r in rows)}/{n}",
        "repairs": sum(r["repairs"] for r in rows),
        "avg_ms": round(sum(r["ms"] or 0 for r in rows) / n),
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="local", choices=["local", "claude", "auto"])
    ap.add_argument("--ablation", action="store_true", help="also run scan-heavy and photo with the vision layer off")
    ap.add_argument("--sets", default="original,scan-light,scan-heavy,photo")
    args = ap.parse_args()
    wanted = args.sets.split(",")
    invs = make_samples.build_samples()
    out = {"engine": args.engine, "run_at": datetime.now().isoformat(timespec="seconds"),
           "layout_model": vision.layout_available()}
    if "original" in wanted:
        run_set("original", [(ROOT / "samples" / inv["file"], inv) for inv in invs], args.engine, out)
    with tempfile.TemporaryDirectory() as td:
        clean = Path(td) / "clean"
        clean.mkdir()
        for inv in invs:   # re-render every invoice clean (marks included) before damaging it
            make_samples.render_pdf(inv, clean / (inv["file"][:-4] + ".pdf"))
        sets = {}
        for name, (dpi, angle, noise, blur) in {"scan-light": (170, 0.45, 160, 0.45), "scan-heavy": (120, 1.2, 60, 0.75)}.items():
            if name not in wanted:
                continue
            d = Path(td) / name
            d.mkdir()
            files = []
            for k, inv in enumerate(invs):
                p = d / (inv["file"][:-4] + ".pdf")
                scan(clean / p.name, p, dpi, angle, noise, blur, seed=11 + k)
                files.append((p, inv))
            sets[name] = files
        if "photo" in wanted:
            d = Path(td) / "photo"
            d.mkdir()
            sets["photo"] = []
            for k, inv in enumerate(invs):
                p = d / (inv["file"][:-4] + ".jpg")
                make_samples.photoify(clean / (inv["file"][:-4] + ".pdf"), p, seed=21 + k)
                sets["photo"].append((p, inv))
        for name, files in sets.items():
            run_set(name, files, args.engine, out, rerendered=True)
        if args.ablation:
            vision.ENHANCE, vision.REGION_OCR = False, False
            for name in ("scan-heavy", "photo"):
                if name in sets:
                    run_set(f"{name} (no vision)", sets[name], args.engine, out, rerendered=True)
            vision.ENHANCE, vision.REGION_OCR = True, True
    shutil.rmtree(_tmp_state, ignore_errors=True)
    (ROOT / "data" / f"eval_{args.engine}.json").write_text(json.dumps(out, indent=2))
    for name, s in out.items():
        if not isinstance(s, dict):
            continue
        print(f"\n== {name}: fields {s['field_accuracy']}%  decisions {s['decision_accuracy']}  right reason "
              f"{s['decision_and_reason_accuracy']}  repairs {s['repairs']}  avg {s['avg_ms']}ms")
        print("   per field:", s["per_field"])
        for r in s["rows"]:
            flag = "OK " if r["decision_ok"] else "BAD"
            print(f"   {flag} {r['sample'][:30]:30} {r['decision']:8} (want {r['expected']:8}) leg={r['legibility']} "
                  f"fields {r['fields_correct']}/{r['fields_total']} wrong={r['wrong_fields']} checks={r['blocking_checks']} "
                  f"reason={'ok' if r['reason_ok'] else 'WRONG'}" + (f" ERROR {r['error']}" if r.get("error") else ""))


if __name__ == "__main__":
    main()
