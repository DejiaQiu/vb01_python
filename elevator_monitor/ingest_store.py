from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_file_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
    token = token.strip("_")
    return token or "unknown"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, ensure_ascii=False))
        fp.write("\n")


def default_ingest_store_dir() -> str:
    return os.environ.get("ELEVATOR_CLOUD_STORE_DIR") or os.environ.get("MONITOR_INGEST_STORE_DIR", "data/cloud_ingest")


class CloudIngestStore:
    def __init__(self, root_dir: str):
        self.root = Path(root_dir).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.alert_dir = self.root / "alerts"
        self.context_dir = self.root / "contexts"
        self.elevator_dir = self.root / "elevators"
        self.device_dir = self.root / "devices"
        self.alert_dir.mkdir(parents=True, exist_ok=True)
        self.context_dir.mkdir(parents=True, exist_ok=True)
        self.elevator_dir.mkdir(parents=True, exist_ok=True)
        self.device_dir.mkdir(parents=True, exist_ok=True)

    def _elevator_root(self, elevator_id: str) -> Path:
        return self.elevator_dir / _safe_file_token(elevator_id)

    def _alert_path(self, event_id: str) -> Path:
        return self.alert_dir / f"{_safe_file_token(event_id)}.json"

    def _latest_status_path(self, elevator_id: str) -> Path:
        return self._elevator_root(elevator_id) / "latest_status.json"

    def _alerts_index_path(self, elevator_id: str) -> Path:
        return self._elevator_root(elevator_id) / "alerts.jsonl"

    @staticmethod
    def _risk_payload(alert_payload: dict[str, Any], health_payload: dict[str, Any]) -> dict[str, Any]:
        alert = alert_payload if isinstance(alert_payload, dict) else {}
        health = health_payload if isinstance(health_payload, dict) else {}
        return {
            "risk_score": float(alert.get("risk_score", health.get("last_risk_score", 0.0)) or 0.0),
            "risk_level_now": str(alert.get("risk_level_now") or health.get("last_risk_level_now") or "normal"),
            "risk_24h": float(alert.get("risk_24h", health.get("last_risk_24h", 0.0)) or 0.0),
            "risk_level_24h": str(alert.get("risk_level_24h") or health.get("last_risk_level_24h") or "normal"),
            "degradation_slope": float(alert.get("degradation_slope", health.get("last_degradation_slope", 0.0)) or 0.0),
        }

    def _build_latest_status_from_heartbeat(
        self,
        existing: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        health_payload = dict(payload.get("health_payload", {})) if isinstance(payload.get("health_payload"), dict) else {}
        alert = dict(existing.get("latest_alert", {})) if isinstance(existing.get("latest_alert"), dict) else {}
        context = dict(existing.get("context", {})) if isinstance(existing.get("context"), dict) else {}
        risk = self._risk_payload(alert, health_payload)
        preferred_issue = {}
        fault_type = str(alert.get("fault_type", "") or health_payload.get("last_fault_type", "")).strip()
        if fault_type and fault_type not in {"unknown", "normal"}:
            fault_confidence = float(alert.get("fault_confidence", health_payload.get("last_fault_confidence", 0.0)) or 0.0)
            if 0.0 <= fault_confidence <= 1.0:
                fault_confidence *= 100.0
            preferred_issue = {
                "fault_type": fault_type,
                "score": round(fault_confidence, 2),
                "level": str(alert.get("level", "warning")),
                "triggered": str(alert.get("level", "")).strip().lower() in {"warning", "anomaly"},
            }
        status = "normal"
        if preferred_issue:
            status = "candidate_faults"
        elif risk["risk_level_24h"] in {"watch", "high", "critical"}:
            status = "watch_only"
        return {
            "workflow_type": "edge_latest_status_v1",
            "source": "edge_ingest",
            "generated_at_ms": _now_ms(),
            "received_at_ms": _now_ms(),
            "site_id": str(payload.get("site_id", "")),
            "site_name": str(payload.get("site_name", "")),
            "device_id": str(payload.get("device_id", "")),
            "elevator_id": str(payload.get("elevator_id", "")),
            "status": status,
            "preferred_issue": preferred_issue,
            "top_candidate": dict(preferred_issue),
            "watch_faults": [dict(preferred_issue)] if status == "watch_only" and preferred_issue else [],
            "risk": risk,
            "health_payload": health_payload,
            "health": health_payload,
            "latest_alert": alert,
            "last_event_id": str(existing.get("last_event_id", "")),
            "context": context,
        }

    def _build_latest_status_from_alert(
        self,
        existing: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        alert_payload = dict(payload.get("alert_payload", {})) if isinstance(payload.get("alert_payload"), dict) else {}
        health_payload = dict(payload.get("health_payload", {})) if isinstance(payload.get("health_payload"), dict) else {}
        fault_type = str(alert_payload.get("fault_type", "")).strip() or str(health_payload.get("last_fault_type", "unknown")).strip() or "unknown"
        fault_confidence = float(alert_payload.get("fault_confidence", health_payload.get("last_fault_confidence", 0.0)) or 0.0)
        if 0.0 <= fault_confidence <= 1.0:
            fault_confidence *= 100.0
        level = str(alert_payload.get("level", "warning")).strip().lower()
        predictive_only = int(float(alert_payload.get("predictive_only", 0) or 0)) > 0
        if fault_type not in {"unknown", "normal"} and level in {"warning", "anomaly"}:
            status = "candidate_faults"
            preferred_issue = {
                "fault_type": fault_type,
                "score": round(fault_confidence, 2),
                "level": level,
                "triggered": True,
            }
            watch_faults: list[dict[str, Any]] = []
        elif str(alert_payload.get("risk_level_24h", "normal")).strip().lower() in {"watch", "high", "critical"}:
            status = "watch_only"
            preferred_issue = {
                "fault_type": fault_type,
                "score": round(fault_confidence, 2),
                "level": level or "warning",
                "triggered": not predictive_only,
            }
            watch_faults = [dict(preferred_issue)]
        else:
            status = "normal"
            preferred_issue = {}
            watch_faults = []

        result = {
            "workflow_type": "edge_latest_status_v1",
            "source": "edge_ingest",
            "generated_at_ms": _now_ms(),
            "received_at_ms": _now_ms(),
            "site_id": str(payload.get("site_id", existing.get("site_id", ""))),
            "site_name": str(payload.get("site_name", existing.get("site_name", ""))),
            "device_id": str(payload.get("device_id", existing.get("device_id", ""))),
            "elevator_id": str(payload.get("elevator_id", existing.get("elevator_id", ""))),
            "status": status,
            "preferred_issue": preferred_issue,
            "top_candidate": dict(preferred_issue),
            "watch_faults": watch_faults,
            "risk": self._risk_payload(alert_payload, health_payload),
            "health_payload": health_payload or dict(existing.get("health_payload", {})),
            "health": health_payload or dict(existing.get("health", {})),
            "latest_alert": alert_payload,
            "last_event_id": str(payload.get("event_id", "")),
        }
        if payload.get("event_id"):
            result["event_id"] = str(payload.get("event_id"))
        return result

    def record_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        elevator_id = str(payload.get("elevator_id", "")).strip()
        if not elevator_id:
            raise ValueError("elevator_id is required")
        latest_path = self._latest_status_path(elevator_id)
        existing = _read_json(latest_path)
        latest = self._build_latest_status_from_heartbeat(existing, payload)
        _write_json(latest_path, latest)
        device_payload = {
            "received_at_ms": _now_ms(),
            **payload,
        }
        device_id = str(payload.get("device_id", "")).strip()
        if device_id:
            _append_jsonl(self.device_dir / f"{_safe_file_token(device_id)}.jsonl", device_payload)
        return latest

    def record_alert(self, payload: dict[str, Any]) -> dict[str, Any]:
        elevator_id = str(payload.get("elevator_id", "")).strip()
        event_id = str(payload.get("event_id", "")).strip()
        if not elevator_id or not event_id:
            raise ValueError("elevator_id and event_id are required")

        event = {
            "event_id": event_id,
            "received_at_ms": _now_ms(),
            "device_id": str(payload.get("device_id", "")),
            "site_id": str(payload.get("site_id", "")),
            "site_name": str(payload.get("site_name", "")),
            "elevator_id": elevator_id,
            "ts_ms": int(payload.get("ts_ms", 0) or 0),
            "alert_payload": dict(payload.get("alert_payload", {})) if isinstance(payload.get("alert_payload"), dict) else {},
            "health_payload": dict(payload.get("health_payload", {})) if isinstance(payload.get("health_payload"), dict) else {},
            "context": {},
        }
        alert_path = self._alert_path(event_id)
        if alert_path.exists():
            existing = _read_json(alert_path)
            if existing:
                event["context"] = dict(existing.get("context", {})) if isinstance(existing.get("context"), dict) else {}
        _write_json(alert_path, event)
        _append_jsonl(self._alerts_index_path(elevator_id), {
            "event_id": event_id,
            "ts_ms": event["ts_ms"],
            "received_at_ms": event["received_at_ms"],
            "alert_payload": event["alert_payload"],
        })

        latest_path = self._latest_status_path(elevator_id)
        latest = self._build_latest_status_from_alert(_read_json(latest_path), payload)
        _write_json(latest_path, latest)
        return event

    def record_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id", "")).strip()
        elevator_id = str(payload.get("elevator_id", "")).strip()
        if not event_id or not elevator_id:
            raise ValueError("event_id and elevator_id are required")

        raw_b64 = str(payload.get("content_b64", "")).strip()
        content = base64.b64decode(raw_b64.encode("ascii")) if raw_b64 else b""
        content_type = str(payload.get("content_type", "application/octet-stream")).strip() or "application/octet-stream"
        file_name = str(payload.get("file_name", "")).strip()
        if not file_name:
            suffix = ".csv.gz" if "csv" in content_type else ".bin"
            file_name = f"{_safe_file_token(event_id)}{suffix}"
        out_path = self.context_dir / file_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(content)

        metadata = {
            "event_id": event_id,
            "elevator_id": elevator_id,
            "device_id": str(payload.get("device_id", "")),
            "site_id": str(payload.get("site_id", "")),
            "site_name": str(payload.get("site_name", "")),
            "received_at_ms": _now_ms(),
            "ts_ms": int(payload.get("ts_ms", 0) or 0),
            "file_name": out_path.name,
            "stored_path": str(out_path),
            "local_path": str(out_path),
            "content_type": content_type,
            "compression": str(payload.get("compression", "")).strip(),
            "size_bytes": out_path.stat().st_size,
        }
        _write_json(self.context_dir / f"{_safe_file_token(event_id)}.json", metadata)

        alert_path = self._alert_path(event_id)
        alert_payload = _read_json(alert_path)
        if alert_payload:
            alert_payload["context"] = metadata
            _write_json(alert_path, alert_payload)

        latest_path = self._latest_status_path(elevator_id)
        latest = _read_json(latest_path)
        if latest.get("last_event_id") == event_id:
            latest["context"] = metadata
            _write_json(latest_path, latest)

        return metadata

    def get_latest_status(self, elevator_id: str) -> dict[str, Any]:
        return _read_json(self._latest_status_path(elevator_id))

    def list_alerts(self, elevator_id: str, limit: int = 20) -> list[dict[str, Any]]:
        index_path = self._alerts_index_path(elevator_id)
        if not index_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        rows.sort(key=lambda item: int(item.get("ts_ms", 0) or 0), reverse=True)
        return rows[: max(1, int(limit))]

    def get_alert(self, event_id: str) -> dict[str, Any]:
        return _read_json(self._alert_path(event_id))


def get_ingest_store(root_dir: str | None = None) -> CloudIngestStore:
    return CloudIngestStore(root_dir or default_ingest_store_dir())
