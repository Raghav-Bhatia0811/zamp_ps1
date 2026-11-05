"""Synthetic invoice pages with exact region labels, for training the layout detector.

Every page is drawn from scratch with randomised layout, fonts, content and
damage, and every region is recorded as it is drawn, so labels are exact and
free. No real invoice is copied.

Classes (keep in sync with app/vision.py LAYOUT_CLASSES):
  0 vendor_block   seller name, address, GSTIN
  1 invoice_meta   invoice no., dates, PO reference
  2 bill_to        buyer block
  3 line_items     the item table
  4 totals         subtotal, tax rows, grand total
  5 bank_details   payee bank block
  6 stamp          rubber stamps and seals (PAID, RECEIVED, ...)
  7 signature      handwritten signatures
  8 handwriting    handwritten notes or corrections

Used by scripts/make_layout_dataset.py. Augmentations (scan, photo, fax) are
applied with the boxes transformed alongside the pixels.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

CLASSES = ["vendor_block", "invoice_meta", "bill_to", "line_items", "totals",
           "bank_details", "stamp", "signature", "handwriting"]
C = {n: i for i, n in enumerate(CLASSES)}

PAGE_W, PAGE_H = 1240, 1754  # A4 at 150 dpi

FONT_DIRS = ["/usr/share/fonts/truetype/liberation2", "/usr/share/fonts/truetype/liberation",
             "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/freefont"]
FAMILIES = [  # (regular, bold)
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"),
    ("FreeSans.ttf", "FreeSansBold.ttf"),
    ("FreeSerif.ttf", "FreeSerifBold.ttf"),
    ("LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf"),
]
HAND_FONTS = ["/usr/share/fonts/truetype/tlwg/Purisa.ttf", "/usr/share/fonts/truetype/tlwg/Purisa-Oblique.ttf",
              "/usr/share/fonts/opentype/urw-base35/Z003-MediumItalic.otf"]


def _find(name):
    for d in FONT_DIRS:
        p = Path(d) / name
        if p.exists():
            return str(p)
    return None


_font_cache = {}


def font(path, size):
    key = (path, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    return _font_cache[key]


# ------------------------------------------------------------------ content
W1 = ["Northgate", "Sapphire", "Kaveri", "Trident", "Orchid", "Sahyadri", "Vertex", "Banyan", "Crescent", "Indus",
      "Pinnacle", "Lotus", "Everest", "Saffron", "Monsoon", "Granite", "Harbor", "Sterling", "Neelam", "Arjun",
      "Kestrel", "Meridian", "Apex", "Brightline", "Northwind", "Coral", "Deccan", "Falcon", "Garuda", "Horizon"]
W2 = ["Office Supplies", "Logistics", "Facility Services", "IT Solutions", "Print Co.", "Packaging", "Engineering",
      "Traders", "Chemicals", "Foods", "Enterprises", "Industries", "Technologies", "Consultants", "Electricals",
      "Hardware", "Transport", "Agencies", "Systems", "Textiles"]
W3 = ["Pvt Ltd", "Private Limited", "LLP", "& Co.", "Ltd", ""]
CITIES = [("Pune", "411045", "27"), ("Mumbai", "400093", "27"), ("Bengaluru", "560058", "29"), ("New Delhi", "110020", "07"),
          ("Chennai", "600032", "33"), ("Hyderabad", "500081", "36"), ("Ahmedabad", "380015", "24"), ("Kolkata", "700091", "19")]
STREETS = ["Unit 7, Kasturi Arcade", "No. 42, 3rd Cross", "Office 302, Siddhi Towers", "B-118, Industrial Area Phase II",
           "Gala 9, Shah Industrial Estate", "Plot 21, MIDC", "4th Floor, Prestige Point", "Shop 3, Market Yard",
           "Survey No. 88, Hinjewadi", "12/A, Ring Road"]
ITEMS = ["A4 copier paper 75gsm", "Toner cartridge HP 88A", "Whiteboard markers (pack of 10)", "FTL freight Pune to Bengaluru",
         "Housekeeping services", "Pest control treatment", "Dell Latitude 5450 i5 16GB", "Docking station WD19S",
         "Product catalogue print A4 32pp", "Point-of-sale standees", "Filter coffee powder 1kg", "Paper cups 150ml",
         "Corrugated boxes 5-ply", "Stretch wrap film 23 micron", "Annual maintenance contract", "Security guard services",
         "Diesel generator rental", "LED panel light 40W", "Safety shoes size 8", "Nitrile gloves (box of 100)",
         "Server rack 42U", "Network switch 24-port", "Consulting fees October", "Cold chain transport", "Label rolls 100x150",
         "HDPE drums 200L", "Forklift hire per day", "Canteen services", "Courier charges", "Software licence renewal"]
STAMP_TEXT = ["PAID", "RECEIVED", "ENTERED", "VERIFIED", "DUPLICATE", "COPY", "CANCELLED", "ACCOUNTS DEPT",
              "GOODS RECEIVED", "APPROVED", "ORIGINAL", "FOR OFFICE USE"]
HAND_TEXT = ["Pay to new a/c", "Revised 1,24,500", "Approved - RK", "Recd 03/11", "pls process urgently", "short by 2 boxes",
             "see email", "OK to pay", "check rate", "GRN 4471", "Paid by cash", "call vendor", "PO?", "2 cartons damaged"]


def rnd_gstin(r, state):
    a = "".join(r.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5))
    return f"{state}{a}{r.randint(1000, 9999)}{r.choice('ABCDEFGHJK')}1Z{r.choice('0123456789ABCDEFGH')}"


def inr(x):
    whole, frac = f"{x:.2f}".split(".")
    if len(whole) > 3:
        head, tail, g = whole[:-3], whole[-3:], []
        while len(head) > 2:
            g.insert(0, head[-2:]); head = head[:-2]
        if head:
            g.insert(0, head)
        whole = ",".join(g + [tail])
    return f"{whole}.{frac}"


# ------------------------------------------------------------------ page builder
class Page:
    def __init__(self, r: random.Random):
        self.r = r
        tone = r.choice([255, 255, 255, 252, 248])
        self.img = Image.new("RGB", (PAGE_W, PAGE_H), (tone, tone, max(0, tone - r.randint(0, 6))))
        self.d = ImageDraw.Draw(self.img)
        fam = r.choice(FAMILIES)
        self.reg, self.bold = _find(fam[0]), _find(fam[1])
        self.scale = r.uniform(0.88, 1.15)
        self.boxes = []          # (cls, x0, y0, x1, y1)
        self._cur = None

    def fs(self, s):
        return max(10, int(s * self.scale))

    # region bookkeeping
    def begin(self, cls):
        self._cur = [cls, 1e9, 1e9, -1e9, -1e9]

    def _grow(self, b):
        if self._cur:
            c = self._cur
            c[1], c[2], c[3], c[4] = min(c[1], b[0]), min(c[2], b[1]), max(c[3], b[2]), max(c[4], b[3])

    def end(self, pad=6):
        c, self._cur = self._cur, None
        if c and c[3] > c[1]:
            self.boxes.append((C[c[0]], c[1] - pad, c[2] - pad, c[3] + pad, c[4] + pad))

    # drawing
    def text(self, x, y, s, size=20, bold=False, align="left", fill=(20, 20, 20)):
        f = font(self.bold if bold else self.reg, self.fs(size))
        w = self.d.textlength(s, font=f)
        if align == "right":
            x -= w
        elif align == "center":
            x -= w / 2
        self.d.text((x, y), s, font=f, fill=fill)
        b = self.d.textbbox((x, y), s, font=f)
        self._grow(b)
        return b

    def line(self, x0, y0, x1, y1, w=1, fill=(60, 60, 60)):
        self.d.line([(x0, y0), (x1, y1)], width=w, fill=fill)
        self._grow((x0, min(y0, y1), x1, max(y0, y1)))

    def rect(self, x0, y0, x1, y1, fill=None, outline=None):
        self.d.rectangle([x0, y0, x1, y1], fill=fill, outline=outline)
        self._grow((x0, y0, x1, y1))

    def lh(self, size):
        return int(self.fs(size) * self.r.uniform(1.25, 1.55))


def random_invoice(seed: int):
    r = random.Random(seed)
    p = Page(r)
    M = r.randint(70, 120)                      # page margin
    right = PAGE_W - M
    city, pin, state = r.choice(CITIES)
    vendor = f"{r.choice(W1)} {r.choice(W2)} {r.choice(W3)}".strip()
    head_style = r.choice(["left", "center", "band", "left_logo"])
    ink = (20, 20, 20)
    y = M

    # ---------- vendor block (+ title)
    band_h = 0
    if head_style == "band":
        band_h = r.randint(170, 230)
        col = r.choice([(29, 43, 69), (36, 60, 48), (70, 30, 30), (40, 40, 40), (20, 60, 110)])
        p.d.rectangle([0, 0, PAGE_W, band_h], fill=col)
        ink_h = (255, 255, 255)
    else:
        ink_h = ink
    p.begin("vendor_block")
    if head_style == "left_logo":
        s = r.randint(70, 100)
        shape = r.choice(["rect", "circle"])
        colr = tuple(r.randint(20, 200) for _ in range(3))
        (p.d.ellipse if shape == "circle" else p.d.rectangle)([M, y, M + s, y + s], fill=colr)
        p._grow((M, y, M + s, y + s))
        x0 = M + s + 20
    else:
        x0 = M
    vy = y + (10 if head_style == "band" else 0)
    al = "center" if head_style == "center" else "left"
    vx = PAGE_W / 2 if head_style == "center" else x0
    p.text(vx, vy, vendor.upper() if r.random() < .3 else vendor, size=r.randint(30, 40), bold=True, align=al, fill=ink_h)
    vy += p.lh(36)
    addr = [r.choice(STREETS), f"{city} {pin}"]
    if head_style in ("center", "band") and r.random() < .6:
        addr = [", ".join(addr)]
    for ln in addr:
        p.text(vx, vy, ln, size=17, align=al, fill=ink_h); vy += p.lh(17)
    gst = rnd_gstin(r, state)
    extra = [f"GSTIN: {gst}", f"+91 {r.randint(20, 99)} {r.randint(1000, 9999)} {r.randint(1000, 9999)}  |  accounts@{vendor.split()[0].lower()}.in"]
    if r.random() < .5:
        extra = [f"GSTIN {gst}   Tel: +91 {r.randint(20, 99)} {r.randint(10000000, 99999999)}"]
    for ln in extra:
        p.text(vx, vy, ln, size=16, align=al, fill=ink_h); vy += p.lh(16)
    p.end()

    title = r.choice(["TAX INVOICE", "Invoice", "INVOICE", "Tax Invoice", "BILL"])
    if head_style == "center":
        vy += 10
        p.line(M, vy, right, vy, w=2)
        vy += 18
        p.text(PAGE_W / 2, vy, title, size=26, bold=True, align="center")
        vy += p.lh(26)
        y = vy + 10
    else:
        p.text(right, y + 6, title, size=r.randint(26, 40), bold=True, align="right", fill=ink_h)
        y = max(vy, band_h) + r.randint(25, 50)

    # ---------- invoice meta
    no = r.choice([f"INV-2026-{r.randint(100, 9999):04d}", f"{r.choice(['BL', 'AFS', 'KIT', 'MPC', 'NW'])}/{r.randint(1, 999):03d}",
                   f"{r.randint(1000, 99999)}", f"{r.choice(['GST', 'TI'])}/26-27/{r.randint(1, 999)}"])
    d1 = f"{r.randint(1, 28):02d} {r.choice(['Sep', 'Oct', 'Nov'])} 2026" if r.random() < .6 else f"{r.randint(1, 28):02d}-{r.randint(9, 11):02d}-2026"
    meta = [(r.choice(["Invoice No:", "Invoice #", "Bill No.:", "Invoice Number"]), no),
            (r.choice(["Invoice Date:", "Dated:", "Issued", "Date"]), d1)]
    if r.random() < .75:
        meta.append((r.choice(["PO Number:", "Your P.O. Ref:", "Purchase Order", "PO"]), f"PO-{r.randint(1000, 1999)}"))
    meta.append((r.choice(["Due Date:", "Payment due", "Due by:"]), f"{r.randint(1, 28):02d} Dec 2026"))
    meta_style = r.choice(["right_kv", "left_kv", "two_col", "inline"]) if head_style != "center" else r.choice(["inline", "left_kv", "two_col"])

    def kv_block(x_label, x_val, yy, items, val_align="left"):
        for k, v in items:
            p.text(x_label, yy, k, size=17, bold=r.random() < .5)
            p.text(x_val, yy, v, size=17, align=val_align)
            yy += p.lh(17)
        return yy

    bill_first = meta_style == "right_kv"
    if meta_style == "right_kv":
        p.begin("invoice_meta")
        ym = kv_block(right - 420, right, y, meta, val_align="right")
        p.end()
        meta_bottom = ym
    elif meta_style == "left_kv":
        p.begin("invoice_meta")
        ym = kv_block(M, M + 190, y, meta)
        p.end()
        y = ym + r.randint(20, 40)
    elif meta_style == "two_col":
        half = (len(meta) + 1) // 2
        p.begin("invoice_meta"); y1 = kv_block(M, M + 190, y, meta[:half]); p.end()
        p.begin("invoice_meta"); y2 = kv_block(PAGE_W / 2, PAGE_W / 2 + 190, y, meta[half:]); p.end()
        y = max(y1, y2) + r.randint(20, 40)
    else:  # inline pairs left/right
        p.begin("invoice_meta")
        yy = y
        for i in range(0, len(meta), 2):
            p.text(M, yy, f"{meta[i][0]} {meta[i][1]}", size=17)
            if i + 1 < len(meta):
                p.text(right, yy, f"{meta[i + 1][0]} {meta[i + 1][1]}", size=17, align="right")
            yy += p.lh(17)
        p.end()
        y = yy + r.randint(20, 40)

    # ---------- bill to
    p.begin("bill_to")
    yb = y
    p.text(M, yb, r.choice(["Bill To", "Billed to", "To,", "Buyer", "Customer"]), size=17, bold=True); yb += p.lh(17)
    buyer = ["Halden Foods Pvt Ltd", "Plot 14, MIDC Bhosari", "Pune, Maharashtra 411026", "GSTIN: 27AAACH1234F1Z3"]
    if r.random() < .4:
        buyer = [", ".join(buyer[:3]), buyer[3]]
    for ln in buyer:
        p.text(M, yb, ln, size=17); yb += p.lh(17)
    p.end()
    if bill_first:
        y = max(yb, meta_bottom)
    else:
        y = yb
    if r.random() < .3:  # stray memo line (unlabelled)
        p.text(M if r.random() < .5 else PAGE_W / 2, y + 6, r.choice(["Ref: your email order dated 12 Oct 2026", "Place of supply: Maharashtra",
                                                                     "Reverse charge: No", "Vehicle No: MH12 AB 4471"]), size=16)
        y += p.lh(16)
    y += r.randint(30, 60)

    # ---------- line items
    n = r.choice([1, 1, 2, 2, 3, 3, 4, 5, 6, 8, 10])
    rows = []
    for _ in range(n):
        q = r.choice([1, 2, 5, 6, 8, 12, 20, 25, 40, 45, 60, 100, 2000])
        rate = r.choice([45, 185, 390, 420, 640, 660, 1450, 3200, 6500, 14200, 20000, 78500, 84000]) * r.choice([1, 1, 1.05])
        rows.append((r.choice(ITEMS), str(r.randint(1000, 999999)), q, round(rate, 2)))
    grid = r.random() < .6
    fill = r.random() < .45
    xs = {"sn": M + 8, "desc": M + 50, "hsn": M + int((right - M) * r.uniform(.47, .55)),
          "qty": M + int((right - M) * r.uniform(.66, .7)), "rate": M + int((right - M) * r.uniform(.82, .86)), "amt": right - 8}
    p.begin("line_items")
    th = p.fs(17)
    if fill:
        p.rect(M, y - 8, right, y + th + 8, fill=r.choice([(233, 237, 244), (230, 230, 230), (225, 238, 228), (40, 50, 70)]))
    hdr_ink = (255, 255, 255) if fill and p.img.getpixel((M + 2, y))[0] < 100 else ink
    show_hsn = r.random() < .7
    p.text(xs["sn"], y, "#", size=17, bold=True, fill=hdr_ink)
    p.text(xs["desc"], y, r.choice(["Description", "Particulars", "Item", "Description of goods/services"]), size=17, bold=True, fill=hdr_ink)
    if show_hsn:
        p.text(xs["hsn"], y, "HSN/SAC", size=17, bold=True, fill=hdr_ink)
    p.text(xs["qty"], y, "Qty", size=17, bold=True, align="right", fill=hdr_ink)
    p.text(xs["rate"], y, "Rate", size=17, bold=True, align="right", fill=hdr_ink)
    p.text(xs["amt"], y, "Amount", size=17, bold=True, align="right", fill=hdr_ink)
    y += th + 14
    if grid:
        p.line(M, y, right, y, w=2)
    y += 12
    rh = p.lh(17) + r.randint(4, 16)
    for i, (desc, hsn, q, rate) in enumerate(rows, 1):
        p.text(xs["sn"], y, str(i), size=17)
        p.text(xs["desc"], y, desc, size=17)
        if show_hsn:
            p.text(xs["hsn"], y, hsn, size=17)
        p.text(xs["qty"], y, f"{q:g}", size=17, align="right")
        p.text(xs["rate"], y, inr(rate), size=17, align="right")
        p.text(xs["amt"], y, inr(q * rate), size=17, align="right")
        y += rh
        if grid:
            p.line(M, y - 8, right, y - 8, w=1, fill=(150, 150, 150))
    p.end()
    y += r.randint(15, 35)

    # ---------- totals
    sub = sum(q * rt for _, _, q, rt in rows)
    rate = r.choice([5, 12, 18, 18, 18, 28])
    tax = round(sub * rate / 100, 2)
    trows = [(f"IGST @ {rate}%", tax)] if r.random() < .4 else [(f"CGST @ {rate / 2:g}%", tax / 2), (f"SGST @ {rate / 2:g}%", tax / 2)]
    tl = right - r.randint(380, 480) if r.random() < .85 else M
    tv = right if tl > M else M + 420
    p.begin("totals")
    yt = y
    p.text(tl, yt, r.choice(["Sub Total", "Subtotal", "Taxable Value", "Total before tax"]), size=17)
    p.text(tv, yt, inr(sub), size=17, align="right"); yt += p.lh(17)
    for lbl, amt in trows:
        p.text(tl, yt, lbl, size=17); p.text(tv, yt, inr(amt), size=17, align="right"); yt += p.lh(17)
    yt += 6
    if r.random() < .5:
        p.line(tl, yt, tv, yt, w=1)
        yt += 8
    p.text(tl, yt, r.choice(["Grand Total (INR)", "Total Amount Due", "Total", "Invoice Total", "Amount Payable"]), size=20, bold=True)
    p.text(tv, yt, ("Rs. " if r.random() < .3 else "") + inr(sub + tax), size=20, bold=True, align="right"); yt += p.lh(20)
    if r.random() < .4:
        p.text(tl if tl > M else M, yt, "Rupees " + r.choice(["One Lakh Twenty Four Thousand only", "Eighty Six Thousand Four Hundred only",
                                                               "Two Lakh Fifty Thousand only"]), size=14)
        yt += p.lh(14)
    p.end()
    totals_box = p.boxes[-1]
    y = yt + r.randint(40, 90)

    # ---------- bank details (position varies)
    bank_y = y if r.random() < .8 else PAGE_H - M - 260
    bx = M if r.random() < .8 else PAGE_W / 2
    p.begin("bank_details")
    yy = bank_y
    p.text(bx, yy, r.choice(["Bank details for payment", "Bank Details", "Payment Information", "Remit to"]), size=17, bold=True); yy += p.lh(17)
    bank = r.choice(["HDFC Bank", "ICICI Bank", "State Bank of India", "Axis Bank", "Kotak Mahindra Bank", "Yes Bank"])
    acct = str(r.randint(10 ** 10, 10 ** 14))
    ifsc = r.choice(["HDFC", "ICIC", "SBIN", "UTIB", "KKBK", "YESB"]) + "0" + f"{r.randint(0, 999999):06d}"
    lines = [f"Bank: {bank}", f"A/c No: {acct}", f"IFSC: {ifsc}"]
    if r.random() < .3:
        lines = [f"{bank}  A/c {acct}  IFSC {ifsc}"]
    if r.random() < .3:
        lines.insert(1, f"A/c Name: {vendor}")
    for ln in lines:
        p.text(bx, yy, ln, size=16); yy += p.lh(16)
    p.end()
    if r.random() < .2:  # unlabelled change-of-bank note
        p.text(bx, yy + 4, "Please note: our bank details have changed. Kindly update your records.", size=16, bold=True)
        yy += p.lh(16)

    # ---------- footer noise (unlabelled)
    if r.random() < .6:
        terms = ["Terms & Conditions", "1. Interest @18% p.a. on overdue payments.", "2. Goods once sold will not be taken back.",
                 "3. Subject to local jurisdiction.", "This is a computer generated invoice."]
        ty = max(yy + 40, PAGE_H - M - 150)
        if ty < PAGE_H - 60:
            for ln in terms[: r.randint(1, 5)]:
                p.text(M, ty, ln, size=14, fill=(70, 70, 70)); ty += p.lh(14)
    sig_area = (right - 360, PAGE_H - M - 230, right, PAGE_H - M - 40)
    has_sig = r.random() < .55
    if has_sig or r.random() < .3:
        p.text(right, PAGE_H - M - 60, "Authorised Signatory", size=15, align="right")
        p.text(right, PAGE_H - M - 210, f"For {vendor}", size=15, align="right")

    # ---------- signature, stamp, handwriting (added on top, like real ink)
    if has_sig:
        draw_signature(p, r, sig_area)
    for _ in range(r.choice([0, 0, 0, 1, 1, 1, 2])):
        draw_stamp(p, r, near=totals_box if r.random() < .5 else None)
    for _ in range(r.choice([0, 0, 0, 0, 1, 1, 2])):
        draw_handwriting(p, r, near=totals_box if r.random() < .4 else None)
    return p.img, p.boxes


# ------------------------------------------------------------------ ink: signatures, stamps, notes
def _pen(r):
    return r.choice([(20, 40, 140), (15, 15, 25), (30, 30, 110), (10, 60, 160)])


def draw_signature(p: Page, r, area):
    x0, y0, x1, y1 = area
    col, w = _pen(r), r.randint(2, 4)
    cx, cy = r.uniform(x0 + 40, x1 - 260), r.uniform(y0 + 50, y1 - 70)
    hf = [h for h in HAND_FONTS if Path(h).exists()]
    if hf and r.random() < .45:  # a written name in a script hand
        name = r.choice(["R. Kulkarni", "Priya M", "S Iyer", "Amit", "J. Fernandes", "K. Rao", "Neha S", "V Patil"])
        f = font(hf[r.randrange(len(hf))], r.randint(34, 50))
        layer = Image.new("L", (int(p.d.textlength(name, font=f)) + 30, 90), 0)
        ImageDraw.Draw(layer).text((15, 10), name, font=f, fill=255)
        layer = layer.rotate(r.uniform(-10, 10), expand=True, resample=Image.BICUBIC)
        x, y = int(cx), int(cy)
        p.img.paste(Image.new("RGB", layer.size, col), (x, y), layer)
        bb = layer.getbbox()
        if bb:
            p.boxes.append((C["signature"], x + bb[0] - 4, y + bb[1] - 4, x + bb[2] + 4, y + bb[3] + 4))
        return
    # cursive scribble: a run of loops drifting right, with varying height
    pts = []
    n_loops = r.randint(3, 8)
    L = r.uniform(140, 260)
    amp = r.uniform(14, 30)
    steps = n_loops * 24
    ph = r.uniform(0, 6.28)
    for k in range(steps):
        t = k / steps
        th = t * n_loops * 2 * math.pi
        rad = amp * (0.6 + 0.4 * math.sin(t * 7 + ph))
        x = cx + t * L + rad * 0.55 * math.cos(th)
        y = cy - rad * math.sin(th) * r.uniform(.9, 1.1) + 10 * math.sin(t * 3 + ph)
        pts.append((x, y))
    p.d.line(pts, fill=col, width=w, joint="curve")
    if r.random() < .6:  # flourish underline
        ly = cy + amp + r.uniform(4, 14)
        a, b = (cx - r.uniform(5, 30), ly), (cx + L + r.uniform(10, 40), ly - r.uniform(-12, 12))
        p.d.line([a, b], fill=col, width=w)
        pts += [a, b]
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    p.boxes.append((C["signature"], min(xs) - 6, min(ys) - 6, max(xs) + 6, max(ys) + 6))


def draw_stamp(p: Page, r, near=None):
    text = r.choice(STAMP_TEXT)
    col = r.choice([(190, 30, 40), (30, 50, 170), (110, 40, 140), (20, 110, 60)])
    fpath = _find(r.choice(["LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSerif-Bold.ttf"]))
    fsz = r.randint(34, 58)
    f = font(fpath, fsz)
    tw = int(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=f))
    kind = r.choice(["box", "box", "round", "circle"])
    sub = r.choice(["", "", f"{r.randint(1, 28):02d} OCT 2026", "HALDEN FOODS", "AP DEPT"])
    sf = font(fpath, max(14, fsz // 3))
    if kind == "circle":
        S = max(tw, 160) + 60
        w_, h_ = S, S
    else:
        w_, h_ = tw + 50, fsz + 40 + (fsz // 3 + 8 if sub else 0)
    st = Image.new("L", (w_ + 20, h_ + 20), 0)
    sd = ImageDraw.Draw(st)
    if kind == "circle":
        sd.ellipse([10, 10, w_ + 10, h_ + 10], outline=255, width=5)
        sd.ellipse([24, 24, w_ - 4, h_ - 4], outline=255, width=2)
        sd.text(((w_ + 20 - tw) / 2, (h_ + 20 - fsz) / 2 - 6), text, font=f, fill=255)
        if sub:
            sw = sd.textlength(sub, font=sf)
            sd.text(((w_ + 20 - sw) / 2, (h_ + 20) / 2 + fsz / 2), sub, font=sf, fill=255)
    else:
        rad = 18 if kind == "round" else 2
        sd.rounded_rectangle([10, 10, w_ + 10, h_ + 10], radius=rad, outline=255, width=r.randint(4, 7))
        sd.text((35, 26), text, font=f, fill=255)
        if sub:
            sd.text((35, 26 + fsz + 6), sub, font=sf, fill=255)
    # ink texture: knock out random specks so it looks stamped
    a = np.array(st, dtype=np.float32)
    noise = np.random.default_rng(r.randint(0, 1 << 30)).random(a.shape)
    a *= (noise > r.uniform(.05, .35)).astype(np.float32) * r.uniform(.55, .9)
    st = Image.fromarray(a.astype(np.uint8)).rotate(r.uniform(-28, 28), expand=True, resample=Image.BICUBIC)
    if near and r.random() < .8:
        _, nx0, ny0, nx1, ny1 = near
        x = int(r.uniform(nx0 - st.width * .3, max(nx0, nx1 - st.width * .7)))
        y = int(r.uniform(ny0 - st.height * .5, ny1 - st.height * .3))
    else:
        x, y = r.randint(40, PAGE_W - st.width - 40), r.randint(200, PAGE_H - st.height - 60)
    x, y = max(0, min(PAGE_W - st.width, x)), max(0, min(PAGE_H - st.height, y))
    p.img.paste(Image.new("RGB", st.size, col), (x, y), st)
    bb = st.getbbox()
    if bb:
        p.boxes.append((C["stamp"], x + bb[0], y + bb[1], x + bb[2], y + bb[3]))


def draw_handwriting(p: Page, r, near=None):
    hf = [h for h in HAND_FONTS if Path(h).exists()]
    text = r.choice(HAND_TEXT)
    col = _pen(r)
    f = font(hf[r.randrange(len(hf))] if hf else None, r.randint(30, 46))
    tw = int(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=f))
    layer = Image.new("L", (tw + 40, 90), 0)
    ld = ImageDraw.Draw(layer)
    ld.text((20, 15), text, font=f, fill=255)
    if r.random() < .35:  # underline or circle
        ld.line([(15, 75), (tw + 25, 70 + r.randint(-6, 6))], fill=255, width=3)
    layer = layer.rotate(r.uniform(-12, 12), expand=True, resample=Image.BICUBIC)
    if near:
        _, nx0, ny0, nx1, ny1 = near
        x, y = int(r.uniform(nx0 - 200, nx1 - layer.width)), int(r.uniform(ny0 - 80, ny1 + 40))
    else:
        x, y = r.randint(40, PAGE_W - layer.width - 40), r.randint(150, PAGE_H - layer.height - 60)
    x, y = max(0, min(PAGE_W - layer.width, x)), max(0, min(PAGE_H - layer.height, y))
    if r.random() < .3 and near:  # strike through a printed amount and write over it
        _, nx0, ny0, nx1, ny1 = near
        sy = int(r.uniform(ny0 + 10, ny1 - 10))
        p.d.line([(nx1 - 160, sy), (nx1, sy - r.randint(-4, 4))], fill=col, width=3)
    p.img.paste(Image.new("RGB", layer.size, col), (x, y), layer)
    bb = layer.getbbox()
    if bb:
        p.boxes.append((C["handwriting"], x + bb[0], y + bb[1], x + bb[2], y + bb[3]))


# ------------------------------------------------------------------ damage with box tracking
def _warp_boxes(boxes, M, w, h):
    out = []
    for c, x0, y0, x1, y1 in boxes:
        pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], dtype=np.float64).T
        q = M @ pts
        q = q[:2] / q[2:3]
        nx0, ny0, nx1, ny1 = q[0].min(), q[1].min(), q[0].max(), q[1].max()
        nx0, ny0, nx1, ny1 = max(0, nx0), max(0, ny0), min(w, nx1), min(h, ny1)
        if nx1 - nx0 > 8 and ny1 - ny0 > 8:
            out.append((c, nx0, ny0, nx1, ny1))
    return out


def degrade(img: Image.Image, boxes, r: random.Random, mode: str):
    """mode: clean | scan | fax | photo. Returns (image, boxes)."""
    import cv2

    a = np.array(img)
    h, w = a.shape[:2]
    rng = np.random.default_rng(r.randint(0, 1 << 30))
    if mode == "clean":
        pass
    elif mode in ("scan", "fax"):
        ang = r.uniform(-2.5, 2.5)
        M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
        a = cv2.warpAffine(a, M, (w, h), borderValue=(255, 255, 255))
        boxes = _warp_boxes(boxes, np.vstack([M, [0, 0, 1]]), w, h)
        g = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float32)
        g = g * r.uniform(.8, 1.0) + r.uniform(0, 30)
        g += rng.normal(0, r.uniform(3, 14), g.shape)
        if mode == "fax":
            g = cv2.resize(g, (w // 2, h // 2))
            g = np.where(g > r.uniform(120, 175), 255, 0).astype(np.float32)
            g = cv2.resize(g, (w, h), interpolation=cv2.INTER_NEAREST)
        else:
            k = r.choice([1, 3, 3, 5])
            g = cv2.GaussianBlur(g, (k, k), 0)
        a = cv2.cvtColor(np.clip(g, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2RGB)
    elif mode == "photo":
        # place the page on a background with perspective, then add uneven light
        bw, bh = int(w * r.uniform(1.08, 1.3)), int(h * r.uniform(1.05, 1.2))
        bg_col = np.array(r.choice([(120, 90, 60), (70, 70, 75), (180, 170, 150), (40, 45, 60), (150, 120, 90)]), np.float32)
        bg = np.clip(bg_col + rng.normal(0, 12, (bh, bw, 3)), 0, 255).astype(np.uint8)
        j = lambda s: r.uniform(-s, s)  # noqa: E731
        ox, oy = (bw - w) / 2, (bh - h) / 2
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        s = w * .06
        dst = np.float32([[ox + j(s), oy + j(s)], [ox + w + j(s), oy + j(s)], [ox + w + j(s), oy + h + j(s)], [ox + j(s), oy + h + j(s)]])
        M = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(a, M, (bw, bh), borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (bw, bh))
        a = np.where(mask[..., None] > 0, warped, bg)
        boxes = _warp_boxes(boxes, M, bw, bh)
        # light gradient + shadow
        yy, xx = np.mgrid[0:bh, 0:bw].astype(np.float32)
        gx, gy = r.uniform(-1, 1), r.uniform(-1, 1)
        light = 1 - r.uniform(.1, .35) * ((xx / bw - .5) * gx + (yy / bh - .5) * gy + .5)
        if r.random() < .5:
            cx, cy = r.uniform(0, bw), r.uniform(0, bh)
            light *= 1 - r.uniform(.15, .35) * np.exp(-(((xx - cx) / (bw * .35)) ** 2 + ((yy - cy) / (bh * .35)) ** 2))
        a = np.clip(a.astype(np.float32) * light[..., None] * np.array([1, r.uniform(.95, 1), r.uniform(.85, 1)]), 0, 255).astype(np.uint8)
        k = r.choice([1, 3, 3, 5])
        a = cv2.GaussianBlur(a, (k, k), 0)
        h, w = bh, bw
    out = Image.fromarray(a)
    return out, boxes
