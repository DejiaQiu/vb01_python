from __future__ import annotations

try:
    from ._base import build_result, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, ratio_to_100, run_detector_cli


FAULT_TYPE = "impact_shock"


def detect(features: dict) -> dict:
    s_crest = ratio_to_100(features.get("a_crest", 0.0), 3.0, 8.0)
    s_peak = ratio_to_100(features.get("peak_rate_hz", 0.0), 0.10, 2.00)
    s_jerk = ratio_to_100(features.get("jerk_rms", 0.0), 0.020, 0.400)
    s_gp2p = ratio_to_100(features.get("g_p2p", 0.0), 0.20, 2.00)

    score = 0.35 * s_crest + 0.25 * s_peak + 0.20 * s_jerk + 0.20 * s_gp2p
    reasons = [
        f"a_crest={features.get('a_crest', 0.0):.3f}",
        f"peak_rate_hz={features.get('peak_rate_hz', 0.0):.6f}",
        f"jerk_rms={features.get('jerk_rms', 0.0):.6f}",
        f"g_p2p={features.get('g_p2p', 0.0):.6f}",
    ]
    return build_result(fault_type=FAULT_TYPE, score=score, reasons=reasons, features=features, min_samples=10)


if __name__ == "__main__":
    raise SystemExit(run_detector_cli(detect, description="冲击撞击识别（峰值密度+尖峰因子+角速度突变）"))
