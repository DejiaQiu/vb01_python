from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from .train_utils import default_feature_names, load_dataset_rows, row_to_feature_vector


_EPS = 1e-9


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从少量故障样本生成可解释故障算法（prototype + confidence）")
    parser.add_argument("--dataset-csv", required=True, help="由 prepare_dataset 生成的数据集")
    parser.add_argument("--output-json", required=True, help="输出算法 JSON")
    parser.add_argument("--target-column", default="target_fault_type", help="故障标签列")
    parser.add_argument("--normal-label", default="normal", help="正常标签名称")
    parser.add_argument("--min-class-samples", type=int, default=3, help="每类最小样本数")
    parser.add_argument("--top-features-per-class", type=int, default=8, help="每类最多保留特征数")
    parser.add_argument("--threshold-quantile", type=float, default=0.2, help="类内分数分位数阈值（0-1）")
    return parser


def _mean_std(vectors: list[list[float]]) -> tuple[list[float], list[float]]:
    if not vectors:
        return [], []
    dim = len(vectors[0])
    mean: list[float] = []
    std: list[float] = []
    for i in range(dim):
        col = [vec[i] for vec in vectors]
        mean.append(float(statistics.fmean(col)))
        std.append(max(_EPS, float(statistics.pstdev(col)) if len(col) > 1 else _EPS))
    return mean, std


def _score_vector(vec: list[float], prototype: list[float], weights: list[float], normal_std: list[float]) -> float:
    weighted_dist = 0.0
    weight_sum = 0.0
    for i, center in enumerate(prototype):
        weight = weights[i]
        if weight <= 0.0:
            continue
        scale = max(_EPS, normal_std[i])
        weighted_dist += weight * abs(vec[i] - center) / scale
        weight_sum += weight
    if weight_sum <= _EPS:
        return 0.0
    return math.exp(-(weighted_dist / weight_sum))


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    qq = max(0.0, min(1.0, float(q)))
    sorted_values = sorted(values)
    idx = int((len(sorted_values) - 1) * qq)
    return float(sorted_values[idx])


def main() -> int:
    args = build_arg_parser().parse_args()

    rows = load_dataset_rows(args.dataset_csv)
    feature_names = default_feature_names(rows)
    min_count = max(1, int(args.min_class_samples))
    top_k = max(1, int(args.top_features_per_class))

    grouped: dict[str, list[list[float]]] = {}
    for row in rows:
        label = str(row.get(args.target_column, "")).strip()
        if not label:
            continue
        # 按标签聚合滑窗特征，后续每一类都会生成一个原型中心。
        grouped.setdefault(label, []).append(row_to_feature_vector(row, feature_names))

    if args.normal_label not in grouped:
        raise SystemExit(f"缺少 normal 类样本：{args.normal_label}")

    grouped = {label: vecs for label, vecs in grouped.items() if len(vecs) >= min_count}
    if args.normal_label not in grouped:
        raise SystemExit("normal 类样本不足，请降低 --min-class-samples")

    classes = sorted(label for label in grouped if label != args.normal_label)
    if not classes:
        raise SystemExit("故障类不足，请提供至少一种非 normal 标签")

    normal_mean, normal_std = _mean_std(grouped[args.normal_label])
    rules: list[dict[str, object]] = []
    class_counter = Counter({label: len(vecs) for label, vecs in grouped.items()})

    for label in classes:
        vectors = grouped[label]
        class_mean, _ = _mean_std(vectors)
        # 计算“故障类相对 normal 的偏移强度”，作为特征权重依据。
        z_shift = [abs((class_mean[i] - normal_mean[i]) / max(_EPS, normal_std[i])) for i in range(len(feature_names))]
        ranked = sorted(range(len(feature_names)), key=lambda idx: z_shift[idx], reverse=True)
        keep_idx = set(ranked[:top_k])

        prototype: list[float] = []
        weights: list[float] = []
        for i in range(len(feature_names)):
            if i in keep_idx:
                prototype.append(class_mean[i])
                weights.append(max(0.0, z_shift[i]))
            else:
                prototype.append(class_mean[i])
                weights.append(0.0)
        if sum(weights) <= _EPS:
            # 兜底：如果区分度很弱，至少保留 top_k 的均匀权重。
            weights = [1.0 if i in keep_idx else 0.0 for i in range(len(feature_names))]

        # 用类内分位数分数做最小阈值，线上低于该阈值时输出 unknown。
        scores = [_score_vector(vec, prototype, weights, normal_std) for vec in vectors]
        min_score = _quantile(scores, args.threshold_quantile)

        rules.append(
            {
                "label": label,
                "sample_count": len(vectors),
                "prototype": {name: float(prototype[i]) for i, name in enumerate(feature_names)},
                "weights": {name: float(weights[i]) for i, name in enumerate(feature_names)},
                "min_score": float(min_score),
            }
        )

    payload = {
        "version": 1,
        "algorithm_type": "generated_fault_algorithm_v1",
        "target_column": args.target_column,
        "normal_label": args.normal_label,
        "feature_names": feature_names,
        "normal_stats": {
            "mean": {name: float(normal_mean[i]) for i, name in enumerate(feature_names)},
            "std": {name: float(normal_std[i]) for i, name in enumerate(feature_names)},
        },
        "classes": rules,
        "class_counts": dict(class_counter),
        "meta": {
            "min_class_samples": min_count,
            "top_features_per_class": top_k,
            "threshold_quantile": float(max(0.0, min(1.0, args.threshold_quantile))),
            "source_dataset": str(Path(args.dataset_csv)),
        },
    }

    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"fault classes: {classes}")
    print(f"class counts: {dict(class_counter)}")
    print(f"saved algorithm: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
