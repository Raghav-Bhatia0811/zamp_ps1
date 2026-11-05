"""Train the invoice layout detector (YOLO) on the synthetic set and export it to ONNX.

    python3 -m venv .venv-train && .venv-train/bin/pip install -r requirements-train.txt
    .venv-train/bin/python scripts/make_layout_dataset.py --n 1600
    .venv-train/bin/python scripts/train_layout.py --epochs 40

Training needs torch + ultralytics. The app itself only needs onnxruntime:
the exported models/layout-yolo.onnx is loaded by app/vision.py.
Note: ultralytics is AGPL-3.0. For a commercial deployment either buy their
licence or swap in an Apache-2.0 detector; the ONNX interface stays the same.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "build/layout-ds/data.yaml"))
    ap.add_argument("--base", default="yolo11n.pt")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--fraction", type=float, default=1.0)
    ap.add_argument("--name", default="layout")
    ap.add_argument("--export-only", action="store_true", help="skip training; validate and export build/runs/<name>/weights/best.pt")
    a = ap.parse_args()

    if not a.export_only:
        train(a)
    export(a)


def train(a):
    model = YOLO(a.base)
    model.train(
        data=a.data, epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, device="cpu", workers=8,
        project=str(ROOT / "build/runs"), name=a.name, exist_ok=True, fraction=a.fraction,
        # documents have a fixed orientation and reading order: no flips, mild geometry
        fliplr=0.0, flipud=0.0, degrees=0.0, shear=0.0, perspective=0.0,
        mosaic=0.5, close_mosaic=8, scale=0.25, translate=0.05, hsv_h=0.01, hsv_s=0.3, hsv_v=0.3,
        patience=12, plots=True, verbose=False,
    )


def export(a):
    import csv
    best = ROOT / f"build/runs/{a.name}/weights/best.pt"
    m = YOLO(str(best))
    metrics = m.val(data=a.data, imgsz=a.imgsz, device="cpu", split="val", plots=False, verbose=False)
    names = m.names
    per_class = {names[i]: round(float(v), 3) for i, v in zip(metrics.box.ap_class_index, metrics.box.maps[metrics.box.ap_class_index])}
    rows = list(csv.DictReader(open(ROOT / f"build/runs/{a.name}/results.csv")))
    report = {"base": a.base, "epochs_trained": len(rows), "imgsz": a.imgsz, "train_images": len(list((Path(a.data).parent / "images/train").glob("*.jpg"))),
              "val_images": len(list((Path(a.data).parent / "images/val").glob("*.jpg"))),
              "mAP50": round(float(metrics.box.map50), 3), "mAP50_95": round(float(metrics.box.map), 3),
              "per_class_mAP50_95": per_class, "classes": [names[i] for i in sorted(names)]}
    onnx_path = Path(m.export(format="onnx", imgsz=a.imgsz, opset=12, simplify=True, dynamic=False))
    (ROOT / "models").mkdir(exist_ok=True)
    shutil.copy(onnx_path, ROOT / "models/layout-yolo.onnx")
    (ROOT / "models/layout-yolo.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
