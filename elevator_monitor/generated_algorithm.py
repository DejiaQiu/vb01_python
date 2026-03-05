from __future__ import annotations

import json
import math
import statistics
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


_EPS = 1e-9
_DEFAULT_FORECAST_FEATURES = ("A_mag_mean", "G_mag_mean", "T_mean")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class GeneratedAlgorithmPrediction:
    label: str
    confidence: float
    top_k: str
    probabilities: dict[str, float]
    best_score: float
    threshold: float


@dataclass(frozen=True)
class ForecastResult:
    horizon_s: float
    values: dict[str, float]
    slopes: dict[str, float]
    confidence: float


@dataclass(frozen=True)
class _ClassRule:
    label: str
    sample_count: int
    prototype: dict[str, float]
    weights: dict[str, float]
    min_score: float


class GeneratedFaultAlgorithmRunner:
    def __init__(self, algorithm_path: str):
        path = Path(algorithm_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"algorithm not found: {path}")
        self.path = str(path)
        payload = json.loads(path.read_text(encoding="utf-8"))

        self.version = int(payload.get("version", 1))
        self.algorithm_type = str(payload.get("algorithm_type", "generated_fault_algorithm_v1"))
        self.feature_names = [str(x) for x in payload.get("feature_names", []) if str(x).strip()]

        normal_stats = payload.get("normal_stats", {})
        mean_raw = normal_stats.get("mean", {}) if isinstance(normal_stats, dict) else {}
        std_raw = normal_stats.get("std", {}) if isinstance(normal_stats, dict) else {}
        self.normal_mean = {str(k): _safe_float(v, 0.0) for k, v in (mean_raw or {}).items()}
        self.normal_std = {str(k): max(_EPS, _safe_float(v, 1.0)) for k, v in (std_raw or {}).items()}

        classes: list[_ClassRule] = []
        for item in payload.get("classes", []):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            if not label:
                continue
            proto_raw = item.get("prototype", {})
            weight_raw = item.get("weights", {})
            prototype = {str(k): _safe_float(v, 0.0) for k, v in (proto_raw or {}).items()}
            weights = {str(k): max(0.0, _safe_float(v, 0.0)) for k, v in (weight_raw or {}).items()}
            classes.append(
                _ClassRule(
                    label=label,
                    sample_count=max(1, int(_safe_float(item.get("sample_count"), 1))),
                    prototype=prototype,
                    weights=weights,
                    min_score=max(0.0, _safe_float(item.get("min_score"), 0.0)),
                )
            )
        if not classes:
            raise ValueError(f"invalid generated algorithm: no classes in {path}")
        self.classes = classes
        self.name = path.stem

    def _class_score(self, cls: _ClassRule, features: dict[str, float]) -> float:
        weighted_dist = 0.0
        weight_sum = 0.0
        for name, center in cls.prototype.items():
            weight = cls.weights.get(name, 0.0)
            if weight <= 0.0:
                continue
            value = _safe_float(features.get(name), self.normal_mean.get(name, 0.0))
            scale = max(_EPS, self.normal_std.get(name, 1.0))
            # 用 normal 的标准差做归一化距离，避免不同量纲特征直接相加失真。
            weighted_dist += weight * abs(value - center) / scale
            weight_sum += weight

        if weight_sum <= _EPS:
            return 0.0
        dist = weighted_dist / weight_sum
        # 距离 -> 相似分数（越接近原型，分数越接近 1）。
        return math.exp(-dist)

    def predict(self, features: dict[str, float], top_k: int = 3) -> Optional[GeneratedAlgorithmPrediction]:
        if not features:
            return None

        score_by_label = {cls.label: self._class_score(cls, features) for cls in self.classes}
        score_sum = sum(score_by_label.values())
        if score_sum <= _EPS:
            return None

        probabilities = {label: score / score_sum for label, score in score_by_label.items()}
        ranked = sorted(probabilities.items(), key=lambda x: x[1], reverse=True)
        best_label, confidence = ranked[0]
        top = "|".join(f"{label}:{prob:.3f}" for label, prob in ranked[: max(1, top_k)])

        best_rule = next((cls for cls in self.classes if cls.label == best_label), None)
        best_score = score_by_label.get(best_label, 0.0)
        threshold = float(best_rule.min_score if best_rule is not None else 0.0)

        # 分数低于该类阈值时，不强行归类，输出 unknown 作为兜底。
        label = best_label if best_score >= threshold else "unknown"
        return GeneratedAlgorithmPrediction(
            label=label,
            confidence=float(confidence),
            top_k=top,
            probabilities=probabilities,
            best_score=float(best_score),
            threshold=threshold,
        )


class OnlineFeatureForecaster:
    def __init__(
        self,
        *,
        feature_names: Optional[list[str]] = None,
        horizon_s: float = 30.0,
        min_points: int = 10,
        max_points: int = 300,
    ):
        names = feature_names or list(_DEFAULT_FORECAST_FEATURES)
        self.feature_names = [str(name) for name in names if str(name).strip()]
        self.horizon_s = max(1.0, float(horizon_s))
        self.min_points = max(3, int(min_points))
        self._history: deque[tuple[int, dict[str, float]]] = deque(maxlen=max(self.min_points * 2, int(max_points)))

    @property
    def history_size(self) -> int:
        return len(self._history)

    def _fit_line(self, xs: list[float], ys: list[float]) -> tuple[float, float]:
        if len(xs) < 2:
            return 0.0, 0.0
        mx = statistics.fmean(xs)
        my = statistics.fmean(ys)
        sxx = sum((x - mx) * (x - mx) for x in xs)
        if sxx <= _EPS:
            return 0.0, 0.0
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        # 一阶线性拟合得到 slope（单位：每秒变化量）。
        slope = sxy / sxx

        y_hat = [my + slope * (x - mx) for x in xs]
        ss_tot = sum((y - my) ** 2 for y in ys)
        if ss_tot <= _EPS:
            r2 = 0.0
        else:
            ss_res = sum((y - yh) ** 2 for y, yh in zip(ys, y_hat))
            r2 = max(0.0, min(1.0, 1.0 - ss_res / ss_tot))
        return float(slope), float(r2)

    def update(self, ts_ms: int, features: Optional[dict[str, float]]) -> Optional[ForecastResult]:
        if features is None:
            return None

        values = {name: _safe_float(features.get(name), 0.0) for name in self.feature_names}
        self._history.append((int(ts_ms), values))

        if len(self._history) == 1:
            return ForecastResult(
                horizon_s=self.horizon_s,
                values=values,
                slopes={name: 0.0 for name in self.feature_names},
                confidence=0.0,
            )

        t0 = self._history[0][0]
        xs = [(item_ts - t0) / 1000.0 for item_ts, _ in self._history]
        last_values = self._history[-1][1]

        pred_values: dict[str, float] = {}
        slopes: dict[str, float] = {}
        r2_list: list[float] = []
        for name in self.feature_names:
            ys = [item_values.get(name, 0.0) for _, item_values in self._history]
            slope, r2 = self._fit_line(xs, ys)
            slopes[name] = slope
            # 线性外推到 horizon_s 秒后的特征值。
            pred_values[name] = float(last_values.get(name, 0.0) + slope * self.horizon_s)
            r2_list.append(r2)

        # 预测置信度=拟合优度均值 * 样本充足度。
        base_conf = statistics.fmean(r2_list) if r2_list else 0.0
        sample_factor = min(1.0, len(self._history) / float(self.min_points))
        confidence = max(0.0, min(1.0, base_conf * sample_factor))

        return ForecastResult(
            horizon_s=self.horizon_s,
            values=pred_values,
            slopes=slopes,
            confidence=float(confidence),
        )

    def snapshot_state(self, max_items: int = 1000) -> dict[str, Any]:
        items = []
        for ts_ms, values in list(self._history)[-max(1, int(max_items)) :]:
            items.append({"ts_ms": ts_ms, "values": dict(values)})
        return {
            "feature_names": list(self.feature_names),
            "horizon_s": self.horizon_s,
            "min_points": self.min_points,
            "history": items,
        }

    def load_state(self, state: Any) -> None:
        if not isinstance(state, dict):
            return
        history = state.get("history", [])
        if not isinstance(history, list):
            return
        self._history.clear()
        for item in history[-self._history.maxlen :]:
            if not isinstance(item, dict):
                continue
            ts_ms = int(_safe_float(item.get("ts_ms"), 0.0))
            values_raw = item.get("values", {})
            if ts_ms <= 0 or not isinstance(values_raw, dict):
                continue
            values = {name: _safe_float(values_raw.get(name), 0.0) for name in self.feature_names}
            self._history.append((ts_ms, values))


__all__ = [
    "ForecastResult",
    "GeneratedAlgorithmPrediction",
    "GeneratedFaultAlgorithmRunner",
    "OnlineFeatureForecaster",
]
