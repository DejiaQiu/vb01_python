from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DifyDispatchResult:
    dispatched: bool
    status: str
    workflow_run_id: str
    task_id: str
    http_status: int
    latency_ms: int
    error: str
    raw: dict[str, Any]

    def to_alert_fields(self) -> dict[str, Any]:
        return {
            "dify_dispatched": int(self.dispatched),
            "dify_status": self.status,
            "dify_workflow_run_id": self.workflow_run_id,
            "dify_task_id": self.task_id,
            "dify_error": self.error,
        }


class DifyWorkflowClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_s: float = 8.0,
        verify_ssl: bool = True,
    ):
        normalized = str(base_url or "").strip().rstrip("/")
        if not normalized:
            raise ValueError("base_url is required")
        if not str(api_key or "").strip():
            raise ValueError("api_key is required")

        self.base_url = normalized
        self.api_key = str(api_key).strip()
        self.timeout_s = max(1.0, float(timeout_s))
        self.verify_ssl = bool(verify_ssl)
        self.endpoint = f"{self.base_url}/workflows/run"

    @staticmethod
    def _extract_run_id(payload: dict[str, Any]) -> str:
        for key in ("workflow_run_id", "id", "run_id"):
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()

        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("workflow_run_id", "id", "run_id"):
                value = data.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
        return ""

    @staticmethod
    def _extract_task_id(payload: dict[str, Any]) -> str:
        for key in ("task_id",):
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()

        data = payload.get("data")
        if isinstance(data, dict):
            value = data.get("task_id")
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    def run_workflow(
        self,
        *,
        inputs: dict[str, Any],
        user: str,
        response_mode: str = "blocking",
    ) -> DifyDispatchResult:
        mode = str(response_mode or "blocking").strip().lower()
        if mode not in {"blocking", "streaming"}:
            mode = "blocking"

        body = {
            "inputs": dict(inputs or {}),
            "response_mode": mode,
            "user": str(user or "elevator-monitor"),
        }

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        ssl_context = None
        if not self.verify_ssl:
            ssl_context = ssl._create_unverified_context()  # noqa: S323

        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s, context=ssl_context) as resp:
                raw_text = resp.read().decode("utf-8", errors="replace")
                payload = json.loads(raw_text) if raw_text.strip() else {}
                latency_ms = int((time.monotonic() - started) * 1000)
                return DifyDispatchResult(
                    dispatched=True,
                    status="success",
                    workflow_run_id=self._extract_run_id(payload),
                    task_id=self._extract_task_id(payload),
                    http_status=int(getattr(resp, "status", 200)),
                    latency_ms=latency_ms,
                    error="",
                    raw=payload if isinstance(payload, dict) else {},
                )
        except urllib.error.HTTPError as ex:
            latency_ms = int((time.monotonic() - started) * 1000)
            raw_text = ""
            try:
                raw_text = ex.read().decode("utf-8", errors="replace")
            except Exception:
                raw_text = ""
            payload: dict[str, Any] = {}
            if raw_text.strip():
                try:
                    obj = json.loads(raw_text)
                    if isinstance(obj, dict):
                        payload = obj
                except json.JSONDecodeError:
                    payload = {"raw": raw_text[:1000]}
            return DifyDispatchResult(
                dispatched=False,
                status="http_error",
                workflow_run_id=self._extract_run_id(payload),
                task_id=self._extract_task_id(payload),
                http_status=int(ex.code),
                latency_ms=latency_ms,
                error=f"http_error:{ex.code}",
                raw=payload,
            )
        except Exception as ex:
            latency_ms = int((time.monotonic() - started) * 1000)
            return DifyDispatchResult(
                dispatched=False,
                status="request_failed",
                workflow_run_id="",
                task_id="",
                http_status=0,
                latency_ms=latency_ms,
                error=f"{type(ex).__name__}:{ex}",
                raw={},
            )
