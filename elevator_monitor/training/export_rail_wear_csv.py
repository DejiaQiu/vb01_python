from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any, Iterable

from ..common import load_records
from ..fault_types import FaultTypeEngine
from ..monitor.constants import RAIL_WEAR_FIELDS
from ..monitor.pipeline import OnlineAnomalyDetector


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把原始振动 CSV/JSONL 转换为导轨磨损趋势 CSV（8列）")
    parser.add_argument("--input", required=True, help="输入原始数据文件，支持 .csv/.jsonl/.ndjson")
    parser.add_argument("--output", required=True, help="输出导轨磨损趋势 CSV")

    parser.add_argument("--sample-hz", type=float, default=100.0, help="采样频率")
    parser.add_argument("--stale-limit", type=int, default=300, help="连续重复阈值")

    parser.add_argument("--baseline-size", type=int, default=5000, help="异常检测基线最大缓存")
    parser.add_argument("--baseline-min-records", type=int, default=300, help="异常检测建基线最小样本")
    parser.add_argument("--baseline-refresh-every", type=int, default=200, help="异常检测基线刷新间隔")
    parser.add_argument("--warning-z", type=float, default=3.5, help="异常检测 warning 阈值")
    parser.add_argument("--anomaly-z", type=float, default=6.0, help="异常检测 anomaly 阈值")

    parser.add_argument("--fault-baseline-size", type=int, default=2000, help="导轨磨损基线最大缓存")
    parser.add_argument("--fault-baseline-min-records", type=int, default=120, help="导轨磨损建基线最小样本")
    return parser


def convert_rows(
    rows: Iterable[dict[str, Any]],
    *,
    sample_hz: float = 100.0,
    stale_limit: int = 300,
    baseline_size: int = 5000,
    baseline_min_records: int = 300,
    baseline_refresh_every: int = 200,
    warning_z: float = 3.5,
    anomaly_z: float = 6.0,
    fault_baseline_size: int = 2000,
    fault_baseline_min_records: int = 120,
) -> list[dict[str, Any]]:
    detector = OnlineAnomalyDetector(
        baseline_size=baseline_size,
        baseline_min_records=baseline_min_records,
        baseline_refresh_every=baseline_refresh_every,
        stale_limit=stale_limit,
        warning_z=warning_z,
        anomaly_z=anomaly_z,
    )
    fault_engine = FaultTypeEngine(
        enabled=True,
        min_level="warning",
        top_k=3,
        stale_limit=stale_limit,
        baseline_size=fault_baseline_size,
        baseline_min_records=fault_baseline_min_records,
        sample_hz=sample_hz,
    )

    out: list[dict[str, Any]] = []
    for row in rows:
        anomaly_result = detector.update(row)
        fault_engine.update(row, anomaly_result)
        export_row = fault_engine.get_rail_wear_export_row()
        if export_row:
            out.append(export_row)
    return out


def main() -> int:
    args = build_arg_parser().parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"输入文件不存在: {in_path}")

    rows = load_records(in_path)
    export_rows = convert_rows(
        rows,
        sample_hz=args.sample_hz,
        stale_limit=args.stale_limit,
        baseline_size=args.baseline_size,
        baseline_min_records=args.baseline_min_records,
        baseline_refresh_every=args.baseline_refresh_every,
        warning_z=args.warning_z,
        anomaly_z=args.anomaly_z,
        fault_baseline_size=args.fault_baseline_size,
        fault_baseline_min_records=args.fault_baseline_min_records,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=RAIL_WEAR_FIELDS)
        writer.writeheader()
        writer.writerows(export_rows)

    alarm_count = sum(1 for row in export_rows if int(row.get("alarm_flag", 0)) == 1)
    print(f"输入记录数: {len(rows)}")
    print(f"输出记录数: {len(export_rows)}")
    print(f"告警记录数: {alarm_count}")
    print(f"输出路径: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
