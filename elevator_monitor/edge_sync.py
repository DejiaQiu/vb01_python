from __future__ import annotations

import base64
import gzip
import hashlib
import json
import sqlite3
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _now_ts_ms() -> int:
    return int(time.time() * 1000)


def _safe_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value or ""))
    token = token.strip("_")
    return token or "unknown"


def build_event_id(elevator_id: str, ts_ms: int, fault_type: str, level: str) -> str:
    seed = f"{elevator_id}|{int(ts_ms)}|{fault_type}|{level}".encode("utf-8", errors="replace")
    digest = hashlib.sha1(seed).hexdigest()[:16]
    return f"{_safe_token(elevator_id)}-{int(ts_ms)}-{digest}"


def build_heartbeat_payload(
    *,
    elevator_id: str,
    device_id: str,
    site_id: str,
    site_name: str = "",
    health_payload: dict[str, Any],
) -> dict[str, Any]:
    updated_at_ms = int(health_payload.get("updated_at_ms") or _now_ts_ms())
    return {
        "device_id": str(device_id or "").strip() or _safe_token(elevator_id),
        "site_id": str(site_id or "").strip(),
        "site_name": str(site_name or "").strip(),
        "elevator_id": str(elevator_id or "").strip() or "elevator-unknown",
        "ts_ms": updated_at_ms,
        "health_payload": dict(health_payload or {}),
    }


def build_alert_payload(
    *,
    elevator_id: str,
    device_id: str,
    site_id: str,
    site_name: str = "",
    alert_payload: dict[str, Any],
    health_payload: dict[str, Any],
) -> dict[str, Any]:
    alert = dict(alert_payload or {})
    ts_ms = int(float(alert.get("ts_ms", _now_ts_ms()) or _now_ts_ms()))
    level = str(alert.get("level", "normal"))
    fault_type = str(alert.get("fault_type", "unknown"))
    event_id = build_event_id(elevator_id=elevator_id, ts_ms=ts_ms, fault_type=fault_type, level=level)
    return {
        "event_id": event_id,
        "device_id": str(device_id or "").strip() or _safe_token(elevator_id),
        "site_id": str(site_id or "").strip(),
        "site_name": str(site_name or "").strip(),
        "elevator_id": str(elevator_id or "").strip() or "elevator-unknown",
        "ts_ms": ts_ms,
        "level": level,
        "fault_type": fault_type,
        "fault_confidence": float(alert.get("fault_confidence", 0.0) or 0.0),
        "risk_score": float(alert.get("risk_score", 0.0) or 0.0),
        "risk_level_now": str(alert.get("risk_level_now", "normal")),
        "risk_24h": float(alert.get("risk_24h", 0.0) or 0.0),
        "risk_level_24h": str(alert.get("risk_level_24h", "normal")),
        "predictive_only": int(float(alert.get("predictive_only", 0) or 0)),
        "alert_context_csv": str(alert.get("alert_context_csv", "")).strip(),
        "alert_payload": alert,
        "health_payload": dict(health_payload or {}),
    }


def build_context_payload(
    *,
    event_id: str,
    elevator_id: str,
    device_id: str,
    site_id: str,
    site_name: str = "",
    ts_ms: int,
    csv_path: str,
    max_raw_bytes: int = 2_000_000,
) -> dict[str, Any]:
    path = Path(csv_path).expanduser().resolve()
    raw = path.read_bytes()
    truncated = False
    if max_raw_bytes > 0 and len(raw) > max_raw_bytes:
        raw = raw[:max_raw_bytes]
        truncated = True
    compressed = gzip.compress(raw)
    encoded = base64.b64encode(compressed).decode("ascii")
    return {
        "event_id": str(event_id),
        "device_id": str(device_id or "").strip() or _safe_token(elevator_id),
        "site_id": str(site_id or "").strip(),
        "site_name": str(site_name or "").strip(),
        "elevator_id": str(elevator_id or "").strip() or "elevator-unknown",
        "ts_ms": int(ts_ms),
        "file_name": path.name,
        "content_type": "text/csv",
        "content_encoding": "base64",
        "compression": "gzip",
        "raw_size": len(raw),
        "compressed_size": len(compressed),
        "truncated": bool(truncated),
        "content_b64": encoded,
    }


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    status_code: int
    error: str


class CloudIngestClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str = "",
        timeout_s: float = 5.0,
        verify_ssl: bool = True,
    ):
        normalized = str(base_url or "").strip().rstrip("/")
        if not normalized:
            raise ValueError("base_url is required")
        self.base_url = normalized
        self.api_token = str(api_token or "").strip()
        self.timeout_s = max(1.0, float(timeout_s))
        self.verify_ssl = bool(verify_ssl)

    def dispatch(self, *, endpoint: str, payload: dict[str, Any], delivery_id: str = "") -> DispatchResult:
        request = urllib.request.Request(
            f"{self.base_url}/{str(endpoint).lstrip('/')}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        if self.api_token:
            request.add_header("Authorization", f"Bearer {self.api_token}")
        if delivery_id:
            request.add_header("X-Idempotency-Key", delivery_id)
        ssl_context = None
        if not self.verify_ssl:
            ssl_context = ssl._create_unverified_context()  # noqa: S323
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s, context=ssl_context) as response:
                response.read()
                return DispatchResult(ok=True, status_code=int(getattr(response, "status", 200)), error="")
        except urllib.error.HTTPError as exc:
            return DispatchResult(ok=False, status_code=int(exc.code), error=f"http_error:{exc.code}")
        except Exception as exc:
            return DispatchResult(ok=False, status_code=0, error=f"{type(exc).__name__}:{exc}")


@dataclass(frozen=True)
class QueueItem:
    row_id: int
    endpoint: str
    body: dict[str, Any]
    attempts: int
    delivery_id: str


class EdgeSyncQueue:
    def __init__(self, db_path: str):
        self.path = Path(db_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    delivery_id TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_ms INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_queue_delivery ON sync_queue(delivery_id)")
            connection.commit()

    def enqueue(self, *, delivery_id: str, endpoint: str, body: dict[str, Any]) -> bool:
        now_ms = _now_ts_ms()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO sync_queue(endpoint, body_json, delivery_id, attempts, next_attempt_ms, last_error, created_at_ms)
                VALUES (?, ?, ?, 0, 0, '', ?)
                """,
                (
                    str(endpoint),
                    json.dumps(body, ensure_ascii=False),
                    str(delivery_id),
                    now_ms,
                ),
            )
            connection.commit()
            return int(cursor.rowcount or 0) > 0

    def pending(self, limit: int = 10) -> list[QueueItem]:
        now_ms = _now_ts_ms()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, endpoint, body_json, attempts, delivery_id
                FROM sync_queue
                WHERE next_attempt_ms <= ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (now_ms, max(1, int(limit))),
            ).fetchall()
        items: list[QueueItem] = []
        for row in rows:
            try:
                body = json.loads(str(row["body_json"]))
            except Exception:
                body = {}
            if isinstance(body, dict):
                items.append(
                    QueueItem(
                        row_id=int(row["id"]),
                        endpoint=str(row["endpoint"]),
                        body=body,
                        attempts=int(row["attempts"]),
                        delivery_id=str(row["delivery_id"]),
                    )
                )
        return items

    def mark_success(self, row_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM sync_queue WHERE id = ?", (int(row_id),))
            connection.commit()

    def mark_retry(self, row_id: int, error: str, attempts: int) -> None:
        backoff_ms = min(60_000, 1_000 * max(1, 2 ** min(6, int(attempts))))
        next_attempt_ms = _now_ts_ms() + backoff_ms
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sync_queue
                SET attempts = ?, next_attempt_ms = ?, last_error = ?
                WHERE id = ?
                """,
                (int(attempts), next_attempt_ms, str(error)[:500], int(row_id)),
            )
            connection.commit()

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS cnt FROM sync_queue").fetchone()
        return int(row["cnt"] if row else 0)

    def drain(self, *, client: CloudIngestClient | None, limit: int = 10) -> dict[str, Any]:
        summary: dict[str, Any] = {"sent": 0, "failed": 0, "last_error": ""}
        if client is None:
            return summary
        for item in self.pending(limit=limit):
            result = client.dispatch(endpoint=item.endpoint, payload=item.body, delivery_id=item.delivery_id)
            if result.ok:
                self.mark_success(item.row_id)
                summary["sent"] += 1
            else:
                self.mark_retry(item.row_id, result.error, item.attempts + 1)
                summary["failed"] += 1
                summary["last_error"] = result.error
                if result.status_code >= 500 or result.status_code == 0:
                    break
        return summary

