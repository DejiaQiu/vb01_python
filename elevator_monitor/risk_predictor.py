from __future__ import annotations

import statistics
from collections import deque
from typing import Any, Optional

from .common import extract_features


def _clamp01(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def _robust_fit(values: list[float]) -> tuple[float, float]:
    med = statistics.median(values)
    abs_dev = [abs(v - med) for v in values]
    mad = statistics.median(abs_dev)
    if mad > 1e-9:
        scale = 1.4826 * mad
    else:
        std = statistics.pstdev(values) if len(values) > 1 else 0.0
        scale = max(std, 1e-6)
    return med, scale


def _parse_missing_ratio(reasons: list[str]) -> float:
    for reason in reasons:
        if reason.startswith("missing:"):
            try:
                return max(0.0, min(1.0, float(reason.split(":", 1)[1])))
            except ValueError:
                return 0.0
    return 0.0


def _risk_level(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.65:
        return "high"
    if score >= 0.40:
        return "watch"
    return "normal"


class OnlineRiskPredictor:
    def __init__(
        self,
        enabled: bool = True,
        stale_limit: int = 300,
        baseline_size: int = 3000,
        baseline_min_records: int = 200,
        trend_window_s: float = 1800.0,
        smooth_alpha: float = 0.08,
        anomaly_scale: float = 8.0,
        fault_weight: float = 0.25,
        vibration_weight: float = 0.20,
        temperature_weight: float = 0.10,
        model_weight: float = 0.35,
    ):
        self.enabled = enabled
        self.stale_limit = max(10, stale_limit)
        self.baseline_size = max(300, baseline_size)
        self.baseline_min_records = max(60, baseline_min_records)
        self.trend_window_ms = int(max(120.0, trend_window_s) * 1000)
        self.smooth_alpha = min(0.8, max(0.01, smooth_alpha))
        self.anomaly_scale = max(1.0, anomaly_scale)
        self.fault_weight = max(0.0, fault_weight)
        self.vibration_weight = max(0.0, vibration_weight)
        self.temperature_weight = max(0.0, temperature_weight)
        self.model_weight = max(0.0, model_weight)

        self._a_hist: deque[float] = deque(maxlen=self.baseline_size)
        self._g_hist: deque[float] = deque(maxlen=self.baseline_size)
        self._t_hist: deque[float] = deque(maxlen=self.baseline_size)
        self._a_stats: Optional[tuple[float, float]] = None
        self._g_stats: Optional[tuple[float, float]] = None
        self._t_stats: Optional[tuple[float, float]] = None

        self._smoothed_risk = 0.0
        self._history: deque[tuple[int, float]] = deque()
        self._updates = 0

    def _fit_baseline(self) -> None:
        if len(self._a_hist) >= self.baseline_min_records:
            self._a_stats = _robust_fit(list(self._a_hist))
        if len(self._g_hist) >= self.baseline_min_records:
            self._g_stats = _robust_fit(list(self._g_hist))
        if len(self._t_hist) >= self.baseline_min_records:
            self._t_stats = _robust_fit(list(self._t_hist))

    @staticmethod
    def _z(value: Optional[float], stats: Optional[tuple[float, float]]) -> Optional[float]:
        if value is None or stats is None:
            return None
        med, scale = stats
        return abs(value - med) / max(scale, 1e-6)

    def _trend_slope_per_hour(self) -> float:
        if len(self._history) < 2:
            return 0.0

        x = [(ts - self._history[0][0]) / 3_600_000.0 for ts, _ in self._history]
        y = [risk for _, risk in self._history]
        x_mean = statistics.fmean(x)
        y_mean = statistics.fmean(y)

        den = sum((xi - x_mean) ** 2 for xi in x)
        if den <= 1e-9:
            return 0.0
        num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
        return num / den

    def update(
        self,
        ts_ms: int,
        row: dict[str, Any],
        anomaly_result: dict[str, Any],
        fault_result: dict[str, Any],
        model_probability: Optional[float] = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {
                "risk_score": 0.0,
                "risk_level_now": "disabled",
                "risk_24h": 0.0,
                "risk_level_24h": "disabled",
                "degradation_slope": 0.0,
                "risk_reasons": "disabled",
            }

        self._updates += 1
        level = str(anomaly_result.get("level", "normal"))
        reasons = list(anomaly_result.get("reasons", []))

        feats = extract_features(row)
        a_mag = feats.get("A_mag")
        g_mag = feats.get("G_mag")
        temp = feats.get("T")

        if level == "normal":
            if a_mag is not None:
                self._a_hist.append(a_mag)
            if g_mag is not None:
                self._g_hist.append(g_mag)
            if temp is not None:
                self._t_hist.append(temp)

        if self._updates % 40 == 0 or self._a_stats is None or self._g_stats is None or self._t_stats is None:
            self._fit_baseline()

        anomaly_score = float(anomaly_result.get("score", 0.0))
        anomaly_component = _clamp01(anomaly_score / self.anomaly_scale)
        missing_component = _parse_missing_ratio(reasons)

        stale_repeat = int(anomaly_result.get("stale_repeat", 0))
        stale_component = _clamp01((stale_repeat - self.stale_limit) / max(1.0, self.stale_limit))

        vib_z_values = [
            z for z in (self._z(a_mag, self._a_stats), self._z(g_mag, self._g_stats)) if z is not None
        ]
        vib_component = _clamp01(max(vib_z_values) / 8.0) if vib_z_values else 0.0

        temp_z = self._z(temp, self._t_stats)
        temp_component = _clamp01((temp_z or 0.0) / 8.0)

        fault_type = str(fault_result.get("fault_type", "unknown"))
        fault_conf = float(fault_result.get("fault_confidence", 0.0))
        fault_component = 0.0 if fault_type in {"unknown", "disabled"} else _clamp01(fault_conf)
        model_component = _clamp01(float(model_probability)) if model_probability is not None else 0.0

        instant = (
            0.48 * anomaly_component
            + 0.14 * missing_component
            + 0.10 * stale_component
            + self.vibration_weight * vib_component
            + self.temperature_weight * temp_component
            + self.fault_weight * fault_component
            + self.model_weight * model_component
        )

        if level == "anomaly":
            instant = max(instant, 0.70)
        elif level == "warning":
            instant = max(instant, 0.45)

        instant = _clamp01(instant)
        self._smoothed_risk = _clamp01(self.smooth_alpha * instant + (1.0 - self.smooth_alpha) * self._smoothed_risk)
        if level == "anomaly":
            # 对强异常快速抬升风险，避免平滑导致响应过慢。
            self._smoothed_risk = max(self._smoothed_risk, instant * 0.75)
        elif level == "warning":
            self._smoothed_risk = max(self._smoothed_risk, instant * 0.45)

        self._history.append((ts_ms, self._smoothed_risk))
        cutoff = ts_ms - self.trend_window_ms
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

        slope = self._trend_slope_per_hour()
        risk_24h = _clamp01(self._smoothed_risk + slope * 24.0)

        explain: list[str] = []
        if anomaly_component >= 0.35:
            explain.append(f"anomaly={anomaly_component:.2f}")
        if vib_component >= 0.35:
            explain.append(f"vibration={vib_component:.2f}")
        if temp_component >= 0.35:
            explain.append(f"temperature={temp_component:.2f}")
        if missing_component >= 0.35:
            explain.append(f"missing={missing_component:.2f}")
        if stale_component >= 0.35:
            explain.append(f"stale={stale_component:.2f}")
        if slope >= 0.03:
            explain.append(f"slope_h={slope:.3f}")
        if fault_component >= 0.35:
            explain.append(f"fault={fault_type}:{fault_component:.2f}")
        if model_component >= 0.35:
            explain.append(f"risk_model={model_component:.2f}")

        return {
            "risk_score": float(self._smoothed_risk),
            "risk_level_now": _risk_level(self._smoothed_risk),
            "risk_24h": float(risk_24h),
            "risk_level_24h": _risk_level(risk_24h),
            "degradation_slope": float(slope),
            "risk_reasons": "|".join(explain),
        }

    def snapshot_state(self, max_items: int = 3000) -> dict[str, Any]:
        keep = max(100, max_items)
        return {
            "a_hist": list(self._a_hist)[-keep:],
            "g_hist": list(self._g_hist)[-keep:],
            "t_hist": list(self._t_hist)[-keep:],
            "smoothed_risk": self._smoothed_risk,
            "history": list(self._history)[-keep:],
            "updates": self._updates,
        }

    def load_state(self, state: Optional[dict[str, Any]]) -> None:
        if not state:
            return

        for value in state.get("a_hist", []):
            try:
                self._a_hist.append(float(value))
            except (TypeError, ValueError):
                continue
        for value in state.get("g_hist", []):
            try:
                self._g_hist.append(float(value))
            except (TypeError, ValueError):
                continue
        for value in state.get("t_hist", []):
            try:
                self._t_hist.append(float(value))
            except (TypeError, ValueError):
                continue

        for item in state.get("history", []):
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            try:
                self._history.append((int(item[0]), float(item[1])))
            except (TypeError, ValueError):
                continue

        try:
            self._smoothed_risk = _clamp01(float(state.get("smoothed_risk", self._smoothed_risk)))
        except (TypeError, ValueError):
            pass
        try:
            self._updates = max(self._updates, int(state.get("updates", 0)))
        except (TypeError, ValueError):
            pass

        self._fit_baseline()
