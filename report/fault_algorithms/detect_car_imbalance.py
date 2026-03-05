from __future__ import annotations

try:
    from ._base import build_result, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, ratio_to_100, run_detector_cli


FAULT_TYPE = "car_imbalance"


def detect(features: dict) -> dict:
    s_lateral = ratio_to_100(features.get("lateral_ratio", 0.0), 1.0, 2.4)
    s_stable = 100.0 - ratio_to_100(features.get("jerk_rms", 0.0), 0.030, 0.350)
    s_low_peak = 100.0 - ratio_to_100(features.get("peak_rate_hz", 0.0), 0.40, 2.50)
    s_cont = ratio_to_100(features.get("a_std", 0.0), 0.004, 0.020)

    score = 0.35 * s_lateral + 0.25 * s_stable + 0.20 * s_low_peak + 0.20 * s_cont
    reasons = [
        f"lateral_ratio={features.get('lateral_ratio', 0.0):.4f}",
        f"jerk_rms={features.get('jerk_rms', 0.0):.6f}",
        f"peak_rate_hz={features.get('peak_rate_hz', 0.0):.6f}",
        f"a_std={features.get('a_std', 0.0):.6f}",
    ]
    return build_result(fault_type=FAULT_TYPE, score=score, reasons=reasons, features=features, min_samples=14)


if __name__ == "__main__":
    raise SystemExit(run_detector_cli(detect, description="轿厢偏载/不平衡识别（横向占比高且长期稳定）"))
