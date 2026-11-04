"""SQLite storage: runs (full audit record per invoice) and the AP ledger (posted invoices)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("STATE_DIR", ROOT / "data"))  # app.db + uploads; mount a volume here
DB_PATH = STATE_DIR / "app.db"
MASTER_PATH = ROOT / "data" / "master.json"
_lock = threading.RLock()


def master() -> dict:
    return json.loads(MASTER_PATH.read_text())


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def init(reset: bool = False):
    with _lock, _conn() as c:
        if reset:
            c.execute("DROP TABLE IF EXISTS runs")
            c.execute("DROP TABLE IF EXISTS ledger")
        c.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created_at TEXT, data TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT, invoice_number TEXT, canon TEXT,
            po_id TEXT, subtotal REAL, total REAL, lines TEXT, invoice_date TEXT, status TEXT,
            source TEXT, run_id TEXT, posted_at TEXT)""")
        if not c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]:
            import re
            for e in master()["ledger_seed"]:
                c.execute("INSERT INTO ledger (vendor_id, invoice_number, canon, po_id, subtotal, total, lines, invoice_date, status, source, run_id, posted_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (e["vendor_id"], e["invoice_number"], re.sub(r"[^A-Z0-9]", "", e["invoice_number"].upper()), e["po_id"],
                           e["subtotal"], e["total"], json.dumps(e["lines"]), e["invoice_date"], e["status"], e["source"], None, now_iso()))


def save_run(run: dict):
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO runs (id, created_at, data) VALUES (?,?,?)",
                  (run["id"], run["created_at"], json.dumps(run)))


def get_run(run_id: str) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def list_runs() -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT data FROM runs ORDER BY created_at DESC").fetchall()
    return [json.loads(r["data"]) for r in rows]


def ledger() -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM ledger ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["lines"] = json.loads(d["lines"])
        out.append(d)
    return out


def post_to_ledger(run: dict, status: str = "approved"):
    f = run["fields"]
    po_id = run.get("po_match", {}).get("po_id")
    lines = [{"description": li["description"], "quantity": li.get("quantity") or 0, "unit_price": li.get("unit_price") or 0}
             for li in f.get("line_items") or []]
    with _lock, _conn() as c:
        c.execute("INSERT INTO ledger (vendor_id, invoice_number, canon, po_id, subtotal, total, lines, invoice_date, status, source, run_id, posted_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f.get("vendor_id"), f.get("invoice_number"), f.get("invoice_number_canonical"), po_id,
                   f.get("subtotal") or 0, f.get("total") or 0, json.dumps(lines), f.get("invoice_date"), status,
                   f"Run {run['id']}", run["id"], now_iso()))
