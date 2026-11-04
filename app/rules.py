"""Stages 3-6: normalise, validate, match to a PO, run controls, decide.

Everything in here is deterministic. Given the same extracted fields, master
data and ledger, it always reaches the same decision, and each decision is a
list of named checks a reviewer can read. The language model never decides.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from difflib import SequenceMatcher

PASS, WARN, FAIL = "pass", "warn", "fail"

# How each check reads when it fails (used in stage summaries and the verdict)
FAIL_SHORT = {
    "R01": "required fields missing", "R02": "vendor not approved", "R03": "GSTIN mismatch",
    "R04": "totals don't add up", "R05": "wrong GST rate", "R06": "bank details changed",
    "R07": "invoice date problem", "R08": "low-confidence read", "R09": "PO not confirmed",
    "R10": "duplicate invoice", "R11": "line not on the PO", "R12": "unit price above PO",
    "R13": "over the PO balance",
}


def short(c):
    return FAIL_SHORT.get(c["id"], c["label"].lower())
HOLD, REJECT, INFO = "hold", "reject", "info"


def check(cid, label, status, detail, severity=INFO, queue=None, action=None, vendor_ask=None):
    return {
        "id": cid, "label": label, "status": status, "detail": detail,
        "severity": severity if status == FAIL else INFO,
        "queue": queue, "action": action, "vendor_ask": vendor_ask,
    }


def money(x):
    if x is None:
        return "-"
    neg = x < 0
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail, groups = whole[:-3], whole[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:]); head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    s = f"₹{whole}" + (f".{frac}" if frac != "00" else "")
    return "−" + s if neg else s


def _clean_name(s):
    s = (s or "").lower()
    s = re.sub(r"\b(pvt|private|ltd|limited|llp|co|company|inc|the)\b\.?", " ", s)
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\s+", " ", s)).strip()


def similarity(a, b):
    a, b = _clean_name(a), _clean_name(b)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return round(max(ratio, 0.5 * ratio + 0.5 * overlap), 3)


def canon_number(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def numeric_core(s):
    groups = re.findall(r"\d+", s or "")
    return groups[-1].lstrip("0") if groups else ""


# ---------------------------------------------------------------- normalise

def normalise(fields: dict, master: dict) -> tuple[dict, dict | None, str]:
    n = {k: v for k, v in fields.items() if not k.startswith("_")}
    n["tax_total"] = round(sum((t.get("amount") or 0) for t in n.get("tax_lines") or []), 2)
    n["tax_rate"] = round(sum((t.get("rate") or 0) for t in n.get("tax_lines") or []), 2) or None
    n["invoice_number_canonical"] = canon_number(n.get("invoice_number"))
    gstin = (n.get("vendor_gstin") or "").upper().replace(" ", "")
    n["vendor_gstin"] = gstin or None

    vendor, how = None, "No match in vendor master"
    if gstin:
        vendor = next((v for v in master["vendors"] if v["gstin"] == gstin), None)
        if vendor:
            how = "Matched on GSTIN"
    if not vendor and n.get("vendor_name"):
        best, score = None, 0.0
        for v in master["vendors"]:
            s = max(similarity(n["vendor_name"], x) for x in [v["name"], *v.get("aliases", [])])
            if s > score:
                best, score = v, s
        if best and score >= 0.82:
            vendor, how = best, f"Matched on name (similarity {score:.2f})"
    n["vendor_id"] = vendor["id"] if vendor else None
    return n, vendor, how


# ---------------------------------------------------------------- validate

def validate(n: dict, vendor: dict | None, master: dict, today: date) -> list[dict]:
    pol = master["policy"]
    out = []

    missing = [f for f in pol["required_fields"] if n.get(f) in (None, "", [])]
    label = {"vendor_name": "vendor name", "invoice_number": "invoice number", "invoice_date": "invoice date", "total": "invoice total"}
    if missing:
        names = ", ".join(label.get(m, m) for m in missing)
        out.append(check("R01", "Required fields present", FAIL, f"Missing or unreadable: {names}.", HOLD,
                         queue="Vendor follow-up", action=f"Ask the vendor for a corrected invoice showing the {names}.",
                         vendor_ask=f"the {names}"))
    else:
        out.append(check("R01", "Required fields present", PASS, "Vendor, invoice number, date and total all present."))

    if not vendor:
        out.append(check("R02", "Vendor is approved", FAIL,
                         f"“{n.get('vendor_name') or 'Unknown'}” is not in the vendor master. Payments only go to onboarded vendors.",
                         REJECT, queue="Procurement", action="Route to vendor onboarding before any payment."))
    elif vendor["status"] != "approved":
        out.append(check("R02", "Vendor is approved", FAIL,
                         f"{vendor['name']} is {vendor['status']}: {vendor.get('status_reason', 'no reason recorded')}.",
                         REJECT, queue="Procurement", action="Vendor must renew KYC before invoices can be accepted."))
    else:
        out.append(check("R02", "Vendor is approved", PASS, f"{vendor['name']} ({vendor['id']}) is active."))

    if vendor and n.get("vendor_gstin"):
        if n["vendor_gstin"] == vendor["gstin"]:
            out.append(check("R03", "GSTIN matches vendor master", PASS, f"{n['vendor_gstin']} on file."))
        else:
            out.append(check("R03", "GSTIN matches vendor master", FAIL,
                             f"Invoice shows {n['vendor_gstin']}, vendor master has {vendor['gstin']}.", HOLD,
                             queue="AP review", action="Confirm the GSTIN with the vendor; input tax credit depends on it."))
    elif vendor:
        out.append(check("R03", "GSTIN matches vendor master", WARN, "No GSTIN printed on the invoice."))

    # arithmetic
    bad_lines = []
    for i, li in enumerate(n.get("line_items") or [], 1):
        q, r, a = li.get("quantity"), li.get("unit_price"), li.get("amount")
        if None not in (q, r, a) and abs(q * r - a) > 1:
            bad_lines.append(f"line {i}: {q:g} × {money(r)} ≠ {money(a)}")
    line_sum = round(sum((li.get("amount") or 0) for li in n.get("line_items") or []), 2)
    sub, tot = n.get("subtotal"), n.get("total")
    problems = list(bad_lines)
    if sub is not None and n.get("line_items") and abs(line_sum - sub) > 1:
        problems.append(f"lines add to {money(line_sum)} but subtotal says {money(sub)}")
    if sub is not None and tot is not None and abs(sub + n["tax_total"] - tot) > 1:
        problems.append(f"subtotal {money(sub)} + tax {money(n['tax_total'])} ≠ total {money(tot)}")
    if problems:
        out.append(check("R04", "Invoice arithmetic adds up", FAIL, "; ".join(problems).capitalize() + ".", HOLD,
                         queue="Vendor follow-up", action="Return to vendor for a corrected invoice.",
                         vendor_ask="a corrected invoice (the totals do not add up)"))
    else:
        out.append(check("R04", "Invoice arithmetic adds up", PASS,
                         f"Lines {money(line_sum)} + tax {money(n['tax_total'])} = {money(tot)}."))

    if vendor and n.get("tax_rate") is not None:
        if abs(n["tax_rate"] - vendor["gst_rate"]) < 0.01:
            out.append(check("R05", "GST rate is correct", PASS, f"{n['tax_rate']:g}% matches the {vendor['gst_rate']}% rate for this vendor."))
        else:
            out.append(check("R05", "GST rate is correct", FAIL,
                             f"Charged {n['tax_rate']:g}%, expected {vendor['gst_rate']}% for this vendor.", HOLD,
                             queue="AP review", action="Check the HSN/SAC classification with tax before paying."))

    if vendor and (n.get("bank_account") or n.get("bank_ifsc")):
        b = vendor["bank"]
        same = (n.get("bank_account") or b["account"]) == b["account"] and (n.get("bank_ifsc") or b["ifsc"]) == b["ifsc"]
        if same:
            out.append(check("R06", "Bank details match vendor master", PASS, f"{b['bank']} ••••{b['account'][-4:]} on file."))
        else:
            out.append(check("R06", "Bank details match vendor master", FAIL,
                             f"Invoice asks for payment to {n.get('bank_name') or 'a new bank'} ••••{(n.get('bank_account') or '????')[-4:]} "
                             f"(IFSC {n.get('bank_ifsc') or '-'}); vendor master has {b['bank']} ••••{b['account'][-4:]}. "
                             "Changed bank details on an invoice are the most common payment-diversion fraud.", HOLD,
                             queue="Treasury verification",
                             action=f"Do not pay. Call {vendor['contact']['name']} on the number on file ({vendor['contact']['phone']}), "
                                    "not any number on the invoice, and get the change confirmed in writing."))

    d = n.get("invoice_date")
    if d:
        try:
            dd = datetime.strptime(d, "%Y-%m-%d").date()
            age = (today - dd).days
            if age < -1:
                out.append(check("R07", "Invoice date is plausible", FAIL, f"Dated {d}, which is in the future.", HOLD,
                                 queue="Vendor follow-up", action="Ask the vendor to reissue with the correct date.",
                                 vendor_ask="the correct invoice date"))
            elif age > 180:
                out.append(check("R07", "Invoice date is plausible", WARN, f"Invoice is {age} days old; check it has not been paid already."))
            else:
                out.append(check("R07", "Invoice date is plausible", PASS, f"Dated {dd.strftime('%d %b %Y')} ({age} days ago)."))
        except ValueError:
            out.append(check("R07", "Invoice date is plausible", WARN, f"Could not parse date “{d}”."))

    if n.get("low_confidence_fields"):
        out.append(check("R08", "Extraction confidence", WARN,
                         "Read with low confidence: " + ", ".join(n["low_confidence_fields"]) + ". Cross-checked against master data below."))
    return out


# ---------------------------------------------------------------- PO matching

def po_state(po: dict, ledger: list[dict]) -> dict:
    """How much of a PO has already been invoiced (posted ledger entries)."""
    ordered = sum(l["quantity"] * l["unit_price"] for l in po["lines"])
    posted = [e for e in ledger if e["po_id"] == po["id"]]
    used = sum(e["subtotal"] for e in posted)
    qty_used = [0.0] * len(po["lines"])
    for e in posted:
        for el in e["lines"]:
            j = best_line(el["description"], po["lines"])[0]
            if j is not None:
                qty_used[j] += el["quantity"]
    return {"ordered": ordered, "invoiced": used, "remaining": ordered - used, "qty_used": qty_used, "postings": posted}


def best_line(desc, po_lines):
    best, score = None, 0.0
    for j, pl in enumerate(po_lines):
        s = similarity(desc, pl["description"])
        if s > score:
            best, score = j, s
    return (best, score) if score >= 0.6 else (None, score)


def match_po(n: dict, vendor: dict | None, master: dict, ledger: list[dict]) -> tuple[dict, list[dict]]:
    pol = master["policy"]
    pos = {p["id"]: p for p in master["purchase_orders"]}
    checks = []
    ref = (n.get("po_reference") or "").upper().replace(" ", "")
    ref = re.sub(r"^PO-?", "PO-", ref) if ref else None

    if ref:
        po = pos.get(ref)
        if not po:
            checks.append(check("R09", "Purchase order found", FAIL, f"{ref} is printed on the invoice but does not exist.", HOLD,
                                queue="Procurement", action="Confirm the correct PO number with the requester.",
                                vendor_ask="the correct purchase order number"))
            return {"method": "explicit", "po_id": None, "reference": ref}, checks
        if vendor and po["vendor_id"] != vendor["id"]:
            checks.append(check("R09", "Purchase order found", FAIL,
                                f"{ref} belongs to a different vendor ({po['vendor_id']}).", HOLD, queue="Procurement",
                                action="Confirm which PO this invoice is for."))
            return {"method": "explicit", "po_id": ref, "reference": ref, "confidence": 0}, checks
        checks.append(check("R09", "Purchase order found", PASS, f"{ref} printed on the invoice; same vendor, status {po['status']}."))
        return {"method": "explicit", "po_id": ref, "reference": ref, "confidence": 1.0}, checks

    # No PO printed: infer from this vendor's open POs
    if not vendor:
        checks.append(check("R09", "Purchase order found", FAIL, "No PO on the invoice and the vendor is unknown, so nothing to match.", HOLD,
                            queue="Procurement"))
        return {"method": "none", "po_id": None}, checks
    cands = []
    for po in master["purchase_orders"]:
        if po["vendor_id"] != vendor["id"] or po["status"] != "open":
            continue
        st = po_state(po, ledger)
        lines = n.get("line_items") or []
        desc = sum(best_line(li["description"], po["lines"])[1] for li in lines) / max(1, len(lines))
        sub = n.get("subtotal") or 0
        amount_fit = 1.0 if 0 < sub <= st["remaining"] + 1 else max(0.0, 1 - abs(sub - st["remaining"]) / max(sub, 1))
        # capped at 90%: without a printed PO number a match is never certain
        conf = round(0.6 * desc + 0.3 * amount_fit, 2)
        cands.append({"po_id": po["id"], "confidence": conf, "description_similarity": round(desc, 2),
                      "amount_fit": round(amount_fit, 2), "po_description": po["description"]})
    cands.sort(key=lambda c: -c["confidence"])
    if cands and cands[0]["confidence"] >= pol["inferred_po_min_confidence"]:
        top = cands[0]
        checks.append(check("R09", "Purchase order found", FAIL,
                            f"No PO printed. Best match is {top['po_id']} ({top['po_description']}) at {top['confidence']:.0%} confidence, "
                            f"from line items and amount. Inferred matches are never auto-approved.", HOLD,
                            queue="AP review", action=f"Confirm {top['po_id']} is the right PO, then approve."))
        return {"method": "inferred", "po_id": top["po_id"], "confidence": top["confidence"], "candidates": cands}, checks
    checks.append(check("R09", "Purchase order found", FAIL, "No PO printed and no open PO for this vendor fits the invoice.", HOLD,
                        queue="Procurement", action="Find the requester and raise or locate a PO.",
                        vendor_ask="the purchase order number this invoice relates to"))
    return {"method": "none", "po_id": None, "candidates": cands}, checks


# ---------------------------------------------------------------- controls

def controls(n: dict, vendor: dict | None, match: dict, master: dict, ledger: list[dict], file_hash: str,
             seen_hashes: dict) -> tuple[dict, list[dict]]:
    pol = master["policy"]
    out = []
    detail = {"line_comparison": [], "po": None}

    # duplicates
    dup = None
    if file_hash in seen_hashes:
        dup = ("exact", f"This exact file was already processed in run {seen_hashes[file_hash]}.")
    elif vendor:
        for e in ledger:
            if e["vendor_id"] != vendor["id"]:
                continue
            same_no = e["canon"] == n["invoice_number_canonical"]
            same_core = numeric_core(e["invoice_number"]) == numeric_core(n.get("invoice_number")) and numeric_core(e["invoice_number"])
            same_amt = n.get("total") is not None and abs(e["total"] - n["total"]) < 1
            if same_no:
                dup = ("exact", f"Invoice {e['invoice_number']} from this vendor was already {e['status']} ({e['source']}, {e['invoice_date']}).")
            elif same_core and same_amt:
                dup = ("near", f"“{n.get('invoice_number')}” is the same invoice as {e['invoice_number']} "
                               f"({e['status']} {e['invoice_date']}, {money(e['total'])}): same vendor, same number once "
                               "punctuation and leading zeros are stripped, same amount.")
            elif same_amt and n.get("invoice_date") and e.get("invoice_date"):
                gap = abs((datetime.strptime(n["invoice_date"], "%Y-%m-%d") - datetime.strptime(e["invoice_date"], "%Y-%m-%d")).days)
                if gap <= pol["duplicate_window_days"] and not dup:
                    dup = ("possible", f"Same vendor and same amount ({money(e['total'])}) as {e['invoice_number']}, {gap} days apart.")
            if dup and dup[0] in ("exact", "near"):
                break
    if dup and dup[0] in ("exact", "near"):
        out.append(check("R10", "Not a duplicate", FAIL, dup[1], REJECT, queue="AP review",
                         action="Reject as duplicate. No payment. Let the vendor know it is already settled."))
    elif dup:
        out.append(check("R10", "Not a duplicate", FAIL, dup[1], HOLD, queue="AP review",
                         action="Compare both invoices before paying."))
    else:
        out.append(check("R10", "Not a duplicate", PASS, "No earlier invoice with this number, file or amount pattern."))

    po_id = match.get("po_id")
    po = next((p for p in master["purchase_orders"] if p["id"] == po_id), None)
    if not po:
        return detail, out

    st = po_state(po, ledger)
    detail["po"] = {"id": po["id"], "description": po["description"], "ordered": st["ordered"],
                    "invoiced_before": st["invoiced"], "remaining_before": st["remaining"],
                    "this_invoice": n.get("subtotal"), "postings": [
                        {"invoice_number": e["invoice_number"], "subtotal": e["subtotal"], "status": e["status"]} for e in st["postings"]]}

    # line-level price and quantity
    tol = pol["line_price_tolerance_pct"]
    price_issues, qty_issues, unmatched = [], [], []
    for li in n.get("line_items") or []:
        j, score = best_line(li["description"], po["lines"])
        row = {"description": li["description"], "qty": li.get("quantity"), "unit_price": li.get("unit_price"),
               "amount": li.get("amount"), "po_line": None}
        if j is None:
            unmatched.append(li["description"])
            row["status"] = "not on PO"
        else:
            pl = po["lines"][j]
            var = ((li["unit_price"] or 0) - pl["unit_price"]) / pl["unit_price"] * 100 if pl["unit_price"] else 0
            billed = st["qty_used"][j] + (li.get("quantity") or 0)
            row.update(po_line=pl["description"], po_qty=pl["quantity"], po_unit_price=pl["unit_price"],
                       variance_pct=round(var, 2), qty_billed_before=st["qty_used"][j], qty_after=billed)
            row["status"] = "ok"
            if abs(var) > tol:
                row["status"] = "price"
                price_issues.append(f"“{li['description']}” billed at {money(li['unit_price'])} vs PO {money(pl['unit_price'])} ({var:+.1f}%)")
            if billed > pl["quantity"] + 1e-6:
                row["status"] = "quantity"
                qty_issues.append(f"“{li['description']}”: {billed:g} billed in total against {pl['quantity']:g} ordered "
                                  f"({st['qty_used'][j]:g} on earlier invoices + {li.get('quantity'):g} on this one)")
        detail["line_comparison"].append(row)

    if unmatched:
        out.append(check("R11", "Every line is on the PO", FAIL, "Not on the PO: " + "; ".join(unmatched) + ".", HOLD,
                         queue="Procurement", action="Ask the requester whether these items were ordered."))
    else:
        out.append(check("R11", "Every line is on the PO", PASS, f"All {len(n.get('line_items') or [])} lines map to {po['id']} lines."))

    if price_issues:
        sub = n.get("subtotal") or 0
        total_var = (sub - st["ordered"]) / st["ordered"] * 100 if st["ordered"] else 0
        hidden = abs(total_var) <= pol["total_tolerance_pct"]
        extra = (f" The invoice total is within {pol['total_tolerance_pct']:g}% of the PO ({total_var:+.2f}%), so a total-only check would have passed it."
                 if hidden else "")
        out.append(check("R12", f"Unit prices within {tol:g}% of PO", FAIL, "; ".join(price_issues) + "." + extra, HOLD,
                         queue="Procurement", action="Ask procurement whether the price change was agreed; if not, request a credit note.",
                         vendor_ask="a credit note or revised invoice at the agreed PO prices"))
    else:
        out.append(check("R12", f"Unit prices within {tol:g}% of PO", PASS, "Every unit price matches the PO."))

    remaining_after = st["remaining"] - (n.get("subtotal") or 0)
    allowed_over = st["ordered"] * pol["po_overrun_tolerance_pct"] / 100
    if qty_issues or remaining_after < -allowed_over - 1:
        msg = "; ".join(qty_issues) if qty_issues else ""
        bal = (f"PO {po['id']} is {money(st['ordered'])}; {money(st['invoiced'])} already invoiced, so only {money(max(st['remaining'], 0))} was left. "
               f"This invoice ({money(n.get('subtotal'))}) goes over by {money(-remaining_after)}.")
        out.append(check("R13", "Within remaining PO balance", FAIL, (bal + (" " + msg + "." if msg else "")), HOLD,
                         queue="Procurement",
                         action=f"Either raise a PO amendment for {money(-remaining_after)} or pay only the remaining {money(max(st['remaining'], 0))}.",
                         vendor_ask=None))
    else:
        out.append(check("R13", "Within remaining PO balance", PASS,
                         f"{money(st['invoiced'])} invoiced before, {money(n.get('subtotal'))} now, {money(remaining_after)} left on {po['id']}."))
    detail["po"]["remaining_after"] = remaining_after
    return detail, out


# ---------------------------------------------------------------- decide

def decide(checks: list[dict], n: dict, vendor: dict | None) -> dict:
    fails = [c for c in checks if c["status"] == FAIL]
    rejects = [c for c in fails if c["severity"] == REJECT]
    holds = [c for c in fails if c["severity"] == HOLD]
    if rejects:
        decision, label = "rejected", "Rejected"
        lead = rejects[0]
        summary = lead["detail"]
    elif holds:
        decision, label = "hold", "On hold"
        names = [short(c) for c in holds]
        summary = (("Needs a person: " if len(holds) == 1 else f"{len(holds)} exceptions need a person: ")
                   + ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] + ". "
                   + "Nothing is paid until a reviewer signs off.")
    else:
        decision, label = "approved", "Approved"
        summary = (f"Approved for payment: all {len(checks)} checks passed. {money(n.get('total'))} to "
                   f"{vendor['name'] if vendor else 'vendor'}, due {_nice(n.get('due_date'))}.")
    blocking = rejects or holds
    queue = blocking[0]["queue"] if blocking else "Payment run"
    actions = []
    for c in blocking:
        if c.get("action") and c["action"] not in actions:
            actions.append(c["action"])
    if not blocking:
        actions = ["Post to ledger and schedule in the next payment run."]

    email = None
    asks = [c["vendor_ask"] for c in blocking if c.get("vendor_ask")]
    if vendor and (asks or any(c["id"] == "R10" for c in rejects)):
        who = vendor["contact"]["name"].split()[0]
        ref = n.get("invoice_number") or "your recent invoice"
        if any(c["id"] == "R10" for c in rejects):
            body = (f"Hi {who},\n\nWe received invoice {ref} again. It matches an invoice we have already processed, "
                    "so we will not pay it a second time. If this is a new charge, please send it with a new invoice number "
                    "and a note of what it covers.\n\nThanks,\nAccounts Payable, Halden Foods")
        else:
            body = (f"Hi {who},\n\nThanks for invoice {ref}. Before we can process it we need "
                    + " and ".join(asks) + ".\n\nOnce we have that, it goes into the next payment run.\n\nThanks,\nAccounts Payable, Halden Foods")
        email = {"to": vendor["contact"]["email"], "subject": f"Invoice {ref}: action needed", "body": body}

    return {"decision": decision, "label": label, "summary": summary, "queue": queue,
            "actions": actions, "vendor_email": email,
            "counts": {"passed": sum(c["status"] == PASS for c in checks), "warnings": sum(c["status"] == WARN for c in checks),
                       "failed": len(fails)}}


def _nice(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%-d %b %Y")
    except (TypeError, ValueError):
        return "per terms"
