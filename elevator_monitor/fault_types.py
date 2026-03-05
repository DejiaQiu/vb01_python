from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from .common import extract_features, parse_float
from .data_recorder import format_ts_ms


LEVEL_RANK = {
    "normal": 0,
    "warning": 1,
    "anomaly": 2,
}


def _level_ge(level: str, minimum: str) -> bool:
    return LEVEL_RANK.get(level, 0) >= LEVEL_RANK.get(minimum, 1)


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


def _parse_missing_ratio(reasons: list[str]) -> Optional[float]:
    for reason in reasons:
        if reason.startswith("missing:"):
            try:
                return float(reason.split(":", 1)[1])
            except ValueError:
                return None
    return None


@dataclass
class FaultTypeCandidate:
    fault_type: str
    score: float
    source: str
    reasons: list[str]
    extras: dict[str, Any] = field(default_factory=dict)

    def compact(self) -> str:
        return f"{self.fault_type}:{self.score:.3f}@{self.source}"


class FaultTypeAlgorithm:
    name = "base"

    def update(self, row: dict[str, Any], anomaly_result: dict[str, Any]) -> Optional[FaultTypeCandidate]:
        raise NotImplementedError

    def snapshot_state(self, max_items: int = 2000) -> dict[str, Any]:
        _ = max_items
        return {}

    def load_state(self, state: Optional[dict[str, Any]]) -> None:
        _ = state

    def get_export_state(self) -> dict[str, Any]:
        return {}


class DataQualityFaultAlgorithm(FaultTypeAlgorithm):
    name = "data_quality_rules"

    def __init__(self, stale_limit: int = 300):
        self.stale_limit = max(10, stale_limit)

    def update(self, row: dict[str, Any], anomaly_result: dict[str, Any]) -> Optional[FaultTypeCandidate]:
        _ = row
        level = str(anomaly_result.get("level", "normal"))
        reasons = list(anomaly_result.get("reasons", []))
        stale_repeat = int(anomaly_result.get("stale_repeat", 0))
        miss_ratio = _parse_missing_ratio(reasons)

        if level == "normal":
            return None

        if miss_ratio is not None and miss_ratio >= 0.5:
            return FaultTypeCandidate(
                fault_type="sensor_missing",
                score=_clamp01(0.5 + miss_ratio / 2.0),
                source=self.name,
                reasons=[f"missing_ratio={miss_ratio:.2f}"],
            )

        if stale_repeat > self.stale_limit * 2:
            excess = stale_repeat - self.stale_limit * 2
            score = _clamp01(0.4 + excess / max(1.0, self.stale_limit))
            return FaultTypeCandidate(
                fault_type="signal_frozen",
                score=score,
                source=self.name,
                reasons=[f"stale_repeat={stale_repeat}"],
            )

        return None


class VibrationFaultAlgorithm(FaultTypeAlgorithm):
    name = "vibration_rules"

    def __init__(
        self,
        baseline_size: int = 2000,
        baseline_min_records: int = 120,
        warning_z: float = 3.0,
        shock_z: float = 6.0,
    ):
        self.baseline_size = max(200, baseline_size)
        self.baseline_min_records = max(40, baseline_min_records)
        self.warning_z = max(1.5, warning_z)
        self.shock_z = max(self.warning_z + 0.5, shock_z)

        self._a_hist: deque[float] = deque(maxlen=self.baseline_size)
        self._g_hist: deque[float] = deque(maxlen=self.baseline_size)
        self._a_stats: Optional[tuple[float, float]] = None
        self._g_stats: Optional[tuple[float, float]] = None
        self._updates = 0

    def _fit(self) -> None:
        if len(self._a_hist) >= self.baseline_min_records:
            self._a_stats = _robust_fit(list(self._a_hist))
        if len(self._g_hist) >= self.baseline_min_records:
            self._g_stats = _robust_fit(list(self._g_hist))

    def _z(self, value: Optional[float], stats: Optional[tuple[float, float]]) -> Optional[float]:
        if value is None or stats is None:
            return None
        med, scale = stats
        return abs(value - med) / max(scale, 1e-6)

    def update(self, row: dict[str, Any], anomaly_result: dict[str, Any]) -> Optional[FaultTypeCandidate]:
        self._updates += 1
        level = str(anomaly_result.get("level", "normal"))

        feats = extract_features(row)
        a_mag = feats.get("A_mag")
        g_mag = feats.get("G_mag")

        # 用正常样本持续更新基线，避免异常污染。
        if level == "normal":
            if a_mag is not None:
                self._a_hist.append(a_mag)
            if g_mag is not None:
                self._g_hist.append(g_mag)

        if self._updates % 40 == 0 or self._a_stats is None or self._g_stats is None:
            self._fit()

        if level == "normal":
            return None

        z_vals = [z for z in (self._z(a_mag, self._a_stats), self._z(g_mag, self._g_stats)) if z is not None]
        if not z_vals:
            return None

        max_z = max(z_vals)
        if max_z >= self.shock_z:
            return FaultTypeCandidate(
                fault_type="impact_shock",
                score=_clamp01(max_z / (self.shock_z * 1.5)),
                source=self.name,
                reasons=[f"max_z={max_z:.2f}", "pattern=spike"],
            )
        if max_z >= self.warning_z:
            return FaultTypeCandidate(
                fault_type="vibration_increase",
                score=_clamp01(max_z / (self.shock_z * 1.2)),
                source=self.name,
                reasons=[f"max_z={max_z:.2f}", "pattern=sustained"],
            )

        return None

    def snapshot_state(self, max_items: int = 2000) -> dict[str, Any]:
        keep = max(40, max_items)
        return {
            "a_hist": list(self._a_hist)[-keep:],
            "g_hist": list(self._g_hist)[-keep:],
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
        try:
            self._updates = max(self._updates, int(state.get("updates", 0)))
        except (TypeError, ValueError):
            pass
        self._fit()


class RailWearFaultAlgorithm(FaultTypeAlgorithm):
    name = "rail_wear_rules"

    def __init__(
        self,
        baseline_size: int = 2000,
        baseline_min_records: int = 120,
        sample_hz: float = 100.0,
        normal_ratio_max: float = 1.1,
        warning_ratio_max: float = 1.4,
        smooth_alpha: float = 0.18,
        rms_window_s: float = 3.0,
        growth_window_s: float = 1800.0,
        confirm_s: float = 0.6,
    ):
        self.baseline_size = max(200, baseline_size)
        self.baseline_min_records = max(40, baseline_min_records)
        self.sample_hz = max(1.0, float(sample_hz))
        self.normal_ratio_max = max(1.01, float(normal_ratio_max))
        self.warning_ratio_max = max(self.normal_ratio_max + 0.05, float(warning_ratio_max))
        self.smooth_alpha = min(1.0, max(0.01, float(smooth_alpha)))
        self.growth_window_ms = int(max(60.0, float(growth_window_s)) * 1000.0)
        self.confirm_samples = max(5, int(self.sample_hz * max(0.05, float(confirm_s))))

        rms_win = max(20, int(self.sample_hz * max(0.5, float(rms_window_s))))
        self._a_window: deque[float] = deque(maxlen=rms_win)
        self._g_window: deque[float] = deque(maxlen=rms_win)
        self._metric_baseline: deque[float] = deque(maxlen=self.baseline_size)
        self._ratio_hist: deque[tuple[int, float]] = deque(maxlen=max(200, self.baseline_size * 3))

        self._metric_med: Optional[float] = None
        self._smoothed_metric: Optional[float] = None
        self._ratio_over_normal_count = 0
        self._ratio_over_warning_count = 0
        self._trip_id = 0
        self._baseline_start_ts_ms: Optional[int] = None
        self._last_export: dict[str, Any] = {}

    @staticmethod
    def _detrended_rms(values: deque[float]) -> Optional[float]:
        if len(values) < 8:
            return None
        mean = statistics.fmean(values)
        return math.sqrt(statistics.fmean((v - mean) ** 2 for v in values))

    def _fit(self) -> None:
        if len(self._metric_baseline) >= self.baseline_min_records:
            self._metric_med = statistics.median(self._metric_baseline)

    def _estimate_daily_growth(self, ts_ms: Optional[int], ratio: float) -> float:
        if ts_ms is None or ts_ms <= 0:
            return 0.0
        self._ratio_hist.append((int(ts_ms), float(ratio)))
        cutoff = int(ts_ms) - self.growth_window_ms
        while self._ratio_hist and self._ratio_hist[0][0] < cutoff:
            self._ratio_hist.popleft()
        if len(self._ratio_hist) < 2:
            return 0.0

        first_ts, first_ratio = self._ratio_hist[0]
        dt_days = (int(ts_ms) - first_ts) / 86_400_000.0
        if dt_days <= 1e-9 or first_ratio <= 1e-9:
            return 0.0
        return ((ratio - first_ratio) / first_ratio) * 100.0 / dt_days

    def update(self, row: dict[str, Any], anomaly_result: dict[str, Any]) -> Optional[FaultTypeCandidate]:
        level = str(anomaly_result.get("level", "normal"))
        ts_ms = int(parse_float(row.get("ts_ms")) or 0)
        feats = extract_features(row)
        a_mag = feats.get("A_mag")
        g_mag = feats.get("G_mag")
        if a_mag is not None:
            self._a_window.append(float(a_mag))
        if g_mag is not None:
            self._g_window.append(float(g_mag))

        a_rms = self._detrended_rms(self._a_window)
        g_rms = self._detrended_rms(self._g_window)
        if a_rms is None and g_rms is None:
            return None

        # 100Hz 设备下以低频波动强度做代理特征，组合加速度与角速度抖动。
        metric = 0.0
        if a_rms is not None:
            metric += a_rms
        if g_rms is not None:
            metric += 0.15 * g_rms

        if self._smoothed_metric is None:
            self._smoothed_metric = metric
        else:
            self._smoothed_metric = self.smooth_alpha * metric + (1.0 - self.smooth_alpha) * self._smoothed_metric

        self._trip_id += 1

        if level == "normal":
            self._metric_baseline.append(self._smoothed_metric)
            if len(self._metric_baseline) % 40 == 0 or self._metric_med is None:
                self._fit()
                if self._metric_med is not None and self._baseline_start_ts_ms is None and ts_ms > 0:
                    self._baseline_start_ts_ms = ts_ms
            self._ratio_over_normal_count = 0
            self._ratio_over_warning_count = 0
            baseline_ratio = 0.0
            if self._metric_med is not None:
                baseline_ratio = float(self._smoothed_metric / max(1e-6, self._metric_med))
            days_since_baseline = 0
            if self._baseline_start_ts_ms is not None and ts_ms > 0:
                days_since_baseline = max(0, int((ts_ms - self._baseline_start_ts_ms) // 86_400_000))
            self._last_export = {
                "trip_id": self._trip_id,
                "timestamp": format_ts_ms(ts_ms) if ts_ms > 0 else "",
                "rms_0_20hz": float(a_rms or 0.0),
                "smoothed_rms": float(self._smoothed_metric or 0.0),
                "baseline_ratio": baseline_ratio,
                "alarm_flag": 0,
                "fault_status": "normal",
                "days_since_baseline": days_since_baseline,
            }
            return None

        if self._metric_med is None:
            self._last_export = {
                "trip_id": self._trip_id,
                "timestamp": format_ts_ms(ts_ms) if ts_ms > 0 else "",
                "rms_0_20hz": float(a_rms or 0.0),
                "smoothed_rms": float(self._smoothed_metric or 0.0),
                "baseline_ratio": 0.0,
                "alarm_flag": 0,
                "fault_status": "normal",
                "days_since_baseline": 0,
            }
            return None

        baseline = max(1e-6, self._metric_med)
        ratio = self._smoothed_metric / baseline
        growth = self._estimate_daily_growth(int(parse_float(row.get("ts_ms")) or 0), ratio)

        if ratio >= self.normal_ratio_max:
            self._ratio_over_normal_count += 1
        else:
            self._ratio_over_normal_count = 0

        if ratio >= self.warning_ratio_max:
            self._ratio_over_warning_count += 1
        else:
            self._ratio_over_warning_count = 0

        status = "normal"
        if self._ratio_over_warning_count >= self.confirm_samples:
            status = "critical"
        elif self._ratio_over_normal_count >= self.confirm_samples:
            status = "warning"
        alarm_flag = 1 if status != "normal" else 0

        days_since_baseline = 0
        if self._baseline_start_ts_ms is not None and ts_ms > 0:
            days_since_baseline = max(0, int((ts_ms - self._baseline_start_ts_ms) // 86_400_000))

        self._last_export = {
            "trip_id": self._trip_id,
            "timestamp": format_ts_ms(ts_ms) if ts_ms > 0 else "",
            "rms_0_20hz": float(a_rms or 0.0),
            "smoothed_rms": float(self._smoothed_metric or 0.0),
            "baseline_ratio": float(ratio),
            "alarm_flag": alarm_flag,
            "fault_status": status,
            "days_since_baseline": days_since_baseline,
        }

        # 连续确认，避免单个冲击点被误判为导轨磨损。
        if self._ratio_over_warning_count >= self.confirm_samples:
            score = _clamp01(0.72 + (ratio - self.warning_ratio_max) * 0.35 + max(0.0, growth) / 180.0)
            return FaultTypeCandidate(
                fault_type="rail_wear_critical",
                score=score,
                source=self.name,
                reasons=[
                    f"ratio={ratio:.3f}",
                    f"daily_growth={growth:.2f}",
                    f"baseline_metric={baseline:.5f}",
                ],
                extras={
                    "rms_0_20hz": float(a_rms or 0.0),
                    "smoothed_rms": float(self._smoothed_metric or 0.0),
                    "baseline_ratio": float(ratio),
                    "alarm_flag": 1,
                    "fault_status": "critical",
                    "days_since_baseline": days_since_baseline,
                    "trip_id": self._trip_id,
                    "timestamp": format_ts_ms(ts_ms) if ts_ms > 0 else "",
                },
            )

        if self._ratio_over_normal_count >= self.confirm_samples:
            span = max(0.05, self.warning_ratio_max - self.normal_ratio_max)
            ratio_part = (ratio - self.normal_ratio_max) / span
            score = _clamp01(0.46 + ratio_part * 0.26 + max(0.0, growth) / 260.0)
            return FaultTypeCandidate(
                fault_type="rail_wear_warning",
                score=score,
                source=self.name,
                reasons=[
                    f"ratio={ratio:.3f}",
                    f"daily_growth={growth:.2f}",
                    f"baseline_metric={baseline:.5f}",
                ],
                extras={
                    "rms_0_20hz": float(a_rms or 0.0),
                    "smoothed_rms": float(self._smoothed_metric or 0.0),
                    "baseline_ratio": float(ratio),
                    "alarm_flag": 1,
                    "fault_status": "warning",
                    "days_since_baseline": days_since_baseline,
                    "trip_id": self._trip_id,
                    "timestamp": format_ts_ms(ts_ms) if ts_ms > 0 else "",
                },
            )
        return None

    def snapshot_state(self, max_items: int = 2000) -> dict[str, Any]:
        keep = max(40, max_items)
        return {
            "a_window": list(self._a_window)[-keep:],
            "g_window": list(self._g_window)[-keep:],
            "metric_baseline": list(self._metric_baseline)[-keep:],
            "ratio_hist": [[ts, ratio] for ts, ratio in list(self._ratio_hist)[-keep:]],
            "smoothed_metric": self._smoothed_metric,
            "ratio_over_normal_count": self._ratio_over_normal_count,
            "ratio_over_warning_count": self._ratio_over_warning_count,
            "trip_id": self._trip_id,
            "baseline_start_ts_ms": self._baseline_start_ts_ms,
            "last_export": dict(self._last_export),
        }

    def load_state(self, state: Optional[dict[str, Any]]) -> None:
        if not state:
            return
        for value in state.get("a_window", []):
            try:
                self._a_window.append(float(value))
            except (TypeError, ValueError):
                continue
        for value in state.get("g_window", []):
            try:
                self._g_window.append(float(value))
            except (TypeError, ValueError):
                continue
        for value in state.get("metric_baseline", []):
            try:
                self._metric_baseline.append(float(value))
            except (TypeError, ValueError):
                continue
        for item in state.get("ratio_hist", []):
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            try:
                self._ratio_hist.append((int(item[0]), float(item[1])))
            except (TypeError, ValueError):
                continue

        smoothed = parse_float(state.get("smoothed_metric"))
        if smoothed is not None:
            self._smoothed_metric = smoothed
        try:
            self._ratio_over_normal_count = max(0, int(state.get("ratio_over_normal_count", 0)))
            self._ratio_over_warning_count = max(0, int(state.get("ratio_over_warning_count", 0)))
        except (TypeError, ValueError):
            self._ratio_over_normal_count = 0
            self._ratio_over_warning_count = 0
        try:
            self._trip_id = max(0, int(state.get("trip_id", 0)))
        except (TypeError, ValueError):
            self._trip_id = 0
        baseline_ts = parse_float(state.get("baseline_start_ts_ms"))
        self._baseline_start_ts_ms = int(baseline_ts) if baseline_ts is not None and baseline_ts > 0 else None
        last_export = state.get("last_export")
        if isinstance(last_export, dict):
            self._last_export = dict(last_export)
        self._fit()

    def get_export_state(self) -> dict[str, Any]:
        return dict(self._last_export)


class TemperatureFaultAlgorithm(FaultTypeAlgorithm):
    name = "temperature_rules"

    def __init__(
        self,
        baseline_size: int = 2000,
        baseline_min_records: int = 120,
        rise_threshold_c: float = 6.0,
        overheat_c: float = 45.0,
    ):
        self.baseline_size = max(200, baseline_size)
        self.baseline_min_records = max(40, baseline_min_records)
        self.rise_threshold_c = max(1.0, rise_threshold_c)
        self.overheat_c = max(20.0, overheat_c)

        self._temp_hist: deque[float] = deque(maxlen=self.baseline_size)
        self._temp_med: Optional[float] = None

    def _fit(self) -> None:
        if len(self._temp_hist) >= self.baseline_min_records:
            self._temp_med = statistics.median(self._temp_hist)

    def update(self, row: dict[str, Any], anomaly_result: dict[str, Any]) -> Optional[FaultTypeCandidate]:
        level = str(anomaly_result.get("level", "normal"))
        temp = parse_float(row.get("t"))
        if temp is None:
            return None

        if level == "normal":
            self._temp_hist.append(temp)
            if len(self._temp_hist) % 40 == 0 or self._temp_med is None:
                self._fit()
            return None

        if temp >= self.overheat_c:
            return FaultTypeCandidate(
                fault_type="temperature_overheat",
                score=_clamp01((temp - self.overheat_c) / 15.0 + 0.6),
                source=self.name,
                reasons=[f"temp_c={temp:.2f}", f"overheat_c={self.overheat_c:.2f}"],
            )

        if self._temp_med is not None:
            rise = temp - self._temp_med
            if rise >= self.rise_threshold_c:
                return FaultTypeCandidate(
                    fault_type="temperature_rise",
                    score=_clamp01(rise / (self.rise_threshold_c * 2.0) + 0.3),
                    source=self.name,
                    reasons=[f"temp_rise_c={rise:.2f}", f"baseline_t={self._temp_med:.2f}"],
                )

        return None

    def snapshot_state(self, max_items: int = 2000) -> dict[str, Any]:
        keep = max(40, max_items)
        return {
            "temp_hist": list(self._temp_hist)[-keep:],
        }

    def load_state(self, state: Optional[dict[str, Any]]) -> None:
        if not state:
            return
        for value in state.get("temp_hist", []):
            try:
                self._temp_hist.append(float(value))
            except (TypeError, ValueError):
                continue
        self._fit()


class FaultTypeEngine:
    def __init__(
        self,
        enabled: bool = True,
        min_level: str = "warning",
        top_k: int = 3,
        stale_limit: int = 300,
        baseline_size: int = 2000,
        baseline_min_records: int = 120,
        vibration_warning_z: float = 3.0,
        vibration_shock_z: float = 6.0,
        temp_rise_c: float = 6.0,
        temp_overheat_c: float = 45.0,
        sample_hz: float = 100.0,
    ):
        self.enabled = enabled
        self.min_level = min_level if min_level in LEVEL_RANK else "warning"
        self.top_k = max(1, top_k)

        self.rail_wear_algorithm = RailWearFaultAlgorithm(
            baseline_size=baseline_size,
            baseline_min_records=baseline_min_records,
            sample_hz=sample_hz,
        )

        self.algorithms: list[FaultTypeAlgorithm] = [
            DataQualityFaultAlgorithm(stale_limit=stale_limit),
            VibrationFaultAlgorithm(
                baseline_size=baseline_size,
                baseline_min_records=baseline_min_records,
                warning_z=vibration_warning_z,
                shock_z=vibration_shock_z,
            ),
            self.rail_wear_algorithm,
            TemperatureFaultAlgorithm(
                baseline_size=baseline_size,
                baseline_min_records=baseline_min_records,
                rise_threshold_c=temp_rise_c,
                overheat_c=temp_overheat_c,
            ),
        ]

    def update(self, row: dict[str, Any], anomaly_result: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {
                "fault_type": "disabled",
                "fault_confidence": 0.0,
                "fault_source": "disabled",
                "fault_candidates": "",
                "fault_reasons": "",
            }

        level = str(anomaly_result.get("level", "normal"))
        allow_emit = _level_ge(level, self.min_level)
        candidates: list[FaultTypeCandidate] = []

        for algorithm in self.algorithms:
            candidate = algorithm.update(row, anomaly_result)
            if candidate is not None and allow_emit:
                candidates.append(candidate)

        if not candidates:
            return {
                "fault_type": "unknown",
                "fault_confidence": 0.0,
                "fault_source": "none",
                "fault_candidates": "",
                "fault_reasons": "",
            }

        candidates.sort(key=lambda x: x.score, reverse=True)
        top = candidates[0]
        top_k = candidates[: self.top_k]

        return {
            "fault_type": top.fault_type,
            "fault_confidence": float(top.score),
            "fault_source": top.source,
            "fault_candidates": "|".join(c.compact() for c in top_k),
            "fault_reasons": "|".join(top.reasons),
        }

    def snapshot_state(self, max_items: int = 2000) -> dict[str, Any]:
        return {
            "algorithms": {algo.name: algo.snapshot_state(max_items=max_items) for algo in self.algorithms},
        }

    def load_state(self, state: Optional[dict[str, Any]]) -> None:
        if not state:
            return
        algo_states = state.get("algorithms", {})
        for algo in self.algorithms:
            algo.load_state(algo_states.get(algo.name))

    def get_rail_wear_export_row(self) -> dict[str, Any]:
        return self.rail_wear_algorithm.get_export_state()
