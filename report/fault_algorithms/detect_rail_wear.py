from __future__ import annotations

try:
    from ._base import build_result, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, ratio_to_100, run_detector_cli


FAULT_TYPE = "guide_rail_wear"


def detect(features: dict) -> dict:
    s_lateral = ratio_to_100(features.get("lateral_ratio", 0.0), 0.9, 2.2)
    s_rms = ratio_to_100(features.get("a_rms_ac", 0.0), 0.004, 0.025)
    s_continuous = ratio_to_100(features.get("zc_rate_hz", 0.0), 0.2, 6.0)
    anti_shock = 100.0 - ratio_to_100(features.get("a_crest", 0.0), 3.8, 7.0)

    score = 0.40 * s_lateral + 0.30 * s_rms + 0.20 * s_continuous + 0.10 * anti_shock
    reasons = [
        f"lateral_ratio={features.get('lateral_ratio', 0.0):.4f}",
        f"a_rms_ac={features.get('a_rms_ac', 0.0):.6f}",
        f"zc_rate_hz={features.get('zc_rate_hz', 0.0):.6f}",
        f"a_crest={features.get('a_crest', 0.0):.3f}",
    ]
    return build_result(fault_type=FAULT_TYPE, score=score, reasons=reasons, features=features, min_samples=14)


if __name__ == "__main__":
    raise SystemExit(run_detector_cli(detect, description="导轨磨损识别（横向振动占比+持续波动）"))
