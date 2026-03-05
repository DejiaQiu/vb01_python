from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .centroid_model import CentroidModel, classification_metrics


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用训练模型批量推理")
    parser.add_argument("--model-json", required=True, help="模型文件")
    parser.add_argument("--dataset-csv", required=True, help="输入数据集")
    parser.add_argument("--output-csv", required=True, help="输出预测结果")
    parser.add_argument("--target-column", default="", help="可选：真实标签列，用于离线评估")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    model = CentroidModel.load(args.model_json)

    with Path(args.dataset_csv).open("r", encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))

    out_rows: list[dict[str, str]] = []
    y_true: list[str] = []
    y_pred: list[str] = []

    for row in rows:
        vec = model.vector_from_row(row)
        proba = model.predict_proba_vec(vec)
        pred = max(proba, key=proba.get) if proba else ""
        conf = proba.get(pred, 0.0)

        out_row = {
            "elevator_id": str(row.get("elevator_id", "")),
            "window_start_ms": str(row.get("window_start_ms", "")),
            "window_end_ms": str(row.get("window_end_ms", "")),
            "pred": pred,
            "confidence": f"{conf:.6f}",
        }
        for cls in model.classes:
            out_row[f"proba_{cls}"] = f"{proba.get(cls, 0.0):.6f}"

        if args.target_column:
            truth = str(row.get(args.target_column, "")).strip()
            out_row["truth"] = truth
            if truth:
                y_true.append(truth)
                y_pred.append(pred)

        out_rows.append(out_row)

    out_path = Path(args.output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(out_rows[0].keys()) if out_rows else ["pred", "confidence"]
    with out_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"predictions: {len(out_rows)}")
    print(f"output: {out_path}")

    if y_true:
        classes = sorted(set(y_true) | set(model.classes))
        metrics = classification_metrics(y_true=y_true, y_pred=y_pred, classes=classes)
        print(f"eval metrics: {metrics}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
