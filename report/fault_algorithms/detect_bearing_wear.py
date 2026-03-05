from __future__ import annotations

try:
    from ._base import build_result, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, ratio_to_100, run_detector_cli


FAULT_TYPE = "traction_motor_bearing_wear"


def detect(features: dict) -> dict:
    s_gstd = ratio_to_100(features.get("g_std", 0.0), 0.08, 0.80)
    s_jerk = ratio_to_100(features.get("jerk_rms", 0.0), 0.015, 0.300)
    s_peak = ratio_to_100(features.get("peak_rate_hz", 0.0), 0.15, 3.00)
    s_crest = ratio_to_100(features.get("a_crest", 0.0), 2.5, 6.0)

    score = 0.35 * s_gstd + 0.25 * s_jerk + 0.20 * s_peak + 0.20 * s_crest
    reasons = [
        f"g_std={features.get('g_std', 0.0):.6f}",
        f"jerk_rms={features.get('jerk_rms', 0.0):.6f}",
        f"peak_rate_hz={features.get('peak_rate_hz', 0.0):.6f}",
        f"a_crest={features.get('a_crest', 0.0):.3f}",
    ]
    return build_result(fault_type=FAULT_TYPE, score=score, reasons=reasons, features=features, min_samples=14)


if __name__ == "__main__":
    raise SystemExit(run_detector_cli(detect, description="曳引机轴承磨损识别（角振动离散度+冲击变化）"))
