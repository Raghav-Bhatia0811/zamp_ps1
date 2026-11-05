"""Build the YOLO training set for the layout detector from synthetic pages.

    .venv-train/bin/python scripts/make_layout_dataset.py --n 1400 --out build/layout-ds

Writes images/{train,val} and labels/{train,val} in YOLO format plus data.yaml.
Mix of damage: 30% clean, 35% scan, 10% fax, 25% phone photo.
"""
from __future__ import annotations

import argparse
import random
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synth_layout as S  # noqa: E402

LONG_EDGE = 960


def one(args):
    i, out, split = args
    r = random.Random(10_000 + i)
    img, boxes = S.random_invoice(i)
    mode = r.choices(["clean", "scan", "fax", "photo"], weights=[30, 35, 10, 25])[0]
    img, boxes = S.degrade(img, boxes, r, mode)
    w, h = img.size
    k = LONG_EDGE / max(w, h)
    img = img.resize((round(w * k), round(h * k)))
    name = f"syn_{i:05d}"
    img.save(out / "images" / split / f"{name}.jpg", quality=r.randint(70, 92))
    with open(out / "labels" / split / f"{name}.txt", "w") as f:
        for c, x0, y0, x1, y1 in boxes:
            f.write(f"{c} {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}\n")
    return mode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1400)
    ap.add_argument("--val", type=float, default=0.12)
    ap.add_argument("--out", default="build/layout-ds")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    n_val = int(a.n * a.val)
    jobs = [(i, out, "val" if i < n_val else "train") for i in range(a.n)]
    with Pool() as pool:
        modes = pool.map(one, jobs, chunksize=8)
    (out / "data.yaml").write_text(
        f"path: {out}\ntrain: images/train\nval: images/val\nnames:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(S.CLASSES)))
    print({m: modes.count(m) for m in set(modes)}, "->", out)


if __name__ == "__main__":
    main()
