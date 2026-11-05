"""Document vision: get a messy page into a state where it can be read reliably.

Stage "Clean up image" (assess -> enhance -> re-assess -> legibility gate):
  * orientation   Tesseract OSD finds pages scanned sideways or upside down
  * page finding  a phone photo is cropped to the sheet and flattened (4-point warp)
  * illumination  shadows and uneven light are divided out (background estimate)
  * denoise       speckle and sensor noise removed (median / non-local means)
  * deskew        text lines straightened (projection-profile search)
  * resolution    low-dpi scans upscaled toward ~300 dpi for OCR
  * legibility    measured, not guessed: OCR the cleaned page and score word confidence

Stage "Find regions": a YOLO detector (trained on synthetic invoices, exported to
ONNX, run with onnxruntime on CPU) finds the vendor block, invoice meta, bill-to,
line table, totals, bank block, stamps, signatures and handwriting. Regions are
re-read on their own at higher scale, and stamps/handwriting feed the controls.

Nothing here decides anything. It produces measurements and evidence that the
rules in rules.py use, and images the reviewer can look at.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
LAYOUT_MODEL = Path(os.environ.get("LAYOUT_MODEL", ROOT / "models" / "layout-yolo.onnx"))
LAYOUT_CLASSES = ["vendor_block", "invoice_meta", "bill_to", "line_items", "totals",
                  "bank_details", "stamp", "signature", "handwriting"]
LAYOUT_LABEL = {"vendor_block": "Vendor", "invoice_meta": "Invoice details", "bill_to": "Bill to",
                "line_items": "Line items", "totals": "Totals", "bank_details": "Bank details",
                "stamp": "Stamp", "signature": "Signature", "handwriting": "Handwriting"}
LAYOUT_COLOR = {"vendor_block": (52, 101, 164), "invoice_meta": (78, 154, 6), "bill_to": (117, 80, 123),
                "line_items": (196, 160, 0), "totals": (204, 0, 0), "bank_details": (6, 152, 154),
                "stamp": (220, 50, 120), "signature": (143, 89, 2), "handwriting": (245, 121, 0)}
ENHANCE = os.environ.get("VISION_ENHANCE", "1") != "0"   # evaluate.py turns this off for the ablation
REGION_OCR = os.environ.get("REGION_OCR", "1") != "0"
MAX_PAGES = 4
OCR_DPI = 300
A4_W_IN = 8.27

IMAGE_MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png", b"II*\x00": "tif", b"MM\x00*": "tif", b"RIFF": "webp"}


def sniff(data: bytes) -> str | None:
    """'pdf', 'jpg', 'png', 'tif', 'webp' or None."""
    if data[:5] == b"%PDF-" or data[:4] == b"%PDF":
        return "pdf"
    for magic, kind in IMAGE_MAGIC.items():
        if data.startswith(magic):
            if kind == "webp" and data[8:12] != b"WEBP":
                continue
            return kind
    return None


# ------------------------------------------------------------------ loading
def load_pages(path: Path, kind: str, dpi: int = 200) -> list[np.ndarray]:
    """Every page as an RGB uint8 array (max MAX_PAGES)."""
    if kind == "pdf":
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["pdftoppm", "-r", str(dpi), "-l", str(MAX_PAGES), "-png", str(path), f"{td}/p"],
                           check=True, capture_output=True)
            files = sorted(Path(td).glob("p*.png"))
            return [np.array(Image.open(f).convert("RGB")) for f in files]
    im = Image.open(path)
    pages = []
    for i in range(getattr(im, "n_frames", 1)):
        if i >= MAX_PAGES:
            break
        im.seek(i)
        fr = ImageOps.exif_transpose(im.copy()).convert("RGB")   # phone photos carry rotation in EXIF
        pages.append(np.array(fr))
    return pages


def _limit(img: np.ndarray, long_edge: int) -> np.ndarray:
    h, w = img.shape[:2]
    k = long_edge / max(h, w)
    return cv2.resize(img, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else img


def gray(img):
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


# ------------------------------------------------------------------ measurements
def sharpness(g: np.ndarray) -> float:
    """Variance of the Laplacian at a fixed working width (higher = sharper)."""
    k = 1240 / g.shape[1]
    s = cv2.resize(g, (1240, round(g.shape[0] * k)), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR)
    return float(cv2.Laplacian(s, cv2.CV_64F).var())


def contrast(g: np.ndarray) -> float:
    lo, hi = np.percentile(g, [2, 98])
    return float(hi - lo)


def unevenness(g: np.ndarray) -> float:
    """Spread of the paper's brightness across the page (shadows, gradients). 0 = perfectly even."""
    s = _limit(g, 600)
    bg = cv2.morphologyEx(s, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    bg = cv2.GaussianBlur(bg, (0, 0), 8)
    m = bg > np.percentile(bg, 30)
    return float(np.std(bg[m])) if m.any() else 0.0


def noise_level(g: np.ndarray) -> float:
    """Median absolute residual after a 3x3 median filter, on background pixels."""
    s = _limit(g, 1400)
    resid = np.abs(s.astype(np.int16) - cv2.medianBlur(s, 3).astype(np.int16))
    bg = s > np.percentile(s, 60)
    return float(np.mean(resid[bg])) if bg.any() else 0.0


def estimate_skew(g: np.ndarray) -> float:
    """Text-line angle in degrees via projection-profile search (coarse then fine)."""
    s = _limit(g, 1000)
    _, b = cv2.threshold(s, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = b.shape
    c = (w / 2, h / 2)

    def score(a):
        M = cv2.getRotationMatrix2D(c, a, 1.0)
        r = cv2.warpAffine(b, M, (w, h), flags=cv2.INTER_NEAREST)
        p = r.sum(axis=1, dtype=np.float64)
        return float(np.sum(np.diff(p) ** 2))

    coarse = max(np.arange(-8, 8.01, 1.0), key=score)
    fine = max(np.arange(coarse - 1, coarse + 1.01, 0.1), key=score)
    return round(float(fine), 2)


def osd_rotation(g: np.ndarray) -> tuple[int, float]:
    """(degrees clockwise to rotate to make the page upright, confidence) from Tesseract OSD."""
    s = _limit(g, 1800)
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        cv2.imwrite(f.name, s)
        out = subprocess.run(["tesseract", f.name, "stdout", "--psm", "0"], capture_output=True, text=True)
    m = re.search(r"Rotate:\s*(\d+)", out.stdout)
    cm = re.search(r"Orientation confidence:\s*([\d.]+)", out.stdout)
    return (int(m.group(1)) if m else 0), (float(cm.group(1)) if cm else 0.0)


def rotate_upright(img, deg_cw: int):
    return {90: cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), 180: cv2.rotate(img, cv2.ROTATE_180),
            270: cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)}.get(deg_cw, img)


def _quad_from_mask(mask, s_shape):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    area, full = cv2.contourArea(c), s_shape[0] * s_shape[1]
    if not 0.2 * full < area < 0.95 * full:
        return None
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    quad = None
    for eps in (0.02, 0.03, 0.045, 0.06):
        ap = cv2.approxPolyDP(hull, eps * peri, True)
        if len(ap) == 4:
            quad = ap.reshape(4, 2).astype(np.float32)
            break
    if quad is None:
        quad = cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)
    x, y, w, h = cv2.boundingRect(quad.astype(np.int32))
    if w > 0.97 * s_shape[1] and h > 0.97 * s_shape[0]:
        return None   # the "page" is the whole frame: a flatbed scan, not a photo
    return quad


def find_page_quad(img: np.ndarray) -> np.ndarray | None:
    """Corners of the sheet of paper in a photo, or None if the page already fills the frame.

    First choice: model the background (desk, hand, table) from the image border and
    take everything that is not background. That keeps dark letterhead bands, which
    a "find the white paper" rule would cut off. Fallback: bright, unsaturated paper."""
    s = _limit(img, 900)
    k = img.shape[1] / s.shape[1]
    lab = cv2.cvtColor(cv2.GaussianBlur(s, (5, 5), 0), cv2.COLOR_RGB2LAB).astype(np.float32)
    b = 10
    border = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3), lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
    mu = np.median(border, 0)
    spread = np.percentile(np.abs(border - mu), 90, axis=0) + 4
    quad = None
    if spread.max() < 40:   # border looks like one background, not the page itself
        d = np.sqrt((((lab - mu) / spread) ** 2).sum(-1))
        mask = (d > 2.5).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8))
        quad = _quad_from_mask(mask, s.shape)
    if quad is None:
        hsv = cv2.cvtColor(s, cv2.COLOR_RGB2HSV)
        paper = ((hsv[..., 2].astype(np.int16) - hsv[..., 1]) > 0).astype(np.uint8) * 255
        _, bright = cv2.threshold(cv2.GaussianBlur(gray(s), (7, 7), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(cv2.bitwise_and(paper, bright), cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
        quad = _quad_from_mask(mask, s.shape)
    return None if quad is None else _order(quad * k)


def invert_dark_bands(g: np.ndarray) -> tuple[np.ndarray, int]:
    """Letterheads often print white text on a dark band; OCR wants dark on light. Flip those areas."""
    k = max(31, (min(g.shape) // 25) | 1)
    local = cv2.blur(g, (k, k))
    dark = (local < 100).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dark)
    out, flipped = g.copy(), 0
    page = g.shape[0] * g.shape[1]
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a > 0.01 * page and w > 0.3 * g.shape[1]:
            m = lab == i
            m = cv2.dilate(m.astype(np.uint8), np.ones((k // 3, k // 3), np.uint8)).astype(bool)
            out[m] = 255 - g[m]
            flipped += 1
    return out, flipped


def _order(q):
    s, d = q.sum(1), np.diff(q, axis=1).ravel()
    return np.float32([q[np.argmin(s)], q[np.argmin(d)], q[np.argmax(s)], q[np.argmax(d)]])


def warp_quad(img, q):
    tl, tr, br, bl = q
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    M = cv2.getPerspectiveTransform(q, np.float32([[0, 0], [w, 0], [w, h], [0, h]]))
    out = cv2.warpPerspective(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    m = max(2, round(min(w, h) * 0.008))   # trim the paper edge so it doesn't OCR as "|"
    return out[m:h - m, m:w - m]


def flatten_light(g: np.ndarray) -> np.ndarray:
    """Divide by an estimate of the paper background: removes shadows and gradients."""
    k = max(15, (min(g.shape) // 40) | 1)
    bg = cv2.morphologyEx(g, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 2)
    return cv2.divide(g, bg, scale=255)


def stretch(g):
    lo, hi = np.percentile(g, [1, 99.5])
    if hi - lo < 10:
        return g
    return np.clip((g.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ OCR
def ocr(g: np.ndarray, psm: int = 6, dpi: int | None = None) -> dict:
    """Tesseract TSV -> words with confidences and boxes, plus layout-ish text.

    Words far apart on a line are joined with a double space so the column-aware
    patterns in extract.py can tell label from value.
    """
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        cv2.imwrite(f.name, g)
        cmd = ["tesseract", f.name, "stdout", "--psm", str(psm)]
        if dpi:
            cmd += ["--dpi", str(dpi)]
        out = subprocess.run(cmd + ["tsv"], capture_output=True, text=True).stdout
    words = []
    for ln in out.splitlines()[1:]:
        p = ln.split("\t")
        if len(p) < 12 or not p[11].strip():
            continue
        try:
            conf = float(p[10])
        except ValueError:
            continue
        if conf < 0:
            continue
        words.append({"block": int(p[2]), "par": int(p[3]), "line": int(p[4]), "x": int(p[6]), "y": int(p[7]),
                      "w": int(p[8]), "h": int(p[9]), "conf": conf, "text": p[11]})
    lines, cur, key = [], [], None
    for wd in words:
        k = (wd["block"], wd["par"], wd["line"])
        if k != key and cur:
            lines.append(cur)
            cur = []
        key = k
        cur.append(wd)
    if cur:
        lines.append(cur)
    lines.sort(key=lambda L: (np.median([w["y"] for w in L]), L[0]["x"]))
    text_lines = []
    for L in lines:
        L.sort(key=lambda w: w["x"])
        hgt = np.median([w["h"] for w in L]) or 10
        s = L[0]["text"]
        for a, b in zip(L, L[1:]):
            gap = b["x"] - (a["x"] + a["w"])
            s += ("   " if gap > 1.6 * hgt else " ") + b["text"]
        text_lines.append(s)
    real = [w for w in words if len(re.sub(r"\W", "", w["text"])) >= 2]
    mean_conf = float(np.mean([w["conf"] for w in real])) if real else 0.0
    return {"text": "\n".join(text_lines), "words": [{k: w[k] for k in ("text", "conf", "x", "y", "w", "h")} for w in words],
            "mean_conf": round(mean_conf, 1), "n_words": len(real)}


def legibility_grade(mean_conf: float, n_words: int) -> tuple[str, int]:
    """(grade, score 0-100). Score blends OCR confidence with how much text was found."""
    coverage = min(1.0, n_words / 60)
    score = round(mean_conf * (0.55 + 0.45 * coverage))
    if score >= 80:
        return "good", score
    if score >= 66:
        return "fair", score
    if score >= 50:
        return "poor", score
    return "unreadable", score


# ------------------------------------------------------------------ the clean-up stage
def assess(img: np.ndarray) -> dict:
    g = gray(img)
    h, w = g.shape
    return {"sharpness": round(sharpness(g), 1), "contrast": round(contrast(g), 1), "uneven": round(unevenness(g), 1), "noise": round(noise_level(g), 2),
            "skew_deg": estimate_skew(g), "width_px": w, "height_px": h, "dpi_est": round(min(w, h) / A4_W_IN)}


def clean_page(img: np.ndarray, source: str) -> tuple[np.ndarray, np.ndarray, dict]:
    """source: 'scan' (image-only PDF) or 'photo' (uploaded picture).

    Returns (clean grayscale for OCR, colour image with the same geometry for
    stamps and previews, report). Photometric fixes only touch the grayscale."""
    steps, before = [], assess(img)
    col = img
    if not ENHANCE:
        return gray(col), col, {"before": before, "after": before, "steps": ["Clean-up turned off (VISION_ENHANCE=0)"],
                                "orientation": 0, "page_found": False}

    # 1. sheet of paper in a photo -> crop and flatten perspective
    quad = find_page_quad(col) if source == "photo" else None
    if quad is not None:
        col = warp_quad(col, quad)
        steps.append("Found the page in the photo and flattened its perspective (4-point warp)")
    g = gray(col)

    # 2. orientation (sideways / upside-down)
    rot, rconf = osd_rotation(g)
    rot = rot if rot and rconf >= 1.5 else 0
    if rot:
        g, col = rotate_upright(g, rot), rotate_upright(col, rot)
        steps.append(f"Turned the page {rot}° to make it upright (Tesseract OSD, confidence {rconf:.1f})")

    # 3. white-on-dark bands, then uneven light, shadows, paper tone
    g, nb = invert_dark_bands(g)
    if nb:
        steps.append(f"Inverted {nb} dark header band{'s' if nb > 1 else ''} so white text reads as dark on light")
    if before["contrast"] < 235 or source == "photo":
        g = flatten_light(g)
        steps.append("Removed shadows and uneven lighting (background division)")

    # 4. noise
    nz = noise_level(g)
    if nz > 2.2:
        g = cv2.medianBlur(g, 3)
        if nz > 4:
            g = cv2.fastNlMeansDenoising(g, None, h=12, templateWindowSize=7, searchWindowSize=15)
        steps.append(f"Removed speckle and sensor noise (noise level {nz:.1f})")

    # 5. skew
    sk = estimate_skew(g)
    if abs(sk) >= 0.25:
        hh, ww = g.shape
        M = cv2.getRotationMatrix2D((ww / 2, hh / 2), sk, 1.0)
        g = cv2.warpAffine(g, M, (ww, hh), flags=cv2.INTER_CUBIC, borderValue=255)
        col = cv2.warpAffine(col, M, (ww, hh), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
        steps.append(f"Straightened text lines by {sk:+.1f}°")

    # 6. resolution: OCR wants ~300 dpi
    dpi = min(g.shape) / A4_W_IN
    if dpi < 250:
        k = min(OCR_DPI / dpi, 3.0)
        g = cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        col = cv2.resize(col, (g.shape[1], g.shape[0]), interpolation=cv2.INTER_CUBIC)
        steps.append(f"Upscaled from ~{dpi:.0f} dpi to ~{dpi * k:.0f} dpi for OCR")

    # 7. soft text -> gentle unsharp mask; then contrast stretch
    if sharpness(g) < 250 and noise_level(g) < 3:   # sharpening a noisy page only sharpens the noise
        blur = cv2.GaussianBlur(g, (0, 0), 1.2)
        g = cv2.addWeighted(g, 1.6, blur, -0.6, 0)
        steps.append("Sharpened soft text (unsharp mask)")
    g = stretch(g)
    after = assess(g)
    if not steps:
        steps.append("Page was already clean; nothing to change")
    return g, col, {"before": before, "after": after, "steps": steps, "orientation": rot, "page_found": quad is not None}


# ------------------------------------------------------------------ layout detection (YOLO, ONNX)
@lru_cache(maxsize=1)
def _session():
    if not LAYOUT_MODEL.exists():
        return None
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = min(8, os.cpu_count() or 4)
    return ort.InferenceSession(str(LAYOUT_MODEL), so, providers=["CPUExecutionProvider"])


def layout_available() -> bool:
    try:
        return _session() is not None
    except Exception:
        return False


def detect_layout(img: np.ndarray, conf_thr: float = 0.35, iou_thr: float = 0.5) -> list[dict]:
    sess = _session()
    if sess is None:
        return []
    rgb = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    inp = sess.get_inputs()[0]
    S = inp.shape[2] if isinstance(inp.shape[2], int) else 640
    h, w = rgb.shape[:2]
    k = min(S / w, S / h)
    nw, nh = round(w * k), round(h * k)
    px, py = (S - nw) // 2, (S - nh) // 2
    canvas = np.full((S, S, 3), 114, np.uint8)
    canvas[py:py + nh, px:px + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
    x = canvas.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
    out = sess.run(None, {inp.name: x})[0][0]            # (4 + nc, anchors)
    out = out.T
    scores = out[:, 4:]
    cls = scores.argmax(1)
    conf = scores[np.arange(len(cls)), cls]
    keep = conf >= conf_thr
    if not keep.any():
        return []
    b, cls, conf = out[keep, :4], cls[keep], conf[keep]
    x0 = (b[:, 0] - b[:, 2] / 2 - px) / k
    y0 = (b[:, 1] - b[:, 3] / 2 - py) / k
    bw, bh = b[:, 2] / k, b[:, 3] / k
    rects = [[float(a), float(c), float(d), float(e)] for a, c, d, e in zip(x0, y0, bw, bh)]
    idx = cv2.dnn.NMSBoxesBatched(rects, conf.tolist(), cls.tolist(), conf_thr, iou_thr)
    dets = []
    for i in np.array(idx).ravel():
        X0, Y0 = max(0, rects[i][0]), max(0, rects[i][1])
        X1, Y1 = min(w, rects[i][0] + rects[i][2]), min(h, rects[i][1] + rects[i][3])
        name = LAYOUT_CLASSES[int(cls[i])]
        dets.append({"cls": name, "label": LAYOUT_LABEL[name], "conf": round(float(conf[i]), 3),
                     "box": [round(X0), round(Y0), round(X1), round(Y1)],
                     "box_rel": [round(X0 / w, 4), round(Y0 / h, 4), round(X1 / w, 4), round(Y1 / h, 4)]})
    dets.sort(key=lambda d: (d["box"][1], d["box"][0]))
    return dets


def crop(img, box, pad=10):
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    return img[max(0, y0 - pad):min(h, y1 + pad), max(0, x0 - pad):min(w, x1 + pad)]


def read_region(g: np.ndarray, box) -> dict:
    """OCR one detected region on its own, upscaled; often reads small print better than the full page."""
    c = crop(g, box, pad=12)
    if c.size == 0:
        return {"text": "", "mean_conf": 0}
    if c.mean() < 110:          # white text on a dark band
        c = 255 - c
    if c.shape[0] < 400:
        c = cv2.resize(c, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
    r = ocr(c, psm=6)
    return {"text": r["text"], "mean_conf": r["mean_conf"], "words": r["words"]}


STAMP_WORDS = ["PAID", "CANCELLED", "CANCELED", "VOID", "DUPLICATE", "COPY", "RECEIVED", "ENTERED", "VERIFIED",
               "APPROVED", "ORIGINAL", "POSTED"]


def read_stamp(rgb: np.ndarray, box) -> dict:
    """Stamps are rotated and inked: isolate the ink colour, square the stamp up using its
    frame, then read it; keep the read that finds a known stamp word."""
    c = crop(rgb if rgb.ndim == 3 else cv2.cvtColor(rgb, cv2.COLOR_GRAY2RGB), box, pad=8)
    if c.size == 0:
        return {"text": "", "keyword": None}
    sat = cv2.cvtColor(c, cv2.COLOR_RGB2HSV)[..., 1]
    coloured = np.percentile(sat, 90) > 60
    g = (255 - sat) if coloured else gray(c)            # coloured ink -> saturation channel drops black print
    ink = (sat > 60) if coloured else (g < 128)
    angles = [0, -10, 10, -20, 20, -30, 30]
    pts = cv2.findNonZero(ink.astype(np.uint8))
    if pts is not None and len(pts) > 50:
        (_, _), (rw, rh), ra = cv2.minAreaRect(pts)
        if rw < rh:
            ra -= 90
        ra = ((ra + 45) % 90) - 45
        angles = [round(ra, 1), round(-ra, 1)] + angles
    g = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    best = {"text": "", "keyword": None, "conf": 0.0, "angle": 0}
    for ang in angles:
        hh, ww = g.shape
        M = cv2.getRotationMatrix2D((ww / 2, hh / 2), ang, 1.0)
        r = cv2.warpAffine(g, M, (ww, hh), borderValue=255)
        for psm in (6, 11):
            o = ocr(r, psm=psm)
            up = re.sub(r"[^A-Z0-9 ]", " ", o["text"].upper())
            kw = next((k for k in STAMP_WORDS if k in up.split() or k in up.replace(" ", "")), None)
            score = o["mean_conf"] + (40 if kw else 0)
            if score > best["conf"]:
                best = {"text": " ".join(up.split())[:60], "keyword": kw, "conf": round(score, 1), "angle": ang}
            if kw and o["mean_conf"] > 70:
                return best
    return best


def draw_layout(img: np.ndarray, dets: list[dict]) -> np.ndarray:
    out = img.copy() if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    t = max(2, round(min(out.shape[:2]) / 450))
    for d in dets:
        x0, y0, x1, y1 = d["box"]
        col = LAYOUT_COLOR[d["cls"]]
        overlay = out.copy()
        cv2.rectangle(overlay, (x0, y0), (x1, y1), col, -1)
        out = cv2.addWeighted(overlay, 0.08, out, 0.92, 0)
        cv2.rectangle(out, (x0, y0), (x1, y1), col, t)
        label = f"{d['label']} {d['conf']:.0%}"
        fs = t * 0.38
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, max(1, t // 2))
        cv2.rectangle(out, (x0, max(0, y0 - th - 8)), (x0 + tw + 8, y0), col, -1)
        cv2.putText(out, label, (x0 + 4, y0 - 5), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), max(1, t // 2), cv2.LINE_AA)
    return out


def save_preview(img: np.ndarray, path: Path, long_edge: int = 1400):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = _limit(img, long_edge)
    if im.ndim == 3:
        im = cv2.cvtColor(im, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(path), im, [cv2.IMWRITE_JPEG_QUALITY, 82])
