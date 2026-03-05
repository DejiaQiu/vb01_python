from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    from ._base import build_feature_pack, load_rows
    from .detect_rope_looseness import detect
except ImportError:  # pragma: no cover
    from _base import build_feature_pack, load_rows
    from detect_rope_looseness import detect


_PATTERN = re.compile(r"vibration_30s_\d{8}_(\d{6})\.csv$")


def _hhmmss(path: Path) -> str:
    m = _PATTERN.match(path.name)
    return m.group(1) if m else "000000"


def _in_range(hhmmss: str, start_hhmm: str, end_hhmm: str) -> bool:
    hhmm = hhmmss[:4]
    return start_hhmm <= hhmm <= end_hhmm


def _apply_consecutive_confirmation(rows: list[dict], confirm_windows: int) -> None:
    k = max(1, int(confirm_windows))
    if k <= 1:
        for row in rows:
            row["confirmed_triggered"] = bool(row["raw_triggered"])
        return

    for row in rows:
        row["confirmed_triggered"] = False

    start = None
    for i, row in enumerate(rows):
        if row["raw_triggered"]:
            if start is None:
                start = i
        else:
            if start is not None:
                run_len = i - start
                if run_len >= k:
                    for j in range(start, i):
                        rows[j]["confirmed_triggered"] = True
            start = None

    if start is not None:
        run_len = len(rows) - start
        if run_len >= k:
            for j in range(start, len(rows)):
                rows[j]["confirmed_triggered"] = True


def run_timeline(
    *,
    input_dir: Path,
    start_hhmm: str,
    end_hhmm: str,
    min_score: float,
    confirm_windows: int,
) -> dict:
    files = sorted(input_dir.glob("vibration_30s_*.csv"), key=lambda p: _hhmmss(p))
    rows: list[dict] = []

    for path in files:
        hhmmss = _hhmmss(path)
        if not _in_range(hhmmss, start_hhmm, end_hhmm):
            continue

        features = build_feature_pack(load_rows(path))
        result = detect(features)
        score = float(result.get("score", 0.0))
        raw_triggered = score >= float(min_score)

        rows.append(
            {
                "hhmmss": hhmmss,
                "file": path.name,
                "score": round(score, 2),
                "level": result.get("level", "normal"),
                "mode": str(result.get("reasons", ["mode=unknown"])[0]).replace("mode=", ""),
                "raw_triggered": raw_triggered,
                "confirmed_triggered": False,
            }
        )

    _apply_consecutive_confirmation(rows, confirm_windows=confirm_windows)

    out = {
        "input_dir": str(input_dir),
        "window": {"start_hhmm": start_hhmm, "end_hhmm": end_hhmm},
        "rule": {"min_score": float(min_score), "confirm_windows": int(confirm_windows)},
        "count": len(rows),
        "raw_trigger_count": sum(1 for row in rows if row["raw_triggered"]),
        "confirmed_trigger_count": sum(1 for row in rows if row["confirmed_triggered"]),
        "rows": rows,
    }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="钢丝绳松动时间序列确认（连续命中才报警）")
    parser.add_argument("--input-dir", default="report", help="输入目录（包含 vibration_30s_*.csv）")
    parser.add_argument("--start-hhmm", default="0000", help="开始时间 HHMM")
    parser.add_argument("--end-hhmm", default="2359", help="结束时间 HHMM")
    parser.add_argument("--min-score", type=float, default=60.0, help="单窗触发分数阈值")
    parser.add_argument("--confirm-windows", type=int, default=2, help="连续命中窗口数")
    parser.add_argument("--pretty", action="store_true", help="格式化输出JSON")
    args = parser.parse_args()

    payload = run_timeline(
        input_dir=Path(args.input_dir),
        start_hhmm=str(args.start_hhmm),
        end_hhmm=str(args.end_hhmm),
        min_score=float(args.min_score),
        confirm_windows=int(args.confirm_windows),
    )

    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
