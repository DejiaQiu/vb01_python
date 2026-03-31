from __future__ import annotations

from collections import deque
from typing import Any, Optional

from report.fault_algorithms._base import build_clean_feature_baseline, build_feature_pack
from report.fault_algorithms.run_all import ALL_BASELINE_KEYS, MIN_EFFECTIVE_SAMPLES, run_all_rows


def _empty_result() -> dict[str, Any]:
    return {
        "input": "edge_runtime",
        "summary": {
            "n_raw": 0,
            "n_effective": 0,
            "fs_hz": 0.0,
            "used_new_only": False,
            "new_ratio": 0.0,
            "sampling_ok": False,
            "sampling_ok_40hz": False,
            "sampling_condition": "warmup",
            "axis_mapping_mode": "default",
            "axis_mapping_signature": "",
        },
        "baseline": {
            "mode": "disabled",
            "count": 0,
            "stats": 0,
            "mapping_match": None,
        },
        "screening": {
            "status": "normal",
            "quality_ok": False,
            "high_confidence_min_score": 60.0,
            "watch_min_score": 45.0,
            "candidate_count": 0,
            "watch_count": 0,
            "sampling_condition": "warmup",
        },
        "system_abnormality": {
            "status": "normal",
            "score": 0.0,
            "shared_abnormal_score": 0.0,
            "baseline_mode": "disabled",
            "baseline_weight": 0.0,
            "baseline_features": 0,
            "baseline_match": None,
            "run_state_score": 0.0,
            "gate_mode": "warmup",
            "shared_hits": 0,
            "shared_strong_hits": 0,
            "shared_feature_total": 0,
            "top_deviations": [],
            "sampling_ok": False,
            "sampling_ok_40hz": False,
            "sampling_condition": "warmup",
        },
        "detector_results": [],
        "top_fault": {},
        "top_candidate": {},
        "candidate_faults": [],
        "watch_faults": [],
        "primary_issue": {},
        "auxiliary_results": [],
        "results": [],
    }


class OnlineEdgeDiagnosis:
    def __init__(
        self,
        *,
        window_s: float = 30.0,
        step_s: float = 2.0,
        baseline_max_windows: int = 240,
        baseline_min_windows: int = 8,
        max_rows: int = 6000,
    ):
        self.window_ms = int(max(8.0, float(window_s)) * 1000)
        self.step_ms = int(max(0.5, float(step_s)) * 1000)
        self.baseline_max_windows = max(8, int(baseline_max_windows))
        self.baseline_min_windows = max(3, int(baseline_min_windows))
        self._rows: deque[tuple[int, dict[str, Any]]] = deque(maxlen=max(256, int(max_rows)))
        self._healthy_feature_rows: deque[dict[str, Any]] = deque(maxlen=self.baseline_max_windows)
        self._last_eval_ts_ms: Optional[int] = None
        self._last_result: dict[str, Any] = _empty_result()

    @property
    def baseline_ready(self) -> bool:
        return len(self._healthy_feature_rows) >= self.baseline_min_windows

    @property
    def baseline_count(self) -> int:
        return len(self._healthy_feature_rows)

    @property
    def last_result(self) -> dict[str, Any]:
        return dict(self._last_result)

    def update(self, ts_ms: int, row: dict[str, Any]) -> dict[str, Any]:
        self._rows.append((int(ts_ms), dict(row)))
        self._trim(ts_ms)

        if self._last_eval_ts_ms is not None and int(ts_ms) - self._last_eval_ts_ms < self.step_ms:
            return dict(self._last_result)

        rows = [record for _, record in self._rows]
        if not rows:
            return dict(self._last_result)

        features = build_feature_pack(rows)
        baseline_payload, baseline_summary = self._build_baseline()
        result = run_all_rows(
            rows,
            source="edge_runtime",
            baseline=baseline_payload,
            baseline_summary=baseline_summary,
        )
        self._remember_healthy_window(features, result)
        self._last_eval_ts_ms = int(ts_ms)
        self._last_result = dict(result)
        return dict(self._last_result)

    def snapshot_state(self, max_items: int = 300) -> dict[str, Any]:
        keep = max(16, int(max_items))
        return {
            "healthy_feature_rows": list(self._healthy_feature_rows)[-keep:],
            "last_eval_ts_ms": self._last_eval_ts_ms,
            "last_result": self._last_result,
        }

    def load_state(self, state: Optional[dict[str, Any]]) -> None:
        if not state:
            return

        for item in state.get("healthy_feature_rows", []):
            if isinstance(item, dict):
                self._healthy_feature_rows.append(dict(item))

        last_eval_ts_ms = state.get("last_eval_ts_ms")
        if isinstance(last_eval_ts_ms, (int, float)):
            self._last_eval_ts_ms = int(last_eval_ts_ms)

        last_result = state.get("last_result")
        if isinstance(last_result, dict):
            self._last_result = dict(last_result)

    def _trim(self, now_ms: int) -> None:
        cutoff = int(now_ms) - self.window_ms
        while self._rows and self._rows[0][0] < cutoff:
            self._rows.popleft()

    def _build_baseline(self) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if len(self._healthy_feature_rows) < 3:
            return None, {"mode": "disabled", "count": 0, "stats": 0}

        feature_rows = list(self._healthy_feature_rows)
        payload = build_clean_feature_baseline(
            feature_rows,
            ALL_BASELINE_KEYS,
            min_samples=MIN_EFFECTIVE_SAMPLES,
        )
        payload["source"] = "edge_runtime"
        stats = payload.get("stats", {})
        return payload, {
            "mode": "rolling_windows",
            "count": int(payload.get("count", 0) or 0),
            "stats": len(stats) if isinstance(stats, dict) else 0,
        }

    def _remember_healthy_window(self, features: dict[str, Any], result: dict[str, Any]) -> None:
        screening = result.get("screening", {}) if isinstance(result.get("screening"), dict) else {}
        if not bool(screening.get("quality_ok", False)):
            return
        if str(screening.get("status", "normal")) != "normal":
            return
        if int(features.get("n", 0) or 0) < MIN_EFFECTIVE_SAMPLES:
            return
        self._healthy_feature_rows.append(dict(features))


def diagnosis_to_anomaly_result(result: dict[str, Any], *, baseline_ready: bool, baseline_count: int) -> dict[str, Any]:
    screening = result.get("screening", {}) if isinstance(result.get("screening"), dict) else {}
    summary = result.get("summary", {}) if isinstance(result.get("summary"), dict) else {}
    system = result.get("system_abnormality", {}) if isinstance(result.get("system_abnormality"), dict) else {}

    screening_status = str(screening.get("status", "normal"))
    if screening_status == "candidate_faults":
        level = "anomaly"
    elif screening_status == "watch_only":
        level = "warning"
    else:
        level = "normal"

    reasons = [
        f"screening:{screening_status}",
        f"gate:{str(system.get('gate_mode', 'unknown'))}",
        f"baseline:{str(system.get('baseline_mode', 'disabled'))}",
    ]
    if not bool(screening.get("quality_ok", False)):
        reasons.append(f"sampling:{str(summary.get('sampling_condition', 'unknown'))}")
    for item in system.get("top_deviations", [])[:3]:
        if isinstance(item, dict):
            key = str(item.get("key", "unknown"))
            score = float(item.get("score", 0.0) or 0.0)
            reasons.append(f"{key}:{score:.1f}")

    return {
        "level": level,
        "score": float(system.get("score", 0.0) or 0.0),
        "reasons": reasons,
        "baseline_ready": bool(baseline_ready),
        "baseline_count": int(baseline_count),
        "stale_repeat": 0,
    }


def diagnosis_to_fault_result(result: dict[str, Any]) -> dict[str, Any]:
    screening = result.get("screening", {}) if isinstance(result.get("screening"), dict) else {}
    summary = result.get("summary", {}) if isinstance(result.get("summary"), dict) else {}
    system = result.get("system_abnormality", {}) if isinstance(result.get("system_abnormality"), dict) else {}
    primary = result.get("primary_issue", {}) if isinstance(result.get("primary_issue"), dict) else {}
    fault_type = str(primary.get("fault_type", "unknown")).strip() or "unknown"
    primary_score = float(primary.get("score", 0.0) or 0.0)
    reasons = primary.get("reasons", []) if isinstance(primary.get("reasons"), list) else []

    issues: list[str] = []
    for item in result.get("candidate_faults", []):
        if isinstance(item, dict) and str(item.get("fault_type", "")).strip():
            issues.append(f"{item['fault_type']}:{float(item.get('score', 0.0)):.1f}")
    for item in result.get("watch_faults", []):
        if isinstance(item, dict) and str(item.get("fault_type", "")).strip():
            issues.append(f"{item['fault_type']}:{float(item.get('score', 0.0)):.1f}")
    if not issues and fault_type not in {"", "unknown", "normal"}:
        issues.append(f"{fault_type}:{primary_score:.1f}")

    return {
        "fault_type": fault_type,
        "fault_confidence": primary_score / 100.0 if fault_type not in {"", "unknown", "normal"} else 0.0,
        "fault_source": "edge_rule_engine_v2",
        "fault_candidates": "|".join(issues),
        "fault_reasons": "|".join(str(item) for item in reasons) if reasons else f"screening={str(screening.get('status', 'normal'))}",
        "fault_screening_status": str(screening.get("status", "normal")),
        "fault_primary_score": primary_score,
        "fault_detector_family": str(primary.get("detector_family", "")),
        "fault_attribution_margin": float(primary.get("attribution_margin", 0.0) or 0.0),
        "system_score": float(system.get("score", 0.0) or 0.0),
        "system_gate_mode": str(system.get("gate_mode", "")),
        "baseline_mode": str(system.get("baseline_mode", "")),
        "baseline_match": system.get("baseline_match"),
        "sampling_condition": str(summary.get("sampling_condition", "unknown")),
        "axis_mapping_signature": str(summary.get("axis_mapping_signature", "")),
    }
