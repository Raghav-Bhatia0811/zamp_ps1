"""Measure accuracy: extracted fields and final decisions vs the known truth.

The truth comes from the same data that generated the sample PDFs, so every
field has a correct answer. Three test sets:
  original  - the 7 samples as shipped (6 digital, 1 scanned)
  scan-light - all 7 turned into scans (170 dpi, slight skew, light noise)
  scan-heavy - all 7 turned into poor scans (120 dpi, more skew, more noise)

Run:  .venv/bin/python scripts/evaluate.py [--engine local|claude|auto]
WARNING: this resets the demo database (runs and ledger).
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image, ImageFilter  # noqa: E402

import make_samples  # noqa: E402
from app import db, extract, pipeline, rules  # noqa: E402

EXPECTED = {
    "01": "approved", "02": "approved", "03": "hold", "04": "rejected",
    "05": "hold", "06": "hold", "07": "hold",
}
REASON = {"03": "R13", "04": "R10", "05": "R09", "06": "R12", "07": "R06"}  # check that must cause the hold/reject
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
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", "1", "-l", "1", str(src), f"{td}/p"], check=True)
        img = Image.open(next(Path(td).glob("p*.png"))).convert("L")
        img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
        rnd, px = random.Random(seed), img.load()
        w, h = img.size
        for _ in range(w * h // noise_div):
            x, y = rnd.randrange(w), rnd.randrange(h)
            px[x, y] = max(0, px[x, y] - rnd.randint(40, 120))
        img.filter(ImageFilter.GaussianBlur(blur)).save(dst, "PDF", resolution=dpi)


def run_set(name, files, engine, out):
    db.init(reset=True)
    field_hits = {f: 0 for f in FIELDS}
    rows = []
    for i, (path, inv) in enumerate(files, 1):
        rid = f"EVAL-{name}-{i}"
        dest = ROOT / "data" / "uploads" / f"{rid}.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, dest)
        pipeline.new_run(rid, path.name, dest, None, engine)
        pipeline.execute(rid)
        r = db.get_run(rid)
        t = truth(inv)
        f = r.get("fields") or {}
        hits = {k: same(k, f.get(k), t[k]) for k in FIELDS}
        for k, v in hits.items():
            field_hits[k] += v
        key = inv["file"][:2]
        decision = (r.get("result") or {}).get("decision", "error")
        blocking = [c["id"] for c in r.get("checks", []) if c["status"] == "fail"]
        reason_ok = (not blocking) if key not in REASON else (REASON[key] in blocking)
        rows.append({"sample": inv["file"], "engine": r.get("engine"), "fields_correct": sum(hits.values()),
                     "fields_total": len(FIELDS), "wrong_fields": [k for k, v in hits.items() if not v],
                     "decision": decision, "expected": EXPECTED[key], "decision_ok": decision == EXPECTED[key],
                     "blocking_checks": blocking, "reason_ok": decision == EXPECTED[key] and reason_ok,
                     "ms": r.get("duration_ms")})
    n = len(files)
    out[name] = {
        "field_accuracy": round(sum(field_hits.values()) / (n * len(FIELDS)) * 100, 1),
        "per_field": {k: f"{v}/{n}" for k, v in field_hits.items()},
        "decision_accuracy": f"{sum(r['decision_ok'] for r in rows)}/{n}",
        "decision_and_reason_accuracy": f"{sum(r['reason_ok'] for r in rows)}/{n}",
        "avg_ms": round(sum(r["ms"] or 0 for r in rows) / n),
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="local", choices=["local", "claude", "auto"])
    args = ap.parse_args()
    invs = make_samples.build_samples()
    samples = [(ROOT / "samples" / inv["file"], inv) for inv in invs]
    out = {"engine": args.engine, "run_at": datetime.now().isoformat(timespec="seconds")}
    run_set("original", samples, args.engine, out)
    with tempfile.TemporaryDirectory() as td:
        # scan from clean renders (sample 05 is already a scan, so re-render it clean first)
        clean = Path(td) / "clean"
        clean.mkdir()
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        for inv in invs:
            c = canvas.Canvas(str(clean / inv["file"]), pagesize=A4)
            inv["layout"](c, inv)
            c.showPage()
            c.save()
        for name, (dpi, angle, noise, blur) in {"scan-light": (170, 0.45, 160, 0.45), "scan-heavy": (120, 0.9, 70, 0.7)}.items():
            d = Path(td) / name
            d.mkdir()
            files = []
            for k, inv in enumerate(invs):
                p = d / inv["file"]
                scan(clean / inv["file"], p, dpi, angle, noise, blur, seed=11 + k)
                files.append((p, inv))
            run_set(name, files, args.engine, out)
    db.init(reset=True)
    for p in (ROOT / "data" / "uploads").glob("EVAL-*.pdf"):
        p.unlink()
    (ROOT / "data" / f"eval_{args.engine}.json").write_text(json.dumps(out, indent=2))
    for name in ("original", "scan-light", "scan-heavy"):
        s = out[name]
        print(f"\n== {name}: fields {s['field_accuracy']}%  decisions {s['decision_accuracy']}  right reason {s['decision_and_reason_accuracy']}  avg {s['avg_ms']}ms")
        print("   per field:", s["per_field"])
        for r in s["rows"]:
            flag = "OK " if r["decision_ok"] else "BAD"
            print(f"   {flag} {r['sample'][:30]:30} {r['decision']:8} (want {r['expected']:8}) "
                  f"fields {r['fields_correct']}/{r['fields_total']} wrong={r['wrong_fields']} checks={r['blocking_checks']} reason={'ok' if r['reason_ok'] else 'WRONG'}")


if __name__ == "__main__":
    main()
