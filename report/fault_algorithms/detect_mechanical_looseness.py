from __future__ import annotations

try:
    from ._base import build_result, ratio_to_100, run_detector_cli
except ImportError:  # pragma: no cover
    from _base import build_result, ratio_to_100, run_detector_cli


FAULT_TYPE = "mechanical_looseness"


def detect(features: dict) -> dict:
    # 机械松动常见表现：
    # 1) 振动离散度上升（a_std）
    # 2) 冲击尖峰更明显（a_crest、a_kurt）
    # 3) 邻近采样变化更剧烈（jerk_rms）
    # 下面把每个原始特征按经验阈值映射到 0~100，便于统一加权。
    s_std = ratio_to_100(features.get("a_std", 0.0), 0.006, 0.030)
    s_crest = ratio_to_100(features.get("a_crest", 0.0), 2.2, 5.2)
    s_kurt = ratio_to_100(features.get("a_kurt", 0.0), 0.3, 4.0)
    s_jerk = ratio_to_100(features.get("jerk_rms", 0.0), 0.010, 0.200)

    # 加权融合：
    # - a_std 权重最高（0.35），优先反映整体振动波动增强；
    # - a_crest/a_kurt 用于刻画尖峰与重尾冲击；
    # - jerk_rms 捕捉瞬态变化，避免只看静态幅值。
    score = 0.35 * s_std + 0.25 * s_crest + 0.20 * s_kurt + 0.20 * s_jerk

    # reasons 直接回传关键原始特征，方便现场复盘时核对依据。
    reasons = [
        f"a_std={features.get('a_std', 0.0):.6f}",
        f"a_crest={features.get('a_crest', 0.0):.3f}",
        f"a_kurt={features.get('a_kurt', 0.0):.3f}",
        f"jerk_rms={features.get('jerk_rms', 0.0):.6f}",
    ]
    # min_samples=12：样本不足时由 build_result 自动下调质量因子，降低误判风险。
    return build_result(fault_type=FAULT_TYPE, score=score, reasons=reasons, features=features, min_samples=12)


if __name__ == "__main__":
    raise SystemExit(run_detector_cli(detect, description="机械松动识别（振动幅值+尖峰性+冲击变化率）"))
