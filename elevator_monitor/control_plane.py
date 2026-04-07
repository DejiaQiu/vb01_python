from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


LIFECYCLE_STAGES = {
    "commissioning",
    "baseline_building",
    "monitoring",
}
CONTROL_ACTIONS = {
    "start_baseline",
    "freeze_baseline",
    "resume_baseline",
    "reset_baseline",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_file_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value or ""))
    token = token.strip("_")
    return token or "unknown"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and not value:
            merged[key] = {}
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def default_control_dir() -> str:
    return os.environ.get("MONITOR_CONTROL_DIR", "data/control")


def _default_state(elevator_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "elevator_id": str(elevator_id or "").strip() or "elevator-unknown",
        "lifecycle_stage": "commissioning",
        "commissioning_confirmed": False,
        "baseline_learning_enabled": False,
        "baseline_frozen": False,
        "alerts_enabled": False,
        "baseline_ready": False,
        "baseline_count": 0,
        "pending_command": {},
        "last_applied_command": {},
        "last_command_seq": 0,
        "monitor": {},
        "updated_at_ms": _now_ms(),
    }


class ControlPlaneStore:
    def __init__(self, root_dir: str | None = None):
        self.root = Path(root_dir or default_control_dir()).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _elevator_root(self, elevator_id: str) -> Path:
        return self.root / _safe_file_token(elevator_id)

    def _state_path(self, elevator_id: str) -> Path:
        return self._elevator_root(elevator_id) / "state.json"

    def read_state(self, elevator_id: str) -> dict[str, Any]:
        payload = _read_json(self._state_path(elevator_id))
        if not payload:
            return {}
        payload["elevator_id"] = str(payload.get("elevator_id") or elevator_id or "elevator-unknown")
        return payload

    def ensure_state(self, elevator_id: str) -> dict[str, Any]:
        payload = _default_state(elevator_id)
        existing = self.read_state(elevator_id)
        if existing:
            payload = _deep_merge(payload, existing)
        payload["elevator_id"] = str(elevator_id or payload.get("elevator_id") or "elevator-unknown")
        if str(payload.get("lifecycle_stage", "")).strip() not in LIFECYCLE_STAGES:
            payload["lifecycle_stage"] = "commissioning"
        if not isinstance(payload.get("monitor"), dict):
            payload["monitor"] = {}
        if not isinstance(payload.get("pending_command"), dict):
            payload["pending_command"] = {}
        if not isinstance(payload.get("last_applied_command"), dict):
            payload["last_applied_command"] = {}
        return payload

    def save_state(self, elevator_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        state = self.ensure_state(elevator_id)
        state = _deep_merge(state, payload if isinstance(payload, dict) else {})
        state["updated_at_ms"] = _now_ms()
        _write_json(self._state_path(elevator_id), state)
        return state

    def sync_runtime_state(self, elevator_id: str, runtime_payload: dict[str, Any]) -> dict[str, Any]:
        monitor = dict(runtime_payload or {})
        patch = {
            "monitor": monitor,
            "baseline_ready": bool(monitor.get("baseline_ready", False)),
            "baseline_count": int(monitor.get("baseline_count", 0) or 0),
        }
        return self.save_state(elevator_id, patch)

    def issue_command(self, elevator_id: str, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"unsupported control action: {action}")

        state = self.ensure_state(elevator_id)
        pending = state.get("pending_command", {}) if isinstance(state.get("pending_command"), dict) else {}
        last_applied = state.get("last_applied_command", {}) if isinstance(state.get("last_applied_command"), dict) else {}
        seq = max(
            int(state.get("last_command_seq", 0) or 0),
            int(pending.get("seq", 0) or 0),
            int(last_applied.get("seq", 0) or 0),
        ) + 1
        command = {
            "seq": seq,
            "action": action,
            "payload": dict(payload or {}),
            "requested_at_ms": _now_ms(),
        }
        return self.save_state(
            elevator_id,
            {
                "pending_command": command,
                "last_command_seq": seq,
            },
        )

    def acknowledge_command(
        self,
        elevator_id: str,
        command: dict[str, Any],
        *,
        message: str = "",
        state_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        seq = int(command.get("seq", 0) or 0)
        patch = dict(state_patch or {})
        patch["last_applied_command"] = {
            "seq": seq,
            "action": str(command.get("action", "")),
            "payload": dict(command.get("payload", {})) if isinstance(command.get("payload"), dict) else {},
            "requested_at_ms": int(command.get("requested_at_ms", 0) or 0),
            "applied_at_ms": _now_ms(),
            "message": str(message or ""),
        }

        current = self.ensure_state(elevator_id)
        pending = current.get("pending_command", {}) if isinstance(current.get("pending_command"), dict) else {}
        if int(pending.get("seq", 0) or 0) == seq:
            patch["pending_command"] = {}
        return self.save_state(elevator_id, patch)

    def list_elevator_ids(self) -> list[str]:
        ids: list[str] = []
        if not self.root.exists():
            return ids
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            state = _read_json(child / "state.json")
            elevator_id = str(state.get("elevator_id", "")).strip() if state else ""
            if not elevator_id:
                elevator_id = child.name
            if elevator_id and elevator_id not in ids:
                ids.append(elevator_id)
        return ids


def discover_elevator_ids(control_root: str | None = None) -> list[str]:
    discovered: list[str] = []

    def _push(value: str) -> None:
        text = str(value or "").strip()
        if text and text not in discovered:
            discovered.append(text)

    store = ControlPlaneStore(control_root)
    for elevator_id in store.list_elevator_ids():
        _push(elevator_id)

    cwd = Path.cwd()
    for path in sorted((cwd / "data" / "profiles").glob("*.json")):
        _push(path.stem)

    for path in sorted((cwd / "data" / "diagnosis").glob("*/latest_status.json")):
        _push(path.parent.name)

    for path in sorted((cwd / "data" / "cloud_ingest" / "elevators").glob("*")):
        if path.is_dir():
            _push(path.name)

    health_path = cwd / "data" / "monitor_health.json"
    health = _read_json(health_path)
    _push(str(health.get("elevator_id", "")))

    return discovered


def get_control_store(root_dir: str | None = None) -> ControlPlaneStore:
    return ControlPlaneStore(root_dir=root_dir)
