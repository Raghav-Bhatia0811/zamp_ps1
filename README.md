# Ledgerline · Invoice Desk

**PS-1 · Invoice processing, from PDF to decision.**
Upload a vendor invoice PDF. Ledgerline reads it, finds the vendor and purchase order, runs twelve named checks and returns **Approved**, **On hold** or **Rejected**, with every step visible and every reason written down.

> Core design choice: **the AI reads, the rules decide.** Claude turns a messy PDF (digital or scanned) into structured fields. A deterministic rules engine makes the payment decision. That makes every decision repeatable and auditable, and the model never approves a payment on its own.

---

## Run it

```bash
cd invoice-desk
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # optional: without it the app uses local OCR
.venv/bin/uvicorn app.main:app --port 8000
# open http://localhost:8000
```

System packages for the offline reader: `poppler-utils` and `tesseract-ocr` (`sudo apt install poppler-utils tesseract-ocr`).

| Env var | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Turns on Claude as the reader |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Model used for extraction |
| `CLAUDE_EFFORT` | `low` | Reasoning effort (`low`/`medium`/`high`). Extraction is a reading task; low keeps demos fast |
| `DEMO_PACING_MS` | `450` | Minimum time each stage stays visible in the live view. `0` disables it |

| `STATE_DIR` | `data/` (`/state` in Docker) | Where `app.db` and uploaded PDFs are kept |

## Deploy on your own server (Docker)

```bash
git clone <this repo> ledgerline && cd ledgerline
cp .env.example .env          # add ANTHROPIC_API_KEY, or leave it empty for offline OCR
docker compose up -d --build  # serves on http://127.0.0.1:8000 (see PORT/BIND in .env)
docker compose logs -f        # watch it start
```

Runs, the ledger and uploaded PDFs live in the `ledgerline-state` Docker volume, so they survive restarts and rebuilds. To update after a `git pull`, run `docker compose up -d --build` again.

To put it behind a domain with HTTPS, point a reverse proxy at port 8000. Turn off response buffering so the live run view (Server-Sent Events) streams. Nginx example:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;          # needed for the live stage updates
    proxy_read_timeout 300s;
    client_max_body_size 20m;     # invoice uploads
}
```

> **The app has no login.** Anyone with the URL can upload invoices and use your API key. For a demo, share the link only with reviewers, or add basic auth at the proxy (`auth_basic` in Nginx).

### Public link with Tailscale Funnel

```bash
sudo tailscale funnel status          # see what is already published
sudo tailscale funnel --bg 8000       # https://<machine>.<tailnet>.ts.net -> localhost:8000
# if 443 is already used by another app: sudo tailscale funnel --bg --https=8443 8000
```

Funnel only publishes on ports 443, 8443 and 10000, and the app must sit at the root of its URL (no subpath). Never run `tailscale funnel reset`: it removes every app published from that machine.

Without Docker: install `poppler-utils` and `tesseract-ocr`, then run the commands in *Run it* with `--host 0.0.0.0`.

Hosted platforms work too: on Render choose *New → Web Service → from repo → Docker* and add `ANTHROPIC_API_KEY`. Railway and Fly.io work the same way.

A full system guide with diagrams and accuracy results is in [`docs/system-guide.html`](docs/system-guide.html). Measure accuracy yourself with `.venv/bin/python scripts/evaluate.py --engine local` (or `claude`). This resets the demo database.

Regenerate the sample invoices any time with `.venv/bin/python scripts/make_samples.py`.

---

## The pipeline

| # | Stage | What happens | Tooling |
|---|---|---|---|
| 1 | Receive document | Detect text-layer vs scanned, page count, SHA-256 fingerprint | pypdf |
| 2 | Read invoice | Extract vendor, GSTIN, invoice no., dates, PO ref, line items, tax rows, total, bank details, notes, plus a list of low-confidence fields | **Claude** (PDF in, schema-constrained JSON out). Fallback: Tesseract OCR + layout parsing |
| 3 | Identify vendor | Match GSTIN exactly, else fuzzy name match against the vendor master | rules |
| 4 | Validate invoice | Required fields, vendor approved, GSTIN, arithmetic, GST rate, **bank details**, date sanity | rules |
| 5 | Match purchase order | Use the printed PO, or **infer** one from the vendor's open POs by line items and amount | rules |
| 6 | Run controls | Duplicates (exact, reformatted number, same file), unit price vs PO, cumulative quantity and **PO balance across invoices** | rules + ledger |
| 7 | Decide and route | Reject > Hold > Approve. Route to a queue, list next steps, draft the vendor email, post approved invoices to the ledger | rules |

Each run is stored in full (stages, timings, raw extraction, every check), so the **Runs** dashboard can replay any decision.

### Decision logic

- **Rejected**: a check that must never be paid fails (duplicate invoice, vendor not onboarded or suspended).
- **On hold**: anything a person must look at (price variance, PO overrun, changed bank details, inferred PO, missing fields, bad arithmetic, wrong GST).
- **Approved**: every check passed. The invoice is posted to the ledger and the PO balance goes down.

A reviewer can approve or reject a held invoice from the decision panel. Approval needs a note, and that note goes into the audit trail.

### Policy (in `data/master.json`)

Unit price tolerance ±2% per line · PO overrun 0% · same-vendor/same-amount duplicate window 7 days · inferred PO suggested at ≥75% confidence, never auto-approved · bank details must equal the vendor master.

---

## Demo scenarios (in `samples/`)

| # | Scenario | Expected | Why it's not trivial |
|---|---|---|---|
| 1 | Clean Northwind invoice | **Approved** | Happy path: 12/12 checks |
| 2 | Brightline instalment 2 of 3 | **Approved** | Same amount as instalment 1. A naive duplicate rule would block it |
| 3 | Brightline instalment 3 | **On hold** | Fine on its own, but with earlier invoices it bills 22 trips against 20 ordered. **Stateful**: run it after #2 |
| 4 | Apex "AFS/0042" | **Rejected** | Already paid as "AFS-42". Different string, same invoice. The rule normalises numbers before comparing |
| 5 | Kestrel scanned copy, no PO | **On hold** | Image-only PDF. PO is only *implied* ("your email order"). PO-1004 is inferred at 90% and a person confirms it |
| 6 | Meridian price change | **On hold** | Total is within 1% of the PO, but one unit price is +10%. A total-only check would pay it |
| 7 | Northwind new bank account | **On hold** | Everything matches except the payee account, the classic payment-diversion fraud. Routed to treasury with "call the number on file" |

Bonus behaviours worth showing live: upload the same PDF twice (rejected as the same file); upload any random PDF or an unknown vendor (rejected, sent to vendor onboarding); **Reset demo** restores the ledger and PO balances.

### 5-minute video script

1. **(0:00)** One sentence on the problem, then the architecture line: *AI reads, rules decide.*
2. **(0:30)** Run #1. Narrate the stages lighting up, open *Read invoice* to show the extracted fields, finish on **Approved**.
3. **(1:30)** Run #2 then #3. Open *Run controls* and point at the PO balance bar going over. That's the stateful edge case.
4. **(2:45)** Run #6. "The total looks fine. Line level, it isn't." Show the vendor email draft.
5. **(3:30)** Run #7. Bank change, treasury queue. Approve a held one with a note to show the audit trail.
6. **(4:15)** Runs dashboard: straight-through rate, exceptions caught, replay a run. Close with what's next.

---

## Assumptions (note these for the interview)

- Buyer is a single Indian company (Halden Foods, Pune), amounts in INR, GST invoices. CGST+SGST and IGST are both handled.
- PO balances are tracked on pre-tax subtotal. Tax is validated separately against the vendor's GST rate.
- The ledger seeds two earlier invoices "imported from ERP" so duplicate and split-PO checks have history.
- Email intake is simulated by upload. The same pipeline would sit behind a mailbox webhook.
- No goods-receipt (3-way) match yet: PO-vs-invoice only.

## What I'd build next

3-way match with goods receipts · email inbox intake · per-vendor learning of layouts · approval limits by amount · ERP connector (Tally/SAP) for posting · confidence-based sampling for human QA.
