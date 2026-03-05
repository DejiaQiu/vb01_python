from __future__ import annotations

import argparse
from collections import Counter

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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="训练故障类型分类模型（centroid baseline）")
    parser.add_argument("--dataset-csv", required=True, help="由 prepare_dataset 生成的数据集")
    parser.add_argument("--output-model", required=True, help="输出模型 JSON")
    parser.add_argument("--target-column", default="target_fault_type", help="标签列名")
    parser.add_argument("--group-column", default="source_file", help="训练/验证切分分组列，默认按采集文件切分")
    parser.add_argument("--normal-label", default="normal", help="正常类别名称")
    parser.add_argument("--exclude-normal", action="store_true", help="训练时移除 normal 类")
    parser.add_argument("--drop-label", action="append", default=[], help="排除某些标签，可重复")
    parser.add_argument("--min-class-samples", type=int, default=8, help="每类最小样本数")
    parser.add_argument("--normal-max-ratio", type=float, default=5.0, help="normal 类相对其他类最大比例")
    parser.add_argument("--val-ratio", type=float, default=0.2, help="验证集比例")
    parser.add_argument("--seed", type=int, default=2026, help="随机种子（用于确定性分割）")
    return parser


def _collect_samples(
    rows: list[dict[str, str]],
    target_column: str,
    normal_label: str,
    exclude_normal: bool,
    drop_labels: set[str],
) -> list[LabeledSample]:
    samples: list[LabeledSample] = []
    for row in rows:
        label = str(row.get(target_column, "")).strip()
        if not label:
            continue
        if label in drop_labels:
            continue
        if exclude_normal and label == normal_label:
            continue
        samples.append(LabeledSample(row=row, label=label))
    return samples


def main() -> int:
    args = build_arg_parser().parse_args()

    rows = load_dataset_rows(args.dataset_csv)
    feature_names = default_feature_names(rows)

    samples = _collect_samples(
        rows=rows,
        target_column=args.target_column,
        normal_label=args.normal_label,
        exclude_normal=args.exclude_normal,
        drop_labels=set(args.drop_label),
    )
    if not samples:
        raise SystemExit("无可用训练样本，请检查 target_column / 过滤条件")

    samples = filter_by_min_samples(samples, min_class_samples=args.min_class_samples)
    if not samples:
        raise SystemExit("按最小样本过滤后无数据，请降低 --min-class-samples")

    if not args.exclude_normal:
        samples = cap_class_ratio(
            samples,
            majority_label=args.normal_label,
            max_ratio=max(1.0, args.normal_max_ratio),
            seed=args.seed,
        )

    labels = [s.label for s in samples]
    if len(set(labels)) < 2:
        raise SystemExit("类别不足 2，无法训练分类器")

    train_samples, val_samples = split_train_val(
        samples,
        val_ratio=args.val_ratio,
        seed=args.seed,
        group_column=str(args.group_column or "").strip(),
    )
    if len({s.label for s in train_samples}) < 2:
        raise SystemExit("训练集类别不足 2，请调整数据量或 val-ratio")

    train_x = [row_to_feature_vector(s.row, feature_names) for s in train_samples]
    train_y = [s.label for s in train_samples]
    val_x = [row_to_feature_vector(s.row, feature_names) for s in val_samples]
    val_y = [s.label for s in val_samples]

    model = fit_centroid_classifier(
        features=train_x,
        labels=train_y,
        feature_names=feature_names,
        task="fault_type",
        eval_features=val_x,
        eval_labels=val_y,
    )

    model.metrics["train_distribution"] = class_distribution(train_samples)
    model.metrics["val_distribution"] = class_distribution(val_samples)
    model.metrics["target_column"] = args.target_column
    model.metrics["split_group_column"] = str(args.group_column or "").strip() or "fallback"

    model.save(args.output_model)

    print(f"train samples: {len(train_samples)}")
    print(f"val samples: {len(val_samples)}")
    print(f"classes: {sorted(Counter(train_y).keys())}")
    print(f"metrics: {model.metrics}")
    print(f"saved model: {args.output_model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
