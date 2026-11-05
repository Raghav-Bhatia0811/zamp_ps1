"""Stage "Read invoice": turn the document into structured invoice fields.

Two engines share one output shape:
  * claude  - Claude reads the document and returns JSON constrained by a schema.
              Digital PDFs go in as PDFs; scans and photos go in as the pages
              cleaned up by vision.py (straightened, de-shadowed, upright).
  * local   - text layer (pdftotext) or the OCR already done on the cleaned pages,
              parsed with patterns. Region OCR from the layout detector is tried
              first for the fields it covers (totals, bank, meta, vendor).
              Offline fallback so the process still runs without an API key.

The extractor only *reads*. It never decides anything: deciding is the job of
the deterministic rules in rules.py, so every decision can be explained.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("CLAUDE_EFFORT", "low")

FIELDS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "vendor_name", "vendor_gstin", "invoice_number", "invoice_date", "due_date",
        "po_reference", "currency", "line_items", "subtotal", "tax_lines", "total",
        "bank_name", "bank_account", "bank_ifsc", "notes", "document_quality",
        "low_confidence_fields",
    ],
    "properties": {
        "vendor_name": {"type": ["string", "null"]},
        "vendor_gstin": {"type": ["string", "null"]},
        "invoice_number": {"type": ["string", "null"]},
        "invoice_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "po_reference": {"type": ["string", "null"], "description": "Only a PO number printed on the invoice"},
        "currency": {"type": ["string", "null"]},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["description", "quantity", "unit_price", "amount"],
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": ["number", "null"]},
                    "unit_price": {"type": ["number", "null"]},
                    "amount": {"type": ["number", "null"]},
                },
            },
        },
        "subtotal": {"type": ["number", "null"]},
        "tax_lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "rate", "amount"],
                "properties": {
                    "label": {"type": "string"},
                    "rate": {"type": ["number", "null"]},
                    "amount": {"type": ["number", "null"]},
                },
            },
        },
        "total": {"type": ["number", "null"]},
        "bank_name": {"type": ["string", "null"]},
        "bank_account": {"type": ["string", "null"]},
        "bank_ifsc": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
        "document_quality": {"type": "string", "enum": ["clean", "scanned", "poor"]},
        "low_confidence_fields": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = """You read vendor invoices for an accounts-payable team. Extract exactly what is printed on the document into the schema.

Rules:
- Copy values as printed. Do not calculate, correct or guess. If a field is not on the document, return null.
- Dates as YYYY-MM-DD. Indian invoices write numeric dates day-first (09-10-2026 is 9 October 2026).
- Amounts as plain numbers without currency symbols or thousands separators.
- po_reference: only a purchase order number that is actually printed. If the invoice only hints at an order (for example "as per your email of 12 Oct"), set po_reference to null and copy that hint into notes.
- tax_lines: one entry per tax row as printed (e.g. CGST and SGST are two rows).
- notes: anything an AP clerk should notice, such as payment instructions, changed bank details, "duplicate"/"copy" stamps, instalment wording, or handwritten marks.
- low_confidence_fields: names of fields you had to read from a blurry, cut-off or ambiguous area.
- vendor fields describe the seller who issued the invoice, not the buyer (the buyer is Halden Foods Pvt Ltd)."""


class ExtractionError(Exception):
    pass


# ---------------------------------------------------------------- intake

def inspect_pdf(path: Path) -> dict:
    reader = PdfReader(str(path))
    text = "".join((p.extract_text() or "") for p in reader.pages)
    has_text = len(text.strip()) > 40
    return {
        "pages": len(reader.pages),
        "has_text_layer": has_text,
        "kind": "Digital PDF with text layer" if has_text else "Scanned image (no text layer)",
        "size_kb": round(path.stat().st_size / 1024, 1),
    }


# ---------------------------------------------------------------- claude engine

def claude_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _png_b64(img, long_edge=2000) -> str:
    import cv2
    h, w = img.shape[:2]
    k = min(1.0, long_edge / max(h, w))
    if k < 1:
        img = cv2.resize(img, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", img)
    return base64.standard_b64encode(buf.tobytes()).decode()


def extract_with_claude(path: Path, pages=None) -> dict:
    """pages: cleaned page images (numpy) for scans/photos; None sends the PDF itself."""
    import anthropic

    client = anthropic.Anthropic(max_retries=2, timeout=120)
    if pages:
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _png_b64(p)}} for p in pages]
        content.append({"type": "text", "text": f"Extract this invoice ({len(pages)} page image{'s' if len(pages) > 1 else ''}, "
                                                "already straightened and cleaned from a scan or photo)."})
    else:
        data = base64.standard_b64encode(path.read_bytes()).decode()
        content = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
                   {"type": "text", "text": "Extract this invoice."}]
    messages = [{"role": "user", "content": content}]
    params = dict(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=messages,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": FIELDS_SCHEMA}},
    )
    try:
        # Server-side fallback: if the primary model declines, the API retries on a fallback model.
        resp = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params
        )
    except anthropic.BadRequestError:
        resp = client.messages.create(**params)

    if resp.stop_reason == "refusal":
        raise ExtractionError("Claude declined to read this document")
    if resp.stop_reason == "max_tokens":
        raise ExtractionError("Extraction output was cut off")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        fields = json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"Claude returned invalid JSON: {e}") from e
    usage = getattr(resp, "usage", None)
    fields["_meta"] = {
        "engine": "claude",
        "model": getattr(resp, "model", MODEL),
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }
    return fields


# ---------------------------------------------------------------- local engine

NUM = r"(\d{1,3}(?:,\d{2,3})*(?:\.\d{2})|\d+\.\d{2})"
GSTIN_RE = re.compile(r"\b[0-9O]{2}[A-Z]{5}[0-9O]{4}[A-Z][0-9A-Z]{1,2}[Z2][0-9A-Z]\b")
BUYER_GSTIN = "27AAACH1234F1Z3"


def _num(s):
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def _acct(s):
    return s.replace(" ", "") if s else s


def parse_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().replace(",", "")
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _text_layer(path: Path) -> str:
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    return out.stdout


def _first(patterns, text, flags=re.I):
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m.group(1).strip()
    return None


def extract_locally(path: Path, has_text: bool, ocr_text: str | None = None, regions: dict | None = None) -> dict:
    """has_text: digital PDF. Otherwise ocr_text is the OCR of the cleaned pages (from vision.py),
    and regions maps layout class -> OCR text of that region read on its own."""
    ocr = not has_text
    text = _text_layer(path) if has_text else (ocr_text or "")
    regions = regions or {}
    low = []
    if ocr:  # OCR edge noise: leading bars/underscores, "__" between columns
        text = "\n".join(re.sub(r"\s_{2,}\s?", "   ", re.sub(r"^[\s|_!:;\]\[]+", "", ln)).rstrip(" |") for ln in text.splitlines())
    lines = [ln for ln in text.splitlines() if ln.strip()]

    # vendor: the first meaningful line of the document
    vendor_name = None
    for ln in lines[:4]:
        cand = re.split(r"\s{2,}|TAX INVOICE|Invoice\b", ln.strip())[0].strip(" :")
        if len(cand) > 3:
            vendor_name = cand
            break

    # OCR often reads the leading state-code digit 0 as the letter O
    gstins = [g[:2].replace("O", "0") + g[2:] for g in GSTIN_RE.findall(text)]
    vendor_gstin = next((g for g in gstins if g != BUYER_GSTIN), None)

    inv_no = _first([
        r"(?:Invoice[ \t]*(?:No\.?|Number|#)|Bill[ \t]*No\.?)[ \t]*[:\-]?[ \t]*([A-Z0-9][A-Z0-9/\-]*(?: \d+)?)",
        r"\bNo[:.][ \t]*([A-Z]{2,}[A-Z0-9/\-]*(?: \d+)?)",
    ], text)
    if inv_no:
        inv_no = inv_no.replace(" ", "")
    raw_date = _first([
        r"(?:Invoice\s*Date|Issued|Dated)\s*[:\-]?\s*(\d{1,2}[\s\-/.][A-Za-z]{3,9}[\s\-/.]\d{4}|\d{1,2}[\-/.]\d{1,2}[\-/.]\d{4}|\d{4}-\d{2}-\d{2})",
    ], text)
    raw_due = _first([
        r"(?:Due\s*Date|Payment\s*due|Due\s*by)\s*[:\-]?\s*(\d{1,2}[\s\-/.][A-Za-z]{3,9}[\s\-/.]\d{4}|\d{1,2}[\-/.]\d{1,2}[\-/.]\d{4})",
    ], text)
    invoice_date = parse_date(raw_date)
    if raw_date and not invoice_date:
        low.append("invoice_date")
    po = _first([r"\b(PO[-\s]?\d{4})\b"], text)
    if po:
        po = "PO-" + re.sub(r"\D", "", po)

    items = _rows(lines)

    subtotal = _num(_first([r"Sub\s*-?\s*Total\s*" + NUM], text))
    tax_lines = []
    for m in re.finditer(r"((?:IGST|CGST|SGST|GST|VAT))\s*@\s*(\d+(?:\.\d+)?)\s*%\s*" + NUM, text, re.I):
        tax_lines.append({"label": f"{m.group(1).upper()} @ {m.group(2)}%", "rate": float(m.group(2)), "amount": _num(m.group(3))})
    total = _num(_first([
        r"(?:Grand[ \t]*Total|Total[ \t]*Amount[ \t]*Due|(?<!Sub )(?<!Sub)\bTotal)[ \t]*(?:\(INR\))?[ \t]*(?:INR|Rs\.?)?[ \t]*" + NUM + r"\s*$",
    ], text, re.I | re.M))
    if total is None:
        m = re.search(r"Total Amount Due\s*" + NUM, text)
        total = _num(m.group(1)) if m else None

    notes = []
    memo = _first([r"(Ref:(?![ \t]*P\.?O)[ \t]*[^\n]+?)(?:\s{2,}|$)"], text, re.I | re.M)
    if memo:
        notes.append(memo)
    period = _first([r"Billing period\s+([^\n]+?)(?:\s{2,}|$)"], text, re.I | re.M)
    if period and period != "-":
        notes.append(f"Billing period: {period}")
    chg = _first([r"((?:Please note|Kindly note)[^\n]+)"], text)
    if chg:
        notes.append(chg.strip())

    fields = {
        "vendor_name": vendor_name,
        "vendor_gstin": vendor_gstin,
        "invoice_number": inv_no,
        "invoice_date": invoice_date,
        "due_date": parse_date(raw_due),
        "po_reference": po,
        "currency": "INR" if re.search(r"INR|Rs\.?|₹", text) else None,
        "line_items": items,
        "subtotal": subtotal,
        "tax_lines": tax_lines,
        "total": total,
        "bank_name": _first([r"Bank\s*:\s*([^\n]+?)(?:\s{2,}|$)"], text, re.I | re.M),
        "bank_account": _acct(_first([r"A/?c(?:count)?\s*(?:No\.?|Number)?\s*[:\-]?\s*(\d(?:[\dOIl]| (?=\d)){7,21})"], text)),
        "bank_ifsc": _first([r"IFSC\s*(?:Code)?\s*[:\-]?\s*([A-Z]{4}0[A-Z0-9]{6})"], text),
        "notes": notes,
        "document_quality": "scanned" if ocr else "clean",
        "low_confidence_fields": sorted(set(low)),
        "_meta": {"engine": "local", "model": "tesseract-ocr" if ocr else "pdftotext"},
    }
    if ocr and regions:
        _apply_regions(fields, regions)
    return fields


ROW = re.compile(
    # OCR leaves stray marks around the serial number and before the description ("1.‘", "_Point-of-sale")
    r"^\s*(?:\d{1,2}(?:[^\w\s]|_){0,3}\s+)?(?:[^\w\s]|_){0,3}\s*(?P<desc>[A-Za-z][^\n]*?)\s+(?:\d{4,8}\s+)?"
    r"(?P<qty>\d[\d,]*)\s+(?P<rate>" + NUM + r")\s+(?P<amt>" + NUM + r")\s*$"
)


def _rows(lines) -> list[dict]:
    items = []
    for ln in lines:
        m = ROW.match(ln)
        if m and not re.search(r"total|cgst|sgst|igst", ln, re.I):
            items.append({
                "description": re.sub(r"\s{2,}", " ", m.group("desc").strip()),
                "quantity": _num(m.group("qty")),
                "unit_price": _num(m.group("rate")),
                "amount": _num(m.group("amt")),
            })
    return items


def _header(text: str) -> dict:
    inv_no = _first([
        r"(?:Invoice[ \t]*(?:No\.?|Number|#)|Bill[ \t]*No\.?)[ \t]*[:\-]?[ \t]*([A-Z0-9][A-Z0-9/\-]*(?: \d+)?)",
        r"\bNo[:.][ \t]*([A-Z]{2,}[A-Z0-9/\-]*(?: \d+)?)",
    ], text)
    date_pat = r"(\d{1,2}[\s\-/.][A-Za-z]{3,9}[\s\-/.]\d{4}|\d{1,2}[\-/.]\d{1,2}[\-/.]\d{4}|\d{4}-\d{2}-\d{2})"
    return {
        "invoice_number": inv_no.replace(" ", "") if inv_no else None,
        "invoice_date": parse_date(_first([r"(?:Invoice\s*Date|Issued|Dated)\s*[:\-]?\s*" + date_pat], text)),
        "due_date": parse_date(_first([r"(?:Due\s*Date|Payment\s*due|Due\s*by)\s*[:\-]?\s*" + date_pat], text)),
    }


def _apply_regions(f: dict, regions: dict):
    """Re-parse fields from region-level OCR; a region read on its own beats the full-page read."""
    used = []
    t = regions.get("totals")
    if t:
        sub = _num(_first([r"(?:Sub\s*-?\s*Total|Taxable\s*Value)[^\d\n]*" + NUM], t))
        tot = _num(_first([r"(?:Grand[ \t]*Total|Total[ \t]*Amount[ \t]*Due|Amount[ \t]*Payable|(?<!Sub )(?<!Sub)\bTotal)[^\d\n]*" + NUM + r"\s*$"], t, re.I | re.M))
        taxes = [{"label": f"{m.group(1).upper()} @ {m.group(2)}%", "rate": float(m.group(2)), "amount": _num(m.group(3))}
                 for m in re.finditer(r"((?:IGST|CGST|SGST|GST))\s*@\s*(\d+(?:\.\d+)?)\s*%[^\d\n]*" + NUM, t, re.I)]
        if sub is not None:
            f["subtotal"] = sub
        if tot is not None:
            f["total"] = tot
        if taxes and len(taxes) >= len(f.get("tax_lines") or []):
            f["tax_lines"] = taxes
        used.append("totals")
    bk = regions.get("bank_details")
    if bk:
        acct = _acct(_first([r"A/?c(?:count)?\s*(?:No\.?|Number)?\s*[:\-]?\s*(\d(?:[\dOIl]| (?=\d)){7,21})"], bk))
        ifsc = _first([r"IFSC\s*(?:Code)?\s*[:\-]?\s*([A-Z0-9]{4}[0O][A-Z0-9]{6})"], bk)
        bank = _first([r"Bank\s*:\s*([^\n]+?)(?:\s{2,}|$)"], bk, re.I | re.M)
        if acct:
            f["bank_account"] = acct
        if ifsc:
            f["bank_ifsc"] = ifsc
        if bank:
            f["bank_name"] = bank
        used.append("bank")
    li = regions.get("line_items")
    if li:
        rows = _rows([re.sub(r"^[\s|_!:;\]\[]+", "", ln) for ln in li.splitlines() if ln.strip()])
        if len(rows) > len(f.get("line_items") or []):
            f["line_items"] = rows
            used.append("line items")
    mt = regions.get("invoice_meta")
    if mt:
        for k, v in _header(mt).items():
            if v and (not f.get(k) or k == "invoice_number"):
                f[k] = v
        po = _first([r"\b(PO[-\s]?\d{4})\b"], mt)
        if po and not f.get("po_reference"):
            f["po_reference"] = "PO-" + re.sub(r"\D", "", po)
        used.append("invoice details")
    vb = regions.get("vendor_block")
    if vb:
        g = [x[:2].replace("O", "0") + x[2:] for x in GSTIN_RE.findall(vb.upper())]
        g = [x for x in g if x != BUYER_GSTIN]
        if g:
            f["vendor_gstin"] = g[0]
        used.append("vendor")
    if used:
        f["_meta"]["regions_used"] = used


def extract(path: Path, has_text: bool, engine: str = "auto", pages=None, ocr_text=None, regions=None) -> tuple[dict, list[str]]:
    """Returns (fields, log lines). Falls back to the local engine if Claude is unavailable or fails.

    pages / ocr_text / regions come from the vision stages for scans and photos."""
    log = []
    use_claude = engine == "claude" or (engine == "auto" and claude_available())
    if use_claude:
        try:
            fields = extract_with_claude(path, None if has_text else pages)
            log.append(f"Read by {fields['_meta']['model']} ("
                       + ("PDF document" if has_text else f"{len(pages or [])} cleaned page image(s)") + ", structured JSON output)")
            return fields, log
        except Exception as e:  # any API/parsing failure -> degrade, don't die mid-demo
            log.append(f"Claude extraction failed ({type(e).__name__}: {str(e)[:140]}). Falling back to local OCR.")
    elif engine == "auto":
        log.append("No Anthropic API key configured, so using the local OCR engine.")
    fields = extract_locally(path, has_text, ocr_text, regions)
    if has_text:
        log.append("Read by local engine: PDF text layer with layout parsing")
    else:
        log.append("Read by local engine: Tesseract OCR on the cleaned page"
                   + (f", with {', '.join(fields['_meta']['regions_used'])} re-read region by region" if fields["_meta"].get("regions_used") else ""))
    return fields, log
