from __future__ import annotations

import argparse

from .centroid_model import fit_centroid_classifier
from .train_utils import (
    LabeledSample,
    cap_class_ratio,
    class_distribution,
    default_feature_names,
    filter_by_min_samples,
    load_dataset_rows,
    row_to_feature_vector,
    split_train_val,
)


_POSITIVE = {"1", "true", "yes", "y"}
_NEGATIVE = {"0", "false", "no", "n"}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="训练24h风险预测模型（二分类，centroid baseline）")
    parser.add_argument("--dataset-csv", required=True, help="由 prepare_dataset 生成的数据集")
    parser.add_argument("--output-model", required=True, help="输出模型 JSON")
    parser.add_argument("--target-column", default="target_fault_24h", help="风险标签列名")
    parser.add_argument("--group-column", default="source_file", help="训练/验证切分分组列，默认按采集文件切分")
    parser.add_argument("--positive-label", default="1", help="正类标签输出值")
    parser.add_argument("--negative-label", default="0", help="负类标签输出值")
    parser.add_argument("--min-class-samples", type=int, default=20, help="每类最小样本数")
    parser.add_argument("--negative-max-ratio", type=float, default=4.0, help="负类相对正类最大比例")
    parser.add_argument("--val-ratio", type=float, default=0.2, help="验证集比例")
    parser.add_argument("--seed", type=int, default=2026, help="随机种子（用于确定性分割）")
    return parser


def _normalize_binary(raw: str, pos_label: str, neg_label: str) -> str:
    text = raw.strip().lower()
    if text in _POSITIVE:
        return pos_label
    if text in _NEGATIVE:
        return neg_label
    try:
        return pos_label if float(text) > 0.0 else neg_label
    except ValueError:
        return neg_label


def _collect_samples(rows: list[dict[str, str]], target_column: str, pos_label: str, neg_label: str) -> list[LabeledSample]:
    out: list[LabeledSample] = []
    for row in rows:
        raw = str(row.get(target_column, "")).strip()
        if not raw:
            continue
        label = _normalize_binary(raw, pos_label=pos_label, neg_label=neg_label)
        out.append(LabeledSample(row=row, label=label))
    return out


def main() -> int:
    args = build_arg_parser().parse_args()

    rows = load_dataset_rows(args.dataset_csv)
    feature_names = default_feature_names(rows)

    samples = _collect_samples(
        rows=rows,
        target_column=args.target_column,
        pos_label=args.positive_label,
        neg_label=args.negative_label,
    )
    if not samples:
        raise SystemExit("无可用训练样本，请检查 target_column")

    samples = filter_by_min_samples(samples, min_class_samples=args.min_class_samples)
    if not samples:
        raise SystemExit("按最小样本过滤后无数据，请降低 --min-class-samples")

    samples = cap_class_ratio(
        samples,
        majority_label=args.negative_label,
        max_ratio=max(1.0, args.negative_max_ratio),
        seed=args.seed,
    )

    labels = {s.label for s in samples}
    if labels != {args.negative_label, args.positive_label}:
        raise SystemExit(f"二分类标签不完整，当前标签集合: {sorted(labels)}")

    train_samples, val_samples = split_train_val(
        samples,
        val_ratio=args.val_ratio,
        seed=args.seed,
        group_column=str(args.group_column or "").strip(),
    )
    train_labels = {s.label for s in train_samples}
    if train_labels != {args.negative_label, args.positive_label}:
        raise SystemExit(f"训练集标签不完整，当前: {sorted(train_labels)}")

    train_x = [row_to_feature_vector(s.row, feature_names) for s in train_samples]
    train_y = [s.label for s in train_samples]
    val_x = [row_to_feature_vector(s.row, feature_names) for s in val_samples]
    val_y = [s.label for s in val_samples]

    model = fit_centroid_classifier(
        features=train_x,
        labels=train_y,
        feature_names=feature_names,
        task="risk_24h",
        eval_features=val_x,
        eval_labels=val_y,
    )
    model.metrics["train_distribution"] = class_distribution(train_samples)
    model.metrics["val_distribution"] = class_distribution(val_samples)
    model.metrics["target_column"] = args.target_column
    model.metrics["positive_label"] = args.positive_label
    model.metrics["negative_label"] = args.negative_label
    model.metrics["split_group_column"] = str(args.group_column or "").strip() or "fallback"

    model.save(args.output_model)

    print(f"train samples: {len(train_samples)}")
    print(f"val samples: {len(val_samples)}")
    print(f"metrics: {model.metrics}")
    print(f"saved model: {args.output_model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
