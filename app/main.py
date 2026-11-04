"""HTTP API + static UI.  Run:  .venv/bin/uvicorn app.main:app --reload"""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, extract, pipeline, rules

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "samples"
UPLOADS = db.STATE_DIR / "uploads"
STATIC = ROOT / "static"

SAMPLES = [
    {"file": "01_happy_path_northwind.pdf", "title": "Clean invoice, PO matches", "tag": "Happy path",
     "vendor": "Northwind Office Supplies", "expect": "approved", "why": "Everything lines up with PO-1001."},
    {"file": "02_split_po_brightline_part2.pdf", "title": "Instalment 2 of 3 on one PO", "tag": "Split PO",
     "vendor": "Brightline Logistics", "expect": "approved", "why": "Fits inside what is left on PO-1002."},
    {"file": "03_split_po_brightline_part3.pdf", "title": "Instalment 3 runs past the PO", "tag": "Split PO",
     "vendor": "Brightline Logistics", "expect": "hold", "why": "Run after instalment 2: bills 22 trips against 20 ordered."},
    {"file": "04_duplicate_apex.pdf", "title": "Paid invoice resent as AFS/0042", "tag": "Duplicate",
     "vendor": "Apex Facility Services", "expect": "rejected", "why": "AFS-42 was already paid; the new number is a format change."},
    {"file": "05_scanned_no_po_kestrel.pdf", "title": "Scanned copy with no PO number", "tag": "Scanned, implied PO",
     "vendor": "Kestrel IT Solutions", "expect": "hold", "why": "PO must be inferred from line items; a person confirms it."},
    {"file": "06_price_variance_meridian.pdf", "title": "Total fits, one unit price raised", "tag": "Price variance",
     "vendor": "Meridian Print Co.", "expect": "hold", "why": "Standees billed 10% over PO; the total hides it."},
    {"file": "07_bank_change_northwind.pdf", "title": "New bank account on the invoice", "tag": "Fraud signal",
     "vendor": "Northwind Office Supplies", "expect": "hold", "why": "Payment details differ from the vendor master."},
]

app = FastAPI(title="Ledgerline · Invoice Desk")
_id_lock = threading.Lock()


@app.on_event("startup")
def _startup():
    UPLOADS.mkdir(parents=True, exist_ok=True)
    db.init()


def _next_id():
    with _id_lock:
        n = len(db.list_runs()) + 1
        rid = f"RUN-{n:04d}"
        while db.get_run(rid):
            n += 1
            rid = f"RUN-{n:04d}"
        return rid


def _summary(r):
    f = r.get("fields") or {}
    res = r.get("result") or {}
    decision = res.get("decision")
    if r.get("review"):
        decision = "approved" if r["review"]["action"] == "approve" else "rejected"
    return {
        "id": r["id"], "created_at": r["created_at"], "filename": r["filename"], "sample": r.get("sample"),
        "status": r["status"], "engine": r.get("engine"), "duration_ms": r.get("duration_ms"),
        "vendor": (r.get("vendor") or {}).get("name") or f.get("vendor_name"),
        "invoice_number": f.get("invoice_number"), "total": f.get("total"), "invoice_date": f.get("invoice_date"),
        "po_id": (r.get("po_match") or {}).get("po_id"), "po_method": (r.get("po_match") or {}).get("method"),
        "decision": decision, "auto_decision": res.get("decision"), "queue": res.get("queue"),
        "reviewed": bool(r.get("review")), "summary": res.get("summary"), "error": r.get("error"),
    }


@app.get("/api/config")
def config():
    m = db.master()
    return {"claude_available": extract.claude_available(), "model": extract.MODEL, "samples": SAMPLES,
            "policy": m["policy"], "buyer": m["buyer"], "pacing_ms": pipeline.PACING_MS}


@app.get("/api/samples/{name}")
def sample_file(name: str):
    if name not in {s["file"] for s in SAMPLES}:
        raise HTTPException(404)
    return FileResponse(SAMPLES_DIR / name, media_type="application/pdf")


@app.post("/api/runs")
async def create_run(file: UploadFile | None = File(None), sample: str | None = Form(None), engine: str = Form("auto")):
    if engine not in ("auto", "claude", "local"):
        raise HTTPException(400, "engine must be auto, claude or local")
    rid = _next_id()
    dest = UPLOADS / f"{rid}.pdf"
    if sample:
        if sample not in {s["file"] for s in SAMPLES}:
            raise HTTPException(404, "Unknown sample")
        shutil.copy(SAMPLES_DIR / sample, dest)
        filename = sample
    elif file:
        data = await file.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, "That file is not a PDF. Upload the invoice as a PDF.")
        if len(data) > 15 * 1024 * 1024:
            raise HTTPException(400, "PDF is over 15 MB.")
        dest.write_bytes(data)
        filename = file.filename or "upload.pdf"
    else:
        raise HTTPException(400, "Send a PDF file or pick a sample")
    pipeline.new_run(rid, filename, dest, sample, engine)
    threading.Thread(target=pipeline.execute, args=(rid,), daemon=True).start()
    return {"id": rid}


@app.get("/api/runs")
def runs():
    return [_summary(r) for r in db.list_runs()]


@app.get("/api/runs/{rid}")
def run(rid: str):
    r = db.get_run(rid)
    if not r:
        raise HTTPException(404)
    r.pop("traceback", None)
    return r


@app.get("/api/runs/{rid}/stream")
async def stream(rid: str):
    if not db.get_run(rid):
        raise HTTPException(404)

    async def gen():
        last = -1
        for _ in range(3000):  # ~7.5 minutes max
            r = db.get_run(rid)
            if r["version"] != last:
                last = r["version"]
                r.pop("traceback", None)
                yield f"data: {json.dumps(r)}\n\n"
            if r["status"] in ("done", "error"):
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(0.15)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{rid}/document")
def document(rid: str):
    r = db.get_run(rid)
    if not r or not Path(r["path"]).exists():
        raise HTTPException(404)
    return FileResponse(r["path"], media_type="application/pdf")


class Review(BaseModel):
    action: str
    note: str = ""
    reviewer: str = "AP reviewer"


@app.post("/api/runs/{rid}/review")
def review(rid: str, body: Review):
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    try:
        return pipeline.review(rid, body.action, body.note.strip(), body.reviewer.strip() or "AP reviewer")
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/master")
def master_data():
    m = db.master()
    led = db.ledger()
    vendors = {v["id"]: v for v in m["vendors"]}
    pos = []
    for po in m["purchase_orders"]:
        st = rules.po_state(po, led)
        pos.append({**po, "vendor_name": vendors[po["vendor_id"]]["name"], "ordered": st["ordered"],
                    "invoiced": st["invoiced"], "remaining": st["remaining"]})
    for e in led:
        e["vendor_name"] = vendors.get(e["vendor_id"], {}).get("name")
    return {"vendors": m["vendors"], "purchase_orders": pos, "ledger": led[::-1], "policy": m["policy"]}


@app.post("/api/reset")
def reset():
    db.init(reset=True)
    for p in UPLOADS.glob("*.pdf"):
        p.unlink()
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
