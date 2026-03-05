from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from ._base import build_feature_pack, load_rows
    from .detect_bearing_wear import detect as detect_bearing_wear
    from .detect_brake_jitter import detect as detect_brake_jitter
    from .detect_car_imbalance import detect as detect_car_imbalance
    from .detect_coupling_misalignment import detect as detect_coupling_misalignment
    from .detect_impact_shock import detect as detect_impact_shock
    from .detect_mechanical_looseness import detect as detect_mechanical_looseness
    from .detect_rail_wear import detect as detect_rail_wear
    from .detect_rope_looseness import detect as detect_rope_looseness
except ImportError:  # pragma: no cover
    from _base import build_feature_pack, load_rows
    from detect_bearing_wear import detect as detect_bearing_wear
    from detect_brake_jitter import detect as detect_brake_jitter
    from detect_car_imbalance import detect as detect_car_imbalance
    from detect_coupling_misalignment import detect as detect_coupling_misalignment
    from detect_impact_shock import detect as detect_impact_shock
    from detect_mechanical_looseness import detect as detect_mechanical_looseness
    from detect_rail_wear import detect as detect_rail_wear
    from detect_rope_looseness import detect as detect_rope_looseness


DETECTORS = [
    detect_mechanical_looseness,
    detect_impact_shock,
    detect_rail_wear,
    detect_rope_looseness,
    detect_bearing_wear,
    detect_coupling_misalignment,
    detect_brake_jitter,
    detect_car_imbalance,
]


def run_all_rows(rows: list[dict[str, str]], source: str = "") -> dict:
    features = build_feature_pack(rows)

    results = [detector(features) for detector in DETECTORS]
    results = sorted(results, key=lambda x: float(x.get("score", 0.0)), reverse=True)

    return {
        "input": str(source),
        "summary": {
            "n_raw": int(features.get("n_raw", 0)),
            "n_effective": int(features.get("n", 0)),
            "fs_hz": round(float(features.get("fs_hz", 0.0)), 4),
            "used_new_only": bool(features.get("used_new_only", False)),
            "new_ratio": round(float(features.get("new_ratio", 0.0)), 4),
        },
        "top_fault": results[0] if results else {},
        "results": results,
    }


def run_all(path: Path) -> dict:
    rows = load_rows(path)
    return run_all_rows(rows, source=str(path))


def main() -> int:
    parser = argparse.ArgumentParser(description="电梯振动8类故障识别（规则版）")
    parser.add_argument("--input", required=True, help="输入CSV")
    parser.add_argument("--pretty", action="store_true", help="格式化JSON输出")
    args = parser.parse_args()

    payload = run_all(Path(args.input))
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
