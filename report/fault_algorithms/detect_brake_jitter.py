from __future__ import annotations

try:
    from ._base import build_result, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, ratio_to_100, run_detector_cli


FAULT_TYPE = "brake_jitter"


def detect(features: dict) -> dict:
    s_gp2p = ratio_to_100(features.get("g_p2p", 0.0), 0.20, 2.20)
    s_zc = ratio_to_100(features.get("zc_rate_hz", 0.0), 1.0, 16.0)
    s_peak = ratio_to_100(features.get("peak_rate_hz", 0.0), 0.15, 2.50)
    s_state = ratio_to_100(
        features.get("sx_std", 0.0) + features.get("sy_std", 0.0) + features.get("sz_std", 0.0),
        0.10,
        4.00,
    )

    score = 0.35 * s_gp2p + 0.25 * s_zc + 0.20 * s_peak + 0.20 * s_state
    reasons = [
        f"g_p2p={features.get('g_p2p', 0.0):.6f}",
        f"zc_rate_hz={features.get('zc_rate_hz', 0.0):.6f}",
        f"peak_rate_hz={features.get('peak_rate_hz', 0.0):.6f}",
        f"state_std_sum={(features.get('sx_std', 0.0)+features.get('sy_std', 0.0)+features.get('sz_std', 0.0)):.6f}",
    ]
    return build_result(fault_type=FAULT_TYPE, score=score, reasons=reasons, features=features, min_samples=12)


if __name__ == "__main__":
    raise SystemExit(run_detector_cli(detect, description="制动器抖动识别（角振动峰峰值+振荡切换）"))
