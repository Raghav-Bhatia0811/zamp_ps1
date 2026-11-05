"""Stage "Verify fields": prove each extracted value, repair OCR slips, score confidence.

An invoice carries its own error-detecting codes, and this stage uses them:
  * GSTIN has a mod-36 check digit and a fixed letter/digit pattern
  * IFSC is 4 letters + '0' + 6 characters, and its bank code must match the bank name
  * quantity x rate = amount, lines sum to the subtotal, subtotal + tax = total
  * the vendor master knows the true GSTIN and bank details

Status of every field, strongest evidence first:
  verified   proven by a checksum, the arithmetic, or the vendor master
  agreed     two independent reads (Claude and OCR) gave the same value
  read       one clear read, nothing to prove it against
  repaired   an OCR slip was corrected; the fix is shown and, for money, a person confirms
  disputed   two reads disagree and nothing settles it
  low        read, but with low OCR confidence
  missing    not on the document

Repairs are conservative: a value is only changed when the corrected value is
proven (checksum passes / master matches / arithmetic closes), and the change
is a known OCR confusion. A wrong amount the vendor actually printed is never
"repaired": that still fails R04 and goes back to the vendor.
"""
from __future__ import annotations

import itertools
import re

ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CRITICAL = ["total", "invoice_number", "bank_account", "bank_ifsc", "vendor_gstin"]
TRACKED = ["vendor_name", "vendor_gstin", "invoice_number", "invoice_date", "due_date", "po_reference",
           "subtotal", "tax_total", "total", "bank_account", "bank_ifsc"]

# characters OCR commonly swaps, by the type a slot needs
TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "U": "0", "I": "1", "L": "1", "J": "1", "T": "7", "Z": "2", "S": "5",
            "B": "8", "G": "6", "A": "4", "E": "8"}
TO_LETTER = {v: k for k, v in {"O": "0", "I": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "A": "4", "T": "7"}.items()}
DIGIT_SWAPS = {"0": "869", "1": "74", "2": "7", "3": "8", "4": "91", "5": "6", "6": "58", "7": "12", "8": "3060", "9": "40"}

IFSC_BANK = {"HDFC": "HDFC", "ICIC": "ICICI", "SBIN": "STATE BANK", "UTIB": "AXIS", "KKBK": "KOTAK", "YESB": "YES",
             "PUNB": "PUNJAB NATIONAL", "BARB": "BANK OF BARODA", "CNRB": "CANARA", "IDIB": "INDIAN BANK", "INDB": "INDUSIND"}


# ------------------------------------------------------------------ GSTIN
def gstin_check_char(g14: str) -> str:
    s = 0
    for i, c in enumerate(g14):
        p = ALNUM.index(c) * (1 if i % 2 == 0 else 2)
        s += p // 36 + p % 36
    return ALNUM[(36 - s % 36) % 36]


GSTIN_SHAPE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def gstin_valid(g: str | None) -> bool:
    return bool(g and GSTIN_SHAPE.match(g) and all(c in ALNUM for c in g) and gstin_check_char(g[:14]) == g[14])


def _coerce_gstin_shape(g: str) -> str:
    slots = "DDLLLLLDDDDLXZX"   # D digit, L letter, X either, Z literal
    out = []
    for c, s in zip(g, slots):
        if s == "D" and not c.isdigit():
            c = TO_DIGIT.get(c, c)
        elif s == "L" and c.isdigit():
            c = TO_LETTER.get(c, c)
        elif s == "Z" and c != "Z":
            c = "Z" if c in "2S7" else c
        out.append(c)
    return "".join(out)


def repair_gstin(raw: str | None, known: list[str]) -> dict:
    g = re.sub(r"[^0-9A-Z]", "", (raw or "").upper())
    if not g:
        return {"value": None, "status": "missing"}
    if gstin_valid(g):
        return {"value": g, "status": "verified", "evidence": "Check digit valid" + (" and matches the vendor master" if g in known else "")}
    if len(g) != 15:
        near = [k for k in known if _edit1(g, k)]
        if len(near) == 1:
            return {"value": near[0], "status": "repaired", "from": g,
                    "evidence": f"Read as {g} ({len(g)} characters); one character from {near[0]} in the vendor master, which is valid"}
        return {"value": g, "status": "low", "evidence": f"{len(g)} characters; a GSTIN has 15"}
    shaped = _coerce_gstin_shape(g)
    if gstin_valid(shaped):
        diff = _diff(g, shaped)
        return {"value": shaped, "status": "repaired", "from": g,
                "evidence": f"Read as {g}; {diff} fixes the letter/digit pattern and the check digit then passes"
                            + (", and it matches the vendor master" if shaped in known else "")}
    # try one confusable substitution that makes the check digit pass, prefer a master match
    cands = set()
    conf_map = {**TO_DIGIT, **TO_LETTER, **{k: v[0] for k, v in DIGIT_SWAPS.items()}}
    for i, c in enumerate(shaped):
        for alt in {conf_map.get(c), *DIGIT_SWAPS.get(c, "")} - {None, c}:
            t = shaped[:i] + alt + shaped[i + 1:]
            if gstin_valid(t):
                cands.add(t)
    in_master = [c for c in cands if c in known]
    if len(in_master) == 1:
        return {"value": in_master[0], "status": "repaired", "from": g,
                "evidence": f"Check digit failed on {g}; {_diff(g, in_master[0])} gives a valid GSTIN that is in the vendor master"}
    near = [k for k in known if _hamming(shaped, k) == 1]
    if len(near) == 1:
        return {"value": near[0], "status": "repaired", "from": g,
                "evidence": f"Check digit failed on {g}; one character from {near[0]} in the vendor master"}
    return {"value": g, "status": "low", "evidence": f"Check digit fails on {g} and no single-character fix is certain"}


def _hamming(a, b):
    return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))


def _edit1(a, b):
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return _hamming(a, b) <= 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def _diff(a, b):
    ch = [f"{x}→{y}" for x, y in zip(a, b) if x != y]
    return ", ".join(ch) if ch else "no change"


# ------------------------------------------------------------------ IFSC and account
IFSC_SHAPE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def repair_ifsc(raw: str | None, bank_name: str | None) -> dict:
    s = re.sub(r"[^0-9A-Z]", "", (raw or "").upper())
    if not s:
        return {"value": None, "status": "missing"}
    fixed = "".join(TO_LETTER.get(c, c) for c in s[:4]) + ("0" if s[4:5] in ("O", "D", "Q") else s[4:5]) + s[5:]
    status, ev = ("verified", "Format valid") if IFSC_SHAPE.match(s) else (("repaired", f"Read as {s}; {_diff(s, fixed)} fixes the format")
                                                                            if IFSC_SHAPE.match(fixed) else ("low", f"{s} is not a valid IFSC format"))
    val = fixed if status == "repaired" else s
    if status != "low" and bank_name:
        want = IFSC_BANK.get(val[:4])
        if want and want not in bank_name.upper():
            return {"value": val, "status": "low", "evidence": f"IFSC bank code {val[:4]} is {want.title()}, but the invoice names {bank_name}"}
        if want:
            ev += f"; bank code {val[:4]} matches {bank_name}"
    return {"value": val, "status": status, "evidence": ev, **({"from": s} if status == "repaired" else {})}


def repair_account(raw: str | None) -> dict:
    s = re.sub(r"[\s-]", "", (raw or "").upper())
    if not s:
        return {"value": None, "status": "missing"}
    d = "".join(TO_DIGIT.get(c, c) for c in s)
    if not d.isdigit() or not 9 <= len(d) <= 18:
        return {"value": s, "status": "low", "evidence": f"{s} is not a 9-18 digit account number"}
    if d != s:
        return {"value": d, "status": "repaired", "from": s, "evidence": f"Read as {s}; {_diff(s, d)} (account numbers are digits only)"}
    return {"value": d, "status": "read", "evidence": f"{len(d)} digits"}


# ------------------------------------------------------------------ arithmetic
def _close(a, b, tol=1.0):
    return a is not None and b is not None and abs(a - b) <= tol


def _ocr_neighbours(x: float) -> set[float]:
    """Values one OCR slip away: a confusable digit, a dropped digit, or an extra digit."""
    s = f"{x:.2f}"
    out = set()
    for i, c in enumerate(s):
        if not c.isdigit():
            continue
        for alt in DIGIT_SWAPS.get(c, ""):
            out.add(s[:i] + alt + s[i + 1:])
        out.add(s[:i] + s[i + 1:])
    for i in range(len(s) + 1):
        for d in "0123456789":
            out.add(s[:i] + d + s[i:])
    vals = set()
    for t in out:
        try:
            vals.add(float(t))
        except ValueError:
            pass
    vals.discard(x)
    return vals


def reconcile(f: dict) -> tuple[dict, list[dict], list[str]]:
    """Check the invoice's internal arithmetic. Returns (field statuses, line repairs, log)."""
    log, status, line_fixes = [], {}, []
    lines = f.get("line_items") or []
    # quantity x rate = amount, per line; repair one value per line only if it's an OCR neighbour
    for i, li in enumerate(lines, 1):
        q, r, a = li.get("quantity"), li.get("unit_price"), li.get("amount")
        if None in (q, r, a) or _close(q * r, a):
            continue
        for fld, want in (("amount", q * r), ("unit_price", a / q if q else None), ("quantity", a / r if r else None)):
            if want is None:
                continue
            if any(_close(v, want, 0.01) for v in _ocr_neighbours(li[fld])):
                line_fixes.append({"line": i, "field": fld, "from": li[fld], "to": round(want, 2)})
                log.append(f"Line {i}: {fld} read as {li[fld]:g} but {q:g} × {r:g} = {q * r:g}; {li[fld]:g} → {want:g} is a one-digit OCR slip")
                li[fld] = round(want, 2)
                break
    line_sum = round(sum((li.get("amount") or 0) for li in lines), 2) if lines else None
    sub, tax, tot = f.get("subtotal"), f.get("tax_total"), f.get("total")
    c1 = lines and _close(line_sum, sub)
    c2 = _close((sub or 0) + (tax or 0), tot) if sub is not None and tot is not None else False
    if c2 and (c1 or not lines):
        ev = "Lines, subtotal, tax and total all add up" if lines else "Subtotal + tax = total (no line items to cross-check)"
        for k in ("subtotal", "tax_total", "total"):
            status[k] = {"status": "verified", "evidence": ev}
        log.append("Arithmetic closes: " + ("line amounts = subtotal, subtotal + tax = total" if lines else "subtotal + tax = total"))
        return status, line_fixes, log
    # exactly one figure off: fix it only if the implied value is one OCR slip away
    cand = []
    if lines and not c1 and _close(line_sum + (tax or 0), tot):
        cand.append(("subtotal", sub, line_sum))
    if c1 and not c2 and tax is not None and tot is not None:
        cand.append(("total", tot, round(sub + tax, 2)))
        cand.append(("tax_total", tax, round(tot - sub, 2)))
    for fld, got, want in cand:
        if got is not None and any(_close(v, want, 0.01) for v in _ocr_neighbours(got)):
            f[fld] = want
            status[fld] = {"status": "repaired", "from": got,
                           "evidence": f"Read as {got:,.2f}; the other figures prove {want:,.2f}, one digit away (likely OCR slip)"}
            others = [k for k in ("subtotal", "tax_total", "total") if k != fld]
            for k in others:
                status[k] = {"status": "verified", "evidence": "Agrees with the rest of the arithmetic"}
            log.append(f"{fld.replace('_', ' ').capitalize()} read as {got:,.2f}, arithmetic proves {want:,.2f}: corrected, flagged for confirmation")
            return status, line_fixes, log
    if c2 and lines and not c1:
        # the totals agree with each other, the lines don't: on a scan that means a line was missed or misread
        for k in ("subtotal", "tax_total", "total"):
            status[k] = {"status": "verified", "evidence": "Subtotal + tax = total"}
        status["line_items"] = {"status": "low", "evidence": f"Lines read add up to {line_sum:,.2f} but the subtotal is {sub:,.2f}; "
                                                             "a line was probably missed or misread"}
        log.append("Totals agree with each other but not with the lines read: treated as an incomplete read, not a vendor error")
        return status, line_fixes, log
    log.append("Arithmetic does not close and no single OCR slip explains it; left as printed for the R04 check")
    return status, line_fixes, log


# ------------------------------------------------------------------ word-level OCR confidence
def _norm(s):
    return re.sub(r"[^0-9A-Z]", "", str(s).upper())


def ocr_confidence(value, words: list[dict]) -> float | None:
    """Confidence of the OCR read that produced a value: the lowest word confidence within one
    occurrence, and the best occurrence when the page and a region re-read both produced it."""
    if value in (None, "") or not words:
        return None
    target = _norm(value if not isinstance(value, float) else f"{value:.2f}")
    if not target:
        return None
    toks = [(_norm(w["text"]), w["conf"]) for w in words]
    best = None
    for n in (1, 2, 3):   # a value may be split over up to three OCR words
        for i in range(len(toks) - n + 1):
            joined = "".join(t for t, _ in toks[i:i + n])
            if joined and (joined == target or (len(target) >= 6 and target in joined and len(joined) - len(target) <= 3)):
                c = min(c for _, c in toks[i:i + n])
                best = c if best is None else max(best, c)
        if best is not None:
            return best
    return None


# ------------------------------------------------------------------ the stage
def verify(fields: dict, ctx: dict, master: dict) -> dict:
    """fields: extracted (mutated in place with repairs). ctx: {source, ocr_words, second_read, layout}.

    Returns {"fields": {name: {...}}, "log": [...], "repairs": [...], "critical_issues": [...], "score": 0-100}.
    """
    log, repairs = [], []
    out = {}
    source = ctx.get("source")              # 'text' | 'scan' | 'photo' | 'image'
    engine = (fields.get("_meta") or {}).get("engine")
    words = ctx.get("ocr_words") or []
    low_flags = set(fields.get("low_confidence_fields") or [])
    second = ctx.get("second_read")         # local OCR read when Claude was primary on an image

    def base(k):
        v = fields.get(k) if k != "tax_total" else (round(sum((t.get("amount") or 0) for t in fields.get("tax_lines") or []), 2)
                                                   if fields.get("tax_lines") else None)
        if v in (None, "", []):
            return {"value": None, "status": "missing", "conf": 0, "source": None}
        if source == "text" and engine == "local":
            return {"value": v, "status": "read", "conf": 99, "source": "PDF text layer"}
        if engine == "claude":
            c = 62 if k in low_flags else 93
            return {"value": v, "status": "low" if k in low_flags else "read", "conf": c, "source": "Claude"}
        c = ocr_confidence(v, words)
        if c is None and k in ("invoice_date", "due_date"):
            from datetime import datetime
            try:
                d = datetime.strptime(v, "%Y-%m-%d")
                for fmt in ("%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %B %Y"):
                    c = ocr_confidence(d.strftime(fmt), words)
                    if c is not None:
                        break
            except ValueError:
                pass
        c = 55.0 if c is None else c
        return {"value": v, "status": "read" if c >= 75 else "low", "conf": round(c), "source": "OCR"}

    for k in TRACKED:
        out[k] = base(k)
    fields["tax_total"] = out["tax_total"]["value"]

    # second, independent read (Claude primary on an image -> compare with OCR)
    if second:
        for k in TRACKED:
            a, b = out[k]["value"], second.get(k) if k != "tax_total" else None
            if a is None or b in (None, ""):
                continue
            same = _norm(a) == _norm(b) if not isinstance(a, (int, float)) else _close(float(a), float(b), 0.5)
            if same:
                out[k].update(status="agreed", conf=max(out[k]["conf"], 97), evidence="Claude and OCR read the same value")
            elif k in CRITICAL:
                out[k].update(status="disputed", conf=min(out[k]["conf"], 45), evidence=f"Claude read {a}, OCR read {b}")

    vendors = master["vendors"]
    known_gstins = [v["gstin"] for v in vendors]

    # GSTIN
    if fields.get("vendor_gstin"):
        r = repair_gstin(fields["vendor_gstin"], known_gstins)
        if r["value"] != fields["vendor_gstin"]:
            repairs.append({"field": "vendor_gstin", "from": fields["vendor_gstin"], "to": r["value"], "why": r["evidence"]})
            fields["vendor_gstin"] = r["value"]
        out["vendor_gstin"].update(value=r["value"], evidence=r.get("evidence"))
        if r["status"] in ("verified", "repaired"):
            # proven by the check digit: a repaired GSTIN that's in the master is as good as verified
            out["vendor_gstin"].update(status="verified" if r["status"] == "verified" or r["value"] in known_gstins else "repaired",
                                       conf=99 if r["value"] in known_gstins else 90)
        else:
            out["vendor_gstin"].update(status="low", conf=min(out["vendor_gstin"]["conf"], 50))
        log.append(f"GSTIN: {r.get('evidence', r['status'])}")

    # IFSC + account
    if fields.get("bank_ifsc"):
        r = repair_ifsc(fields["bank_ifsc"], fields.get("bank_name"))
        if r["value"] != fields["bank_ifsc"]:
            repairs.append({"field": "bank_ifsc", "from": fields["bank_ifsc"], "to": r["value"], "why": r["evidence"]})
            fields["bank_ifsc"] = r["value"]
        out["bank_ifsc"].update(value=r["value"], evidence=r["evidence"])
        if r["status"] == "low":
            out["bank_ifsc"].update(status="low", conf=min(out["bank_ifsc"]["conf"], 50))
        elif out["bank_ifsc"]["status"] in ("read", "low") and r["status"] == "verified":
            out["bank_ifsc"].update(status="verified", conf=max(out["bank_ifsc"]["conf"], 95))
        elif r["status"] == "repaired":
            out["bank_ifsc"].update(status="repaired", conf=88)
        log.append(f"IFSC: {r['evidence']}")
    if fields.get("bank_account"):
        r = repair_account(fields["bank_account"])
        if r["value"] != fields["bank_account"]:
            repairs.append({"field": "bank_account", "from": fields["bank_account"], "to": r["value"], "why": r["evidence"]})
            fields["bank_account"] = r["value"]
            out["bank_account"].update(value=r["value"], status="repaired", conf=80, evidence=r["evidence"])
        elif r["status"] == "low":
            out["bank_account"].update(status="low", conf=min(out["bank_account"]["conf"], 50), evidence=r["evidence"])
        # matching the vendor master proves it (a mismatch is R06's job, not a read problem)
        v = next((v for v in vendors if v["gstin"] == fields.get("vendor_gstin")), None)
        if v and r["value"] == v["bank"]["account"]:
            out["bank_account"].update(status="verified", conf=99, evidence="Equals the account in the vendor master")

    # arithmetic: the strongest proof for every amount on the page
    a_status, line_fixes, a_log = reconcile(fields)
    log += a_log
    for fx in line_fixes:
        repairs.append({"field": f"line {fx['line']} {fx['field']}", "from": fx["from"], "to": fx["to"], "why": "quantity × rate = amount"})
    if "tax_total" in a_status and a_status["tax_total"]["status"] == "repaired":
        # put the proven tax back on the tax rows proportionally
        want = fields["tax_total"]
        tl = fields.get("tax_lines") or []
        got = sum((t.get("amount") or 0) for t in tl)
        if tl and got:
            for t in tl:
                t["amount"] = round((t.get("amount") or 0) * want / got, 2)
    line_issue = a_status.pop("line_items", None)
    if line_issue and source != "text":
        fields["line_items_incomplete"] = True
        out["line_items"] = {"value": f"{len(fields.get('line_items') or [])} lines", "status": "low", "conf": 50,
                             "source": "OCR" if engine != "claude" else "Claude", "evidence": line_issue["evidence"]}
    for k, st in a_status.items():
        if st["status"] == "repaired":
            repairs.append({"field": k, "from": st["from"], "to": fields.get(k), "why": st["evidence"]})
            out[k].update(value=fields.get(k), status="repaired", conf=75, evidence=st["evidence"])
        elif out[k]["value"] is not None and out[k]["status"] != "disputed":
            out[k].update(status="verified", conf=99, evidence=st["evidence"])

    # dates: parseable and in order
    from datetime import datetime
    def _d(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None
    di, dd = _d(fields.get("invoice_date")), _d(fields.get("due_date"))
    if di and dd:
        if dd >= di:
            for k in ("invoice_date", "due_date"):
                if out[k]["status"] in ("read", "low") and out[k]["conf"] >= 60:
                    out[k].update(status="verified", conf=max(out[k]["conf"], 95), evidence="Valid date; due date falls after invoice date")
        else:
            out["due_date"].update(status="low", conf=40, evidence="Due date is before the invoice date")
    elif fields.get("invoice_date") and not di:
        out["invoice_date"].update(status="low", conf=30, evidence="Not a valid date")

    # vendor name matched in master is verified
    v = next((v for v in vendors if v["gstin"] == fields.get("vendor_gstin")), None)
    if v and out["vendor_name"]["value"]:
        out["vendor_name"].update(status="verified", conf=99, evidence=f"GSTIN identifies {v['name']}")
    po = (fields.get("po_reference") or "").upper().replace(" ", "")
    if po and any(p["id"] == re.sub(r"^PO-?", "PO-", po) for p in master["purchase_orders"]):
        out["po_reference"].update(status="verified", conf=99, evidence="Exists in the PO register")

    # policy §4.5: anything read badly, disputed, or corrected by the system on a money field goes to a person.
    # Identifiers proven by a check digit or the master (GSTIN, IFSC format) do not.
    money = ("total", "subtotal", "tax_total", "bank_account")
    critical = [{"field": k, **out[k]} for k in CRITICAL + ["line_items", "subtotal", "tax_total"] if k in out
                if (out[k]["status"] in ("low", "disputed") and k in CRITICAL + ["line_items"])
                or (out[k]["status"] == "repaired" and k in money)]
    if line_fixes:
        critical.append({"field": "line_items", "status": "repaired", "conf": 75, "value": None,
                         "evidence": "; ".join(f"line {x['line']} {x['field']} {x['from']:g} → {x['to']:g}" for x in line_fixes)})
    present = [o for o in out.values() if o["status"] != "missing"]
    score = round(sum(o["conf"] for o in present) / len(present)) if present else 0
    return {"fields": out, "log": log, "repairs": repairs, "critical_issues": critical, "score": score}
