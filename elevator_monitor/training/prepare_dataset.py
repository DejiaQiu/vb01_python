from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from .dataset_builder import (
    build_window_samples,
    dataset_fieldnames,
    discover_data_files,
    load_fault_events,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建训练数据集（窗口特征 + 标签）")
    parser.add_argument(
        "--data-glob",
        action="append",
        required=True,
        help="输入数据文件 glob，可重复传入，如 data/*.csv",
    )
    parser.add_argument("--label-csv", default="", help="标签文件 CSV（可选）")
    parser.add_argument("--output", required=True, help="输出训练集 CSV")
    parser.add_argument("--window-s", type=float, default=10.0, help="窗口长度（秒）")
    parser.add_argument("--step-s", type=float, default=5.0, help="滑窗步长（秒）")
    parser.add_argument("--horizon-s", type=float, default=24 * 3600.0, help="风险标签前瞻窗口（秒）")
    parser.add_argument("--min-samples", type=int, default=20, help="窗口最小样本数")
    parser.add_argument("--default-event-duration-s", type=float, default=300.0, help="标签缺失结束时间时的默认持续秒数")
    parser.add_argument("--default-elevator-id", default="", help="数据文件无 elevator_id 字段时的默认值")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    files = discover_data_files(args.data_glob)
    if not files:
        raise SystemExit("未找到输入数据文件，请检查 --data-glob")

    timelines = load_fault_events(args.label_csv or None, default_event_duration_s=args.default_event_duration_s)
    samples = build_window_samples(
        data_files=files,
        event_timelines=timelines,
        window_s=args.window_s,
        step_s=args.step_s,
        horizon_s=args.horizon_s,
        min_samples=max(1, args.min_samples),
        default_elevator_id=args.default_elevator_id or None,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=dataset_fieldnames())
        writer.writeheader()
        writer.writerows(sample.to_row() for sample in samples)

    fault_counter = Counter(sample.target_fault_type for sample in samples)
    risk_counter = Counter(sample.target_fault_24h for sample in samples)

    print(f"输入文件数: {len(files)}")
    print(f"输出样本数: {len(samples)}")
    print(f"输出路径: {out_path}")
    print(f"fault_type分布: {dict(fault_counter)}")
    print(f"fault_24h分布: {dict(risk_counter)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
