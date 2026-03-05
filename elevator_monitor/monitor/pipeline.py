from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

from ..common import CORE_FIELDS, FEATURE_FIELDS, core_signature, extract_features, missing_ratio, parse_float


@dataclass
class RobustStats:
    median: float
    scale: float


class OnlineAnomalyDetector:
    def __init__(
        self,
        baseline_size: int = 5000,
        baseline_min_records: int = 300,
        baseline_refresh_every: int = 200,
        stale_limit: int = 300,
        warning_z: float = 3.5,
        anomaly_z: float = 6.0,
    ):
        self.baseline_size = max(100, baseline_size)
        self.baseline_min_records = max(20, baseline_min_records)
        self.baseline_refresh_every = max(20, baseline_refresh_every)

        self.stale_limit = max(10, stale_limit)
        self.warning_z = warning_z
        self.anomaly_z = anomaly_z

        self._baseline_buffer: deque[dict[str, Optional[float]]] = deque(maxlen=self.baseline_size)
        self._baseline_stats: dict[str, RobustStats] = {}

        self._prev_sig: Optional[tuple[str, ...]] = None
        self._stale_repeat = 0
        self._processed = 0

    @property
    def baseline_ready(self) -> bool:
        return bool(self._baseline_stats)

    @property
    def stale_repeat(self) -> int:
        return self._stale_repeat

    @property
    def baseline_count(self) -> int:
        return len(self._baseline_buffer)

    def _fit_baseline(self) -> None:
        feature_values: dict[str, list[float]] = {k: [] for k in FEATURE_FIELDS}
        for feats in self._baseline_buffer:
            for k in FEATURE_FIELDS:
                v = feats.get(k)
                if v is not None:
                    feature_values[k].append(v)

        baseline: dict[str, RobustStats] = {}
        for k, values in feature_values.items():
            if len(values) < 20:
                continue
            med = statistics.median(values)
            abs_dev = [abs(v - med) for v in values]
            mad = statistics.median(abs_dev)
            if mad > 1e-9:
                scale = 1.4826 * mad
            else:
                std = statistics.pstdev(values) if len(values) > 1 else 0.0
                scale = max(std, 1e-6)
            baseline[k] = RobustStats(median=med, scale=scale)

        self._baseline_stats = baseline

    def update(self, row: dict[str, Any]) -> dict[str, Any]:
        self._processed += 1
        reasons: list[str] = []

        miss_ratio = missing_ratio(row)
        missing_penalty = 0.0
        if miss_ratio > 0:
            missing_penalty = miss_ratio * 8.0
            reasons.append(f"missing:{miss_ratio:.2f}")

        sig = core_signature(row)
        if sig is not None and sig == self._prev_sig:
            self._stale_repeat += 1
        else:
            self._stale_repeat = 1 if sig is not None else 0
            self._prev_sig = sig

        feats = extract_features(row)

        z_vals: list[float] = []
        high_z: list[str] = []
        for key, stats in self._baseline_stats.items():
            v = feats.get(key)
            if v is None:
                continue
            z = abs(v - stats.median) / stats.scale if stats.scale > 0 else 0.0
            z_vals.append(z)
            if z >= self.warning_z:
                high_z.append(f"{key}:{z:.1f}")

        if z_vals:
            max_z = max(z_vals)
            mean_z = statistics.fmean(z_vals)
            z_score = 0.7 * max_z + 0.3 * mean_z
        else:
            max_z = 0.0
            z_score = 0.0
            if self.baseline_ready:
                reasons.append("no-valid-features")
            else:
                reasons.append("baseline-warming")

        stale_penalty = 0.0
        if self._stale_repeat > self.stale_limit:
            stale_penalty = min(6.0, (self._stale_repeat - self.stale_limit) / max(1.0, self.stale_limit / 2.0))
            reasons.append(f"stale:{self._stale_repeat}")

        score = z_score + missing_penalty + stale_penalty

        if high_z:
            reasons.append("z:" + ",".join(high_z))

        if miss_ratio >= 0.5 or self._stale_repeat > self.stale_limit * 2 or max_z >= self.anomaly_z:
            level = "anomaly"
        elif score >= self.warning_z:
            level = "warning"
        else:
            level = "normal"

        # 只用相对健康样本更新基线，降低异常污染
        has_core = any(parse_float(row.get(k)) is not None for k in CORE_FIELDS)
        if has_core and miss_ratio < 0.5 and level == "normal":
            self._baseline_buffer.append(feats)

        if (
            len(self._baseline_buffer) >= self.baseline_min_records
            and (not self.baseline_ready or self._processed % self.baseline_refresh_every == 0)
        ):
            self._fit_baseline()

        return {
            "level": level,
            "score": score,
            "reasons": reasons,
            "baseline_ready": self.baseline_ready,
            "baseline_count": self.baseline_count,
            "stale_repeat": self._stale_repeat,
        }

    def snapshot_state(self, max_items: int = 5000) -> dict[str, Any]:
        keep = max(100, max_items)
        return {
            "baseline_buffer": list(self._baseline_buffer)[-keep:],
            "processed": self._processed,
        }

    def load_state(self, state: Optional[dict[str, Any]]) -> None:
        if not state:
            return

        for feats in state.get("baseline_buffer", []):
            if not isinstance(feats, dict):
                continue
            row: dict[str, Optional[float]] = {}
            for key in FEATURE_FIELDS:
                value = feats.get(key)
                try:
                    row[key] = float(value) if value is not None else None
                except (TypeError, ValueError):
                    row[key] = None
            self._baseline_buffer.append(row)

        try:
            self._processed = max(self._processed, int(state.get("processed", 0)))
        except (TypeError, ValueError):
            pass

        if len(self._baseline_buffer) >= 20:
            self._fit_baseline()
