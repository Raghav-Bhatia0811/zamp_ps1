# Ledgerline · Invoice Desk

**PS-1 · Invoice processing, from PDF (or phone photo) to decision.**
Upload a vendor invoice: a digital PDF, a scan, or a photo taken on a phone. Ledgerline cleans the image, finds its regions, reads it, proves every field it can, finds the vendor and purchase order, runs sixteen named checks and returns **Approved**, **On hold** or **Rejected**. Every step is visible and every reason is written down, with the policy clause behind it.

> Core design choice: **the AI reads, the rules decide, and nothing is paid on a value that wasn't read clearly.**
> Computer vision makes a messy page readable. Claude (or local OCR) turns it into fields. A verification layer proves those fields with checksums, arithmetic and master data. A deterministic rules engine makes the payment decision. Retrieval over the AP policy and past reviewer decisions explains it.

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
On first start the embedding model for retrieval (BAAI/bge-small-en-v1.5, ~65 MB, ONNX) is downloaded into `models/embed/`. Without network access the app keeps working on keyword search alone.

| Env var | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Turns on Claude as the reader and for *Ask about this invoice* |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Model used for extraction and answers |
| `CLAUDE_EFFORT` | `low` | Reasoning effort for extraction (`low`/`medium`/`high`) |
| `DEMO_PACING_MS` | `450` | Minimum time each stage stays visible in the live view. `0` disables it |
| `STATE_DIR` | `data/` (`/state` in Docker) | Where `app.db`, uploads and page images are kept |
| `VISION_ENHANCE` / `REGION_OCR` | `1` | Set to `0` to switch off image clean-up / region re-reads (used for the ablation) |
| `DENSE_RETRIEVAL` | `1` | Set to `0` for keyword-only retrieval |

## Deploy on your own server (Docker)

```bash
git clone <this repo> ledgerline && cd ledgerline
cp .env.example .env          # add ANTHROPIC_API_KEY, or leave it empty for offline OCR
docker compose up -d --build  # serves on http://127.0.0.1:8000 (see PORT/BIND in .env)
docker compose logs -f
```

The image bakes in the layout model and the embedding model, so it runs with no network access. Runs, the ledger, page images and uploads live in the `ledgerline-state` Docker volume. To put it behind a domain, point a reverse proxy at port 8000 with response buffering off (the live view uses Server-Sent Events):

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

> **The app has no login.** Anyone with the URL can upload invoices and use your API key. Share the link only with reviewers, or add basic auth at the proxy.

Public link with Tailscale Funnel: `sudo tailscale funnel --bg 8000` (or `--https=8443 8000` if 443 is taken). Never run `tailscale funnel reset`: it unpublishes every app on the machine.

---

## Architecture

```
                 ┌──────────── document intelligence ────────────┐   ┌──────── decision ────────┐
 PDF / scan ──▶  Clean up ──▶ Find regions ──▶ Read ──▶ Verify  ──▶  Vendor ▶ Validate ▶ PO ▶ Controls ▶ Decide
 / phone photo   (OpenCV)     (YOLO, ONNX)    (Claude    (checksum,    (deterministic rules, 16 checks)    │
                                │              or OCR)   arithmetic,                                        ▼
                                └─ stamps, signatures,    master data)            policy citations + similar past
                                   handwriting ─────────────────────────▶ R14      decisions (hybrid retrieval)
```

| # | Stage | What happens | Tooling |
|---|---|---|---|
| 1 | Receive document | PDF or image (JPG/PNG/TIFF/WebP, EXIF rotation honoured), text layer vs scan vs photo, SHA-256 fingerprint | pypdf, Pillow |
| 2 | **Clean up image** | Find the sheet in a photo and flatten it (4-point warp), turn sideways/upside-down pages upright, invert white-on-dark letterhead bands, divide out shadows, denoise, deskew, upscale to ~300 dpi. Then **measure legibility** by OCR confidence on the cleaned page | OpenCV, Tesseract OSD |
| 3 | **Find regions** | A YOLO detector finds vendor block, invoice details, bill-to, line table, totals, bank block, **stamps, signatures, handwriting**. Key regions are re-read on their own at higher scale. Stamps are colour-isolated, squared up and read ("PAID 14 OCT 2026") | YOLO11n → ONNX Runtime (CPU) |
| 4 | Read invoice | Vendor, GSTIN, invoice no., dates, PO ref, line items, tax rows, total, bank details, notes | **Claude** (cleaned page images or PDF in, schema-constrained JSON out). Fallback: OCR + region-aware parsing |
| 5 | **Verify fields** | Prove each field: GSTIN check digit, IFSC format and bank code, qty × rate = amount, lines = subtotal, subtotal + tax = total, vendor master. Repair known OCR slips (O→0, I→1, 6→8) only when the fix is proven. When Claude read an image, a second independent OCR read is compared (dual read) | rules |
| 6 | Identify vendor | GSTIN exact, else fuzzy name against the vendor master | rules |
| 7 | Validate invoice | Required fields, vendor approved, GSTIN, arithmetic, GST rate, bank details, date sanity, signature | rules |
| 8 | Match purchase order | Printed PO, or **infer** one from open POs. Lines matched by text **or by meaning** (embeddings), with a model-number guard so "HP 12A" never matches "HP 88A" | rules + embeddings |
| 9 | Run controls | Duplicates, unit price vs PO, cumulative PO balance, **payment stamps and handwritten changes** | rules + ledger + layout |
| 10 | Decide and route | Reject > Hold > Approve. Queue, next steps, vendor email draft, **policy clause per exception, similar past decisions**, post to ledger | rules + retrieval |

If the page is **not legible** even after clean-up, stages 7–9 are skipped: nothing is checked, let alone paid, on values that could not be read. The invoice is held and the vendor is asked for a clearer copy.

### The checks

| ID | Check | Fails as | Policy |
|---|---|---|---|
| R00 | Document is legible (OCR confidence after clean-up) | Hold → Vendor follow-up | §2.2 |
| R01 | Required fields present | Hold | §4.1 |
| R02 | Vendor is approved | Reject → Procurement | §3.1 |
| R03 | GSTIN matches vendor master | Hold | §3.3 |
| R04 | Arithmetic adds up | Hold → Vendor follow-up | §4.2 |
| R05 | GST rate correct | Hold | §4.3 |
| R06 | Bank details match vendor master | Hold → Treasury | §6.2 |
| R07 | Invoice date plausible | Hold | §4.4 |
| R08 | Critical fields read reliably: total, bank account, IFSC, invoice no., GSTIN and line items; any amount corrected by the system | Hold → AP review | §4.5 |
| R09 | Purchase order found (inferred POs always held) | Hold | §5.2 |
| R10 | Not a duplicate (normalised number, same file, same amount) | Reject | §6.1 |
| R11 | Every line is on the PO (by text or meaning) | Hold | §5.3 |
| R12 | Unit prices within ±2% of PO | Hold | §5.4 |
| R13 | Within remaining PO balance (across split invoices) | Hold | §5.5 |
| R14 | No PAID/CANCELLED stamp, no handwriting on amounts or bank | Hold | §2.4 stamps, §2.3 handwriting |
| R15 | Signed (paper invoices) | Warning only | §2.5 |

The policy text lives in [`knowledge/ap-policy.md`](knowledge/ap-policy.md) and [`knowledge/gst-notes.md`](knowledge/gst-notes.md). Each check is mapped to its clause by an explicit table, not by retrieval, so a citation is never wrong.

---

## Messy documents: how each part earns its place

| Problem on real invoices | What handles it | Why this and not something simpler |
|---|---|---|
| Phone photo at an angle, on a desk, with a shadow | Page detection by modelling the background from the image border, 4-point warp, background division | "Find the white paper" cut off a dark navy letterhead (vendor name and GSTIN) in testing. Modelling the *desk* keeps it |
| Scanned sideways or upside down | Tesseract OSD, rotate before anything else | OCR on an upside-down page returns garbage with high confidence on some words |
| Faint fax, blur | Legibility measured as OCR word confidence × coverage after clean-up; below 50 → ask for a clearer copy | A blur metric alone can't tell "soft but readable" from "unreadable". Reading it *is* the test |
| PAID stamp, pen edits next to the total | YOLO layout detector with stamp / signature / handwriting classes, stamp OCR on the saturation channel with frame-based deskew | An LLM may silently read *through* a stamp. A detector makes the mark an explicit, auditable event |
| OCR slips (O↔0, I↔1, 6↔8) | GSTIN mod-36 check digit, IFSC shape, arithmetic cross-footing, vendor master | An invoice is its own checksum. Repairs are only made when proven, and any repaired **amount or bank account still goes to a person** |
| Wording differs from the PO | Embedding similarity (bge-small, ONNX) with a model-number guard | Fuzzy strings fail on "Photocopy sheets" vs "copier paper"; embeddings alone would match HP 12A to HP 88A |
| "Why is this on hold, and what did we do last time?" | Hybrid retrieval (BM25 + dense, reciprocal rank fusion) over policy + past reviewer notes; Claude answers with citations | Keyword search finds "R06" and "IFSC"; embeddings find "supplier moved banks". Fusion gets both. Every reviewer note becomes a new precedent |

### The layout model

YOLO11n (2.6M parameters), fine-tuned from COCO weights on **1,600 synthetic invoice pages** drawn by [`scripts/synth_layout.py`](scripts/synth_layout.py): random layouts, fonts, vendors, line counts, stamps, signatures and handwriting, then damaged as clean / scan / fax / phone photo. Labels are exact because every region is recorded as it is drawn. Exported to ONNX (10 MB) and run with ONNX Runtime, so torch is only a training dependency.

```bash
python3 -m venv .venv-train && .venv-train/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv-train/bin/pip install -r requirements-train.txt
.venv-train/bin/python scripts/make_layout_dataset.py --n 1600
.venv-train/bin/python scripts/train_layout.py --epochs 12      # CPU; ~4 min/epoch on 20 cores
```

The shipped model stopped after 10 epochs, when validation mAP had stopped improving. Validation results are written to [`models/layout-yolo.json`](models/layout-yolo.json). Honest limit: the validation set is synthetic too. In production I would label 200–300 real invoices and fine-tune on them, sampling the pages where the detector was least confident (active learning). Ultralytics is AGPL-3.0: for a commercial deployment, buy their licence or swap in an Apache-2.0 detector. The ONNX interface stays the same.

---

## Accuracy

`.venv/bin/python scripts/evaluate.py --engine local --ablation` runs every sample through the full pipeline in a scratch database (your demo data is not touched) and compares each field and decision with the truth used to generate the documents.

| Test set (10 invoices each) | Field accuracy | Correct decision | Correct decision *and* reason | Avg time |
|---|---|---|---|---|
| Original samples (7 digital, 2 scans, 1 photo) | 99.0% | 10/10 | 10/10 | 2.4 s |
| Light scans (170 dpi) | 97.3% | 10/10 | 10/10 | 5.0 s |
| Heavy scans (120 dpi, skew, noise, blur) | 99.1% | 10/10 | 10/10 | 6.2 s |
| Phone photos (angled, shadowed, JPG) | 97.3% | 10/10 | 10/10 | 3.0 s |
| Heavy scans, **vision off** | 85.5% | 9/10 | 8/10 | 4.4 s |
| Phone photos, **vision off** | 41.8% | 5/10 | 3/10 | 1.2 s |

Local OCR engine, CPU only, layout model on. Field accuracy covers 11 fields per invoice (GSTIN, invoice no., both dates, PO, subtotal, tax, total, account, IFSC, all line items) and leaves out the deliberately illegible fax. The one field missed in the original set is sample 10's total: it is struck through by hand, so it is correctly left unread and the invoice is held under R14.

**What the ablation shows.** With clean-up and region re-reads switched off, phone photos fall from 97% to 42% field accuracy and half the decisions go wrong. Most failures are amounts and line items lost to perspective and shadow. On heavy scans the gap is smaller (99% vs 86%), mostly bank account numbers. Note that with vision off the system still *holds* bad reads instead of paying them: the verification layer catches what the reader got wrong. Vision is what turns those holds back into correct straight-through decisions.

Run with `--engine claude` to measure Claude as the reader on the same sets.

---

## Demo scenarios (in `samples/`)

| # | Scenario | Expected | Why it's not trivial |
|---|---|---|---|
| 1 | Clean Northwind invoice | **Approved** | Happy path: every check passes |
| 2 | Brightline instalment 2 of 3 | **Approved** | Same amount as instalment 1. A naive duplicate rule would block it |
| 3 | Brightline instalment 3 | **On hold** | Fine alone, but with earlier invoices it bills 22 trips against 20 ordered. **Stateful**: run after #2 |
| 4 | Apex "AFS/0042" | **Rejected** | Already paid as "AFS-42". Numbers are normalised before comparing |
| 5 | Kestrel scanned copy, no PO | **On hold** | Image-only PDF. PO only *implied*; PO-1004 inferred at 90% and a person confirms |
| 6 | Meridian price change | **On hold** | Total within 1% of the PO, but one unit price is +10% |
| 7 | Northwind new bank account | **On hold** | Payment-diversion fraud pattern. Treasury, call the number on file |
| 8 | **Phone photo** (JPG), worded unlike the PO | **Approved** | Angled, shadowed photo with a dark letterhead. CV flattens and cleans it; every field is proven; all three lines matched to PO-1008 *by meaning* ("Photocopy sheets…" → "A4 copier paper…") |
| 9 | **Fax too faint to read** | **On hold** | Legibility 21/100. Nothing is guessed: validation is skipped and the vendor is asked for a clear copy |
| 10 | **Upside-down scan, PAID stamp, pen edit** | **On hold** | Page turned upright automatically. Detector finds the stamp (read as "PAID 14 OCT 2026") and handwriting over the struck-through total |

Also worth showing live: upload the same file twice (rejected as the same file), upload your own phone photo of any printed invoice, and approve a held invoice with a note, then open **Knowledge** to see the note added as a precedent.

### 5-minute video script

1. **(0:00)** The problem in one line; the architecture line: *vision cleans, AI reads, every field is proven, rules decide.*
2. **(0:30)** Run #1. Stages light up; open *Verify fields* (every field proven). **Approved**.
3. **(1:15)** Run #8, the phone photo. Open *Clean up image*: before/after, legibility 91/100. Open *Find regions*. Point at "matched by meaning" in controls. **Approved** from a photo.
4. **(2:15)** Run #10. Page turned upright, PAID stamp detected and read, handwriting on the total. **On hold**, with policy §2.4 cited and similar past decisions.
5. **(3:00)** Run #9. Legibility 21: "it knows when it can't read". Downstream checks skipped, vendor email drafted.
6. **(3:30)** Run #7 bank change; ask "What must I check before approving?" and show the cited answer. Approve one held invoice with a note.
7. **(4:15)** Runs dashboard, then the accuracy table and ablation: what computer vision is worth on bad scans and photos. Close with what's next.

---

## Assumptions (note these for the interview)

- Buyer is a single Indian company (Halden Foods, Pune), amounts in INR, GST invoices. CGST+SGST and IGST both handled. GSTINs in the sample data carry valid check digits.
- PO balances are tracked on pre-tax subtotal. Tax is validated separately.
- The ledger seeds two earlier invoices and the knowledge base seeds 16 past reviewer decisions, "imported" from an earlier quarter, so duplicate, split-PO and precedent features have history.
- Email intake is simulated by upload. The same pipeline would sit behind a mailbox webhook.
- No goods-receipt (3-way) match yet: PO-vs-invoice only.
- Handwriting is detected, not read. Detection is enough for the policy (handwritten changes are not accepted).

## What I'd build next

Fine-tune the layout model on real labelled invoices (active learning from low-confidence pages) · 3-way match with goods receipts · email inbox intake · e-invoice IRN/QR verification · approval limits by amount · ERP connector (Tally/SAP) for posting · confidence-based sampling of approved invoices for human QA.
