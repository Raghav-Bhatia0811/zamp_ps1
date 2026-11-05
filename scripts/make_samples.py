"""Generate the demo invoices in samples/.

Three vendor layouts (classic, modern, letterhead), and five kinds of paper:
clean digital PDFs, a scanned copy, a phone photo (angled, shadowed, JPG), a
fax too faint to read, and an upside-down scan with a PAID stamp and a pen
edit next to the total. Extraction has to cope with all of them.
Run:  .venv/bin/python scripts/make_samples.py
"""
import json
import random
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter
from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"
W, H = A4


def inr(x: float) -> str:
    neg = x < 0
    x = abs(x)
    whole, frac = f"{x:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + f"{whole}.{frac}"


def totals(inv):
    sub = sum(q * r for _, _, q, r in inv["lines"])
    tax = round(sub * inv["rate"] / 100, 2)
    return sub, tax, sub + tax


BUYER = ["Halden Foods Pvt Ltd", "Plot 14, MIDC Bhosari", "Pune, Maharashtra 411026", "GSTIN: 27AAACH1234F1Z3"]


def tax_rows(inv, tax):
    if inv["tax_mode"] == "igst":
        return [(f"IGST @ {inv['rate']}%", tax)]
    half = round(tax / 2, 2)
    return [(f"CGST @ {inv['rate'] / 2:g}%", half), (f"SGST @ {inv['rate'] / 2:g}%", tax - half)]


def draw_lines_table(c, inv, y, *, grid: bool, header_fill=None):
    cols = {"sn": 52, "desc": 72, "hsn": 318, "qty": 382, "rate": 466, "amt": 545}
    if header_fill:
        c.setFillColor(header_fill)
        c.rect(44, y - 6, W - 88, 20, stroke=0, fill=1)
        c.setFillColor(black)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(cols["sn"], y, "#")
    c.drawString(cols["desc"], y, "Description")
    c.drawString(cols["hsn"], y, "HSN/SAC")
    c.drawRightString(cols["qty"], y, "Qty")
    c.drawRightString(cols["rate"], y, "Rate")
    c.drawRightString(cols["amt"], y, "Amount")
    y -= 8
    if grid:
        c.setLineWidth(0.6)
        c.line(44, y, W - 44, y)
    y -= 16
    c.setFont("Helvetica", 9)
    for i, (desc, hsn, q, r) in enumerate(inv["lines"], 1):
        c.drawString(cols["sn"], y, str(i))
        c.drawString(cols["desc"], y, desc)
        c.drawString(cols["hsn"], y, hsn)
        c.drawRightString(cols["qty"], y, f"{q:g}")
        c.drawRightString(cols["rate"], y, inr(r))
        c.drawRightString(cols["amt"], y, inr(q * r))
        y -= 22
        if grid:
            c.setLineWidth(0.3)
            c.line(44, y + 14, W - 44, y + 14)
    return y


def draw_totals(c, inv, y, label_x=330, value_x=545, total_label="Grand Total"):
    sub, tax, tot = totals(inv)
    c.setFont("Helvetica", 9)
    c.drawString(label_x, y, "Sub Total")
    c.drawRightString(value_x, y, inr(sub))
    for lbl, amt in tax_rows(inv, tax):
        y -= 16
        c.drawString(label_x, y, lbl)
        c.drawRightString(value_x, y, inr(amt))
    y -= 20
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(label_x, y, f"{total_label} (INR)")
    c.drawRightString(value_x, y, inr(tot))
    inv["_total_at"] = (value_x, y)
    return y


def draw_bank(c, inv, y):
    b = inv["bank"]
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(52, y, "Bank details for payment")
    c.setFont("Helvetica", 8.5)
    c.drawString(52, y - 14, f"Bank: {b['bank']}")
    c.drawString(52, y - 27, f"A/c No: {b['account']}")
    c.drawString(52, y - 40, f"IFSC: {b['ifsc']}")
    y -= 58
    if inv.get("note"):
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(52, y, inv["note"])
        y -= 14
    return y


# ---------- layout A: classic ----------
def layout_classic(c, inv):
    v = inv["vendor"]
    c.setFont("Helvetica-Bold", 16)
    c.drawString(52, H - 64, v["name"])
    c.setFont("Helvetica", 8.5)
    for i, ln in enumerate(v["address"] + [f"GSTIN: {v['gstin']}", f"{v['phone']}  |  {v['email']}"]):
        c.drawString(52, H - 80 - i * 12, ln)
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(W - 52, H - 64, "TAX INVOICE")
    c.setFont("Helvetica", 9)
    meta = [("Invoice No:", inv["number"]), ("Invoice Date:", inv["date"])]
    if inv.get("po"):
        meta.append(("PO Number:", inv["po"]))
    meta.append(("Due Date:", inv["due"]))
    for i, (k, val) in enumerate(meta):
        c.drawRightString(W - 140, H - 86 - i * 14, k)
        c.drawRightString(W - 52, H - 86 - i * 14, val)
    y = H - 170
    c.setFont("Helvetica-Bold", 9)
    c.drawString(52, y, "Bill To")
    c.setFont("Helvetica", 9)
    for i, ln in enumerate(BUYER):
        c.drawString(52, y - 14 - i * 12, ln)
    if inv.get("memo"):
        c.drawString(330, y - 14, inv["memo"])
    y -= 90
    y = draw_lines_table(c, inv, y, grid=True)
    y = draw_totals(c, inv, y - 10)
    draw_bank(c, inv, y - 50)
    c.setFont("Helvetica", 7.5)
    c.drawString(52, 52, "This is a computer generated invoice. Subject to Pune jurisdiction.")


# ---------- layout B: modern band ----------
def layout_modern(c, inv):
    v = inv["vendor"]
    c.setFillColor(HexColor("#1d2b45"))
    c.rect(0, H - 110, W, 110, stroke=0, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont("Helvetica-Bold", 18)
    c.drawString(52, H - 58, v["name"])
    c.setFont("Helvetica", 8.5)
    c.drawString(52, H - 76, ", ".join(v["address"]))
    c.drawString(52, H - 90, f"GSTIN {v['gstin']}   {v['email']}")
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(W - 52, H - 62, "Invoice")
    c.setFillColor(black)
    y = H - 150
    c.setFont("Helvetica", 9)
    left = [("Invoice #", inv["number"]), ("Issued", inv["date"]), ("Payment due", inv["due"])]
    right = [("Purchase Order", inv["po"] or "-"), ("Billing period", inv.get("period", "-"))]
    for i, (k, val) in enumerate(left):
        c.setFont("Helvetica-Bold", 8.5); c.drawString(52, y - i * 15, k)
        c.setFont("Helvetica", 9); c.drawString(140, y - i * 15, val)
    for i, (k, val) in enumerate(right):
        c.setFont("Helvetica-Bold", 8.5); c.drawString(330, y - i * 15, k)
        c.setFont("Helvetica", 9); c.drawString(420, y - i * 15, val)
    y -= 62
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(52, y, "Billed to")
    c.setFont("Helvetica", 9)
    c.drawString(52, y - 13, ", ".join(BUYER[:3]))
    c.drawString(52, y - 26, BUYER[3])
    if inv.get("memo"):
        c.drawString(52, y - 39, inv["memo"])
    y -= 72
    y = draw_lines_table(c, inv, y, grid=False, header_fill=HexColor("#e9edf4"))
    c.setLineWidth(0.5)
    c.line(330, y + 6, W - 44, y + 6)
    y = draw_totals(c, inv, y - 10, total_label="Total Amount Due")
    draw_bank(c, inv, y - 50)


# ---------- layout C: letterhead ----------
def layout_letterhead(c, inv):
    v = inv["vendor"]
    c.setFont("Helvetica-Bold", 17)
    c.drawCentredString(W / 2, H - 60, v["name"].upper())
    c.setFont("Helvetica", 8.5)
    c.drawCentredString(W / 2, H - 76, ", ".join(v["address"]))
    c.drawCentredString(W / 2, H - 89, f"GSTIN: {v['gstin']}  Tel: {v['phone']}")
    c.setLineWidth(1.2)
    c.line(52, H - 100, W - 52, H - 100)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(W / 2, H - 122, "TAX INVOICE")
    y = H - 152
    c.setFont("Helvetica", 9)
    c.drawString(52, y, f"Bill No.: {inv['number']}")
    c.drawRightString(W - 52, y, f"Dated: {inv['date']}")
    if inv.get("po"):
        c.drawString(52, y - 15, f"Your P.O. Ref: {inv['po']}")
    c.drawRightString(W - 52, y - 15, f"Due by: {inv['due']}")
    y -= 50
    c.drawString(52, y, "To,")
    for i, ln in enumerate(BUYER):
        c.drawString(52, y - 13 - i * 12, ln)
    y -= 86
    y = draw_lines_table(c, inv, y, grid=True)
    y = draw_totals(c, inv, y - 10, total_label="Total")
    draw_bank(c, inv, y - 50)
    c.setFont("Helvetica-Oblique", 8)
    c.drawRightString(W - 52, 90, f"For {v['name']}")
    c.drawRightString(W - 52, 64, "Authorised Signatory")


# ---------- ink added on paper after printing: stamps and pen
HAND_FONT = None


def _hand_font():
    global HAND_FONT
    if HAND_FONT is None:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        HAND_FONT = "Helvetica-Oblique"
        for f in ("/usr/share/fonts/truetype/tlwg/Purisa.ttf", "/usr/share/fonts/truetype/tlwg/Purisa-Oblique.ttf"):
            if Path(f).exists():
                pdfmetrics.registerFont(TTFont("Hand", f))
                HAND_FONT = "Hand"
                break
    return HAND_FONT


def draw_marks(c, inv):
    marks = inv.get("marks") or {}
    tx, ty = inv.get("_total_at", (545, 300))
    if marks.get("stamp"):
        c.saveState()
        c.translate(tx - 250, ty - 70)
        c.rotate(14)
        red = HexColor("#c0262d")
        c.setStrokeColor(red)
        c.setFillColor(red)
        c.setFillAlpha(0.85)
        c.setStrokeAlpha(0.85)
        c.setLineWidth(3)
        c.roundRect(0, 0, 150, 58, 6, stroke=1, fill=0)
        c.setLineWidth(1)
        c.roundRect(5, 5, 140, 48, 4, stroke=1, fill=0)
        c.setFont("Helvetica-Bold", 28)
        c.drawCentredString(75, 22, marks["stamp"])
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(75, 10, marks.get("stamp_sub", ""))
        c.restoreState()
    if marks.get("pen"):
        blue = HexColor("#1f3a9a")
        c.saveState()
        c.setStrokeColor(blue)
        c.setFillColor(blue)
        c.setLineWidth(1.6)
        c.line(tx - 62, ty + 3, tx + 4, ty + 5)          # strike through the printed total
        c.translate(tx - 120, ty - 26)
        c.rotate(-5)
        c.setFont(_hand_font(), 15)
        c.drawString(0, 0, marks["pen"])
        c.restoreState()


def draw(c, inv):
    inv["layout"](c, inv)
    draw_marks(c, inv)


def load_vendor(master, vid, address):
    v = next(x for x in master["vendors"] if x["id"] == vid)
    return {"name": v["name"], "gstin": v["gstin"], "address": address,
            "phone": v["contact"]["phone"], "email": v["contact"]["email"]}, v["bank"]


def build_samples():
    master = json.loads((ROOT / "data" / "master.json").read_text())
    nw, nw_bank = load_vendor(master, "V001", ["Unit 7, Kasturi Arcade", "Baner Road, Pune 411045"])
    bl, bl_bank = load_vendor(master, "V002", ["No. 42, 3rd Cross, Peenya Industrial Area", "Bengaluru 560058"])
    ap, ap_bank = load_vendor(master, "V003", ["Office 302, Siddhi Towers", "Kothrud, Pune 411038"])
    ke, ke_bank = load_vendor(master, "V004", ["B-118, Okhla Industrial Area Phase II", "New Delhi 110020"])
    me, me_bank = load_vendor(master, "V005", ["Gala 9, Shah Industrial Estate", "Andheri East, Mumbai 400093"])

    return [
        dict(file="01_happy_path_northwind.pdf", layout=layout_classic, vendor=nw, bank=nw_bank,
             number="INV-2026-0412", date="28 Oct 2026", due="27 Nov 2026", po="PO-1001",
             tax_mode="cgst_sgst", rate=18,
             lines=[("A4 copier paper 75gsm (box of 5 reams)", "4802", 40, 1450.00),
                    ("Toner cartridge HP 88A", "8443", 12, 3200.00),
                    ("Whiteboard markers (pack of 10)", "9608", 25, 420.00)]),
        dict(file="02_split_po_brightline_part2.pdf", layout=layout_modern, vendor=bl, bank=bl_bank,
             number="BL/Q3/02", date="29 Sep 2026", due="13 Nov 2026", po="PO-1002",
             period="Sep 2026 (instalment 2 of 3)", tax_mode="igst", rate=18,
             lines=[("FTL freight Pune to Bengaluru, 32ft MXL", "996511", 8, 20000.00)]),
        dict(file="03_split_po_brightline_part3.pdf", layout=layout_modern, vendor=bl, bank=bl_bank,
             number="BL/Q3/03", date="31 Oct 2026", due="15 Dec 2026", po="PO-1002",
             period="Oct 2026 (instalment 3 of 3)", tax_mode="igst", rate=18,
             lines=[("FTL freight Pune to Bengaluru, 32ft MXL", "996511", 6, 20000.00)]),
        dict(file="04_duplicate_apex.pdf", layout=layout_letterhead, vendor=ap, bank=ap_bank,
             number="AFS/0042", date="09-10-2026", due="08-11-2026", po="PO-1003",
             tax_mode="cgst_sgst", rate=18,
             lines=[("Housekeeping services, September 2026", "998533", 1, 84000.00),
                    ("Pest control treatment", "998531", 1, 6500.00)]),
        dict(file="05_scanned_no_po_kestrel.pdf", layout=layout_classic, vendor=ke, bank=ke_bank, scan=True,
             number="KIT/2026/1187", date="30 Oct 2026", due="29 Nov 2026", po=None,
             memo="Ref: your email order dated 12 Oct 2026", tax_mode="igst", rate=18,
             lines=[("Dell Latitude 5450 i5 16GB 512GB", "8471", 6, 78500.00),
                    ("Dell docking station WD19S", "8473", 6, 14200.00)]),
        dict(file="06_price_variance_meridian.pdf", layout=layout_modern, vendor=me, bank=me_bank,
             number="MPC-7781", date="27 Oct 2026", due="26 Nov 2026", po="PO-1005",
             period="Oct 2026", tax_mode="cgst_sgst", rate=18,
             lines=[("Product catalogue print, A4 32pp", "4911", 2000, 45.00),
                    ("Point-of-sale standees", "4911", 45, 660.00)]),
        dict(file="07_bank_change_northwind.pdf", layout=layout_classic, vendor=nw,
             bank={"bank": "ICICI Bank", "account": "001905556789", "ifsc": "ICIC0000019"},
             note="Please note: our bank details have changed. Kindly update your records.",
             number="INV-2026-0447", date="01 Nov 2026", due="01 Dec 2026", po="PO-1006",
             tax_mode="cgst_sgst", rate=18,
             lines=[("Filter coffee powder 1kg", "0901", 30, 640.00),
                    ("Paper cups 150ml (pack of 100)", "4823", 60, 185.00),
                    ("Assorted tea bags (box of 100)", "0902", 20, 390.00)]),
        # --- messy documents
        dict(file="08_phone_photo_northwind.jpg", layout=layout_modern, vendor=nw, bank=nw_bank, post="photo",
             number="INV-2026-0463", date="03 Nov 2026", due="03 Dec 2026", po="PO-1008",
             period="Nov 2026", tax_mode="cgst_sgst", rate=18,
             lines=[("Photocopy sheets A4 75 GSM (carton, 5 reams)", "4802", 20, 1450.00),
                    ("Ballpoint pen, blue ink - pack of 50", "9608", 30, 260.00),
                    ("Stapler 24/6, heavy-duty metal", "8472", 10, 540.00)]),
        dict(file="09_illegible_fax_kestrel.pdf", layout=layout_classic, vendor=ke, bank=ke_bank, post="fax",
             number="KIT/2026/1203", date="02 Nov 2026", due="02 Dec 2026", po="PO-1007",
             tax_mode="igst", rate=18,
             lines=[("Annual maintenance contract, 40 endpoints", "998713", 1, 120000.00)]),
        dict(file="10_stamped_upside_down_apex.pdf", layout=layout_letterhead, vendor=ap, bank=ap_bank, post="flip",
             number="AFS/0051", date="31-10-2026", due="30-11-2026", po="PO-1009",
             tax_mode="cgst_sgst", rate=18, marks={"stamp": "PAID", "stamp_sub": "14 OCT 2026", "pen": "Revised 96,500 /-"},
             lines=[("Housekeeping services, October 2026", "998533", 1, 84000.00),
                    ("Pest control treatment", "998531", 1, 6500.00)]),
    ]


def scanify(pdf_path: Path):
    """Turn a clean PDF into an image-only 'scanned' PDF: no text layer, slight skew, noise."""
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", "170", "-gray", "-png", str(pdf_path), f"{td}/p"], check=True)
        img = Image.open(next(Path(td).glob("p*.png"))).convert("L")
        img = img.rotate(0.45, resample=Image.BICUBIC, expand=False, fillcolor=255)
        rnd = random.Random(7)
        px = img.load()
        w, h = img.size
        for _ in range(w * h // 160):
            x, y = rnd.randrange(w), rnd.randrange(h)
            px[x, y] = max(0, px[x, y] - rnd.randint(40, 120))
        img = img.filter(ImageFilter.GaussianBlur(0.45))
        # warm-grey paper tone, like a photocopy
        img = Image.merge("RGB", [img.point(lambda v: min(255, v * 0.97 + 4)), img.point(lambda v: v * 0.96 + 3), img.point(lambda v: v * 0.93)])
        img.save(pdf_path, "PDF", resolution=170)


def _render(pdf_path: Path, dpi: int) -> Image.Image:
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf_path), f"{td}/p"], check=True)
        return Image.open(next(Path(td).glob("p*.png"))).convert("RGB")


def photoify(pdf_path: Path, out_jpg: Path, seed: int = 3):
    """A phone photo of the printed page: on a desk, at an angle, with a shadow across it."""
    import cv2
    import numpy as np
    page = np.array(_render(pdf_path, 200))
    h, w = page.shape[:2]
    rng = np.random.default_rng(seed)
    bw, bh = int(w * 1.28), int(h * 1.2)
    yy, xx = np.mgrid[0:bh, 0:bw].astype(np.float32)
    grain = (np.sin(xx / 9 + np.sin(yy / 60) * 3) * 10 + rng.normal(0, 6, (bh, bw))).astype(np.float32)
    desk = np.clip(np.stack([118 + grain, 84 + grain * .8, 52 + grain * .6], -1), 0, 255).astype(np.uint8)
    ox, oy = (bw - w) / 2, (bh - h) / 2
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[ox + 70, oy + 40], [ox + w - 30, oy - 20], [ox + w + 40, oy + h - 30], [ox - 40, oy + h + 25]])
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(page, M, (bw, bh), flags=cv2.INTER_CUBIC)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (bw, bh))
    img = np.where(mask[..., None] > 0, warped, desk).astype(np.float32)
    light = 1 - 0.22 * (xx / bw) - 0.10 * (yy / bh)                       # window light from the left
    shadow = 1 - 0.38 * np.exp(-(((xx - bw * .78) / (bw * .22)) ** 2 + ((yy - bh * .35) / (bh * .5)) ** 2))  # phone/hand shadow
    img *= (light * shadow)[..., None] * np.array([1.0, .97, .9], np.float32)
    img = cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (3, 3), 0)
    Image.fromarray(img).save(out_jpg, "JPEG", quality=84)


def faxify(pdf_path: Path, seed: int = 5):
    """A fax that came through badly: 60 dpi, smeared, faded, streaked."""
    import numpy as np
    img = _render(pdf_path, 200).convert("L")
    w, h = img.size
    small = img.resize((w * 60 // 200, h * 60 // 200), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.3))
    a = np.array(small.resize((w * 100 // 200, h * 100 // 200), Image.BILINEAR), dtype=np.float32)
    rng = np.random.default_rng(seed)
    a = 255 - (255 - a) * 0.42                                            # faded toner
    a += rng.normal(0, 22, a.shape)
    for x in rng.choice(a.shape[1], 14, replace=False):                   # vertical streaks
        a[:, x:x + 2] -= rng.uniform(40, 90)
    for y in rng.choice(a.shape[0], 30, replace=False):                   # dropped scan lines
        a[y:y + 2, :] = 255
    a = np.clip(a, 0, 255).astype(np.uint8)
    Image.fromarray(a).save(pdf_path, "PDF", resolution=100)


def flip_scan(pdf_path: Path, seed: int = 9):
    """Scanned upside down on a flatbed, with a little skew and noise."""
    img = _render(pdf_path, 170)
    img = img.rotate(180.6, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    rnd = random.Random(seed)
    px = img.load()
    w, h = img.size
    for _ in range(w * h // 220):
        x, y = rnd.randrange(w), rnd.randrange(h)
        v = max(0, px[x, y][0] - rnd.randint(40, 110))
        px[x, y] = (v, v, v)
    img.filter(ImageFilter.GaussianBlur(0.4)).save(pdf_path, "PDF", resolution=170)


def render_pdf(inv, path: Path):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"Invoice {inv['number']}")
    c.setAuthor(inv["vendor"]["name"])
    draw(c, inv)
    c.showPage()
    c.save()


def main():
    OUT.mkdir(exist_ok=True)
    for inv in build_samples():
        path = OUT / inv["file"]
        if inv.get("post") == "photo":
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td) / "page.pdf"
                render_pdf(inv, tmp)
                photoify(tmp, path)
        else:
            render_pdf(inv, path)
            if inv.get("scan"):
                scanify(path)
            elif inv.get("post") == "fax":
                faxify(path)
            elif inv.get("post") == "flip":
                flip_scan(path)
        print("wrote", path.name, "total", inr(totals(inv)[2]))


if __name__ == "__main__":
    main()
