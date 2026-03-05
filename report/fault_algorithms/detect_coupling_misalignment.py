from __future__ import annotations

try:
    from ._base import build_result, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, ratio_to_100, run_detector_cli


FAULT_TYPE = "coupling_misalignment"


def detect(features: dict) -> dict:
    s_corr_x = ratio_to_100(features.get("gx_ax_corr", 0.0), 0.35, 0.95)
    s_corr_y = ratio_to_100(features.get("gy_ay_corr", 0.0), 0.35, 0.95)
    s_lateral = ratio_to_100(features.get("lateral_ratio", 0.0), 0.8, 2.0)
    s_gstd = ratio_to_100(features.get("g_std", 0.0), 0.06, 0.60)

    score = 0.30 * s_corr_x + 0.30 * s_corr_y + 0.20 * s_lateral + 0.20 * s_gstd
    reasons = [
        f"gx_ax_corr={features.get('gx_ax_corr', 0.0):.4f}",
        f"gy_ay_corr={features.get('gy_ay_corr', 0.0):.4f}",
        f"lateral_ratio={features.get('lateral_ratio', 0.0):.4f}",
        f"g_std={features.get('g_std', 0.0):.6f}",
    ]
    return build_result(fault_type=FAULT_TYPE, score=score, reasons=reasons, features=features, min_samples=14)


if __name__ == "__main__":
    raise SystemExit(run_detector_cli(detect, description="联轴器不对中识别（平动-转动耦合增强）"))
