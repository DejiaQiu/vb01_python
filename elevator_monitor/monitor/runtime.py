from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import signal
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event
from typing import Any, Optional

from ..common import CORE_FIELDS, REG_MAP
from ..common import dumps_jsonl
from ..control_plane import ControlPlaneStore
from ..data_recorder import DataRecorder, format_ts_ms, now_ts_ms
from ..device_model import DeviceModel
from ..edge_sync import (
    CloudIngestClient,
    EdgeSyncQueue,
    build_alert_payload,
    build_context_payload,
    build_heartbeat_payload,
)
from ..risk_predictor import OnlineRiskPredictor
from .alerting import ALERT_FIELDS, build_alert_record, should_emit_predictive_alert
from .args import build_arg_parser
from .constants import DATA_FIELDS
from .edge_diagnosis import OnlineEdgeDiagnosis, diagnosis_to_anomaly_result, diagnosis_to_fault_result


class RealtimeMonitor:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.args.diagnosis_window_s = max(8.0, float(self.args.diagnosis_window_s))
        self.args.diagnosis_step_s = max(0.5, float(self.args.diagnosis_step_s))
        self.args.diagnosis_baseline_max_windows = max(8, int(self.args.diagnosis_baseline_max_windows))
        self.args.diagnosis_baseline_min_windows = max(3, int(self.args.diagnosis_baseline_min_windows))
        self.args.diagnosis_max_rows = max(256, int(self.args.diagnosis_max_rows))
        self.args.alert_context_pre_seconds = max(1.0, float(self.args.alert_context_pre_seconds))
        self.args.alert_context_max_rows = max(100, int(self.args.alert_context_max_rows))
        self.args.reg_count = max(1, int(self.args.reg_count))
        self.args.reg_addr = int(self.args.reg_addr)
        self.args.edge_sync_timeout_s = max(1.0, float(self.args.edge_sync_timeout_s))
        self.args.edge_sync_heartbeat_every_s = max(1.0, float(self.args.edge_sync_heartbeat_every_s))
        self.args.edge_sync_drain_every_s = max(0.5, float(self.args.edge_sync_drain_every_s))
        self.args.edge_sync_drain_batch_size = max(1, int(self.args.edge_sync_drain_batch_size))
        self.args.edge_sync_max_context_bytes = max(16_384, int(self.args.edge_sync_max_context_bytes))
        self.stop_event = Event()

        self.logger = self._build_logger(args)

        self.device: Optional[DeviceModel] = None
        self.diagnosis_engine = self._build_diagnosis_engine()
        self.risk_predictor = self._build_risk_predictor()
        self.alert_context_rows: deque[dict[str, Any]] = deque(maxlen=self.args.alert_context_max_rows)
        self.edge_sync_queue = self._build_edge_sync_queue()
        self.edge_sync_client = self._build_edge_sync_client()
        self.control_store = ControlPlaneStore()
        self.lifecycle_stage = "commissioning"
        self.commissioning_confirmed = False
        self.baseline_learning_enabled = False
        self.baseline_frozen = False
        self.alerts_enabled = False
        self._last_applied_command_seq = 0

        self.started_monotonic = time.monotonic()
        self.last_data_monotonic = 0.0
        self.last_data_ts_ms: Optional[int] = None
        self.last_written_data_ts: Optional[int] = None

        self.total_loops = 0
        self.records_written = 0
        self.skipped_total = 0
        self.alerts_emitted = 0
        self._last_alert_emit_ms: Optional[int] = None
        self._last_level = "normal"
        self.last_fault_type = "unknown"
        self.last_fault_confidence = 0.0
        self.last_fault_source = ""
        self.last_screening_status = "normal"
        self.last_system_score = 0.0
        self.last_risk_score = 0.0
        self.last_risk_level_now = "normal"
        self.last_risk_24h = 0.0
        self.last_risk_level_24h = "normal"
        self.last_degradation_slope = 0.0
        self.last_alert_context_path = ""
        self.last_edge_sync_status = ""
        self.last_edge_sync_error = ""
        self.edge_sync_dispatch_count = 0
        self._last_edge_sync_drain = 0.0
        self._last_edge_sync_heartbeat_ms: Optional[int] = None

        self._last_health_write = 0.0
        self.status = "starting"

        self.profile_path = self._resolve_profile_path(args.profile_path, args.elevator_id)
        self.profile_loaded = False
        self.profile_load_error = ""
        self.profile_save_count = 0
        self._last_profile_save_records = 0
        self._load_profile()
        self._init_control_state()

    def _build_diagnosis_engine(self) -> OnlineEdgeDiagnosis:
        return OnlineEdgeDiagnosis(
            window_s=self.args.diagnosis_window_s,
            step_s=self.args.diagnosis_step_s,
            baseline_max_windows=self.args.diagnosis_baseline_max_windows,
            baseline_min_windows=self.args.diagnosis_baseline_min_windows,
            max_rows=self.args.diagnosis_max_rows,
        )

    def _build_risk_predictor(self) -> OnlineRiskPredictor:
        return OnlineRiskPredictor(
            enabled=self.args.risk_enabled,
            stale_limit=self.args.stale_limit,
            baseline_size=self.args.risk_baseline_size,
            baseline_min_records=self.args.risk_baseline_min_records,
            trend_window_s=self.args.risk_trend_window_s,
            smooth_alpha=self.args.risk_smooth_alpha,
            anomaly_scale=self.args.risk_anomaly_scale,
            fault_weight=self.args.risk_fault_weight,
            vibration_weight=self.args.risk_vibration_weight,
            temperature_weight=self.args.risk_temperature_weight,
            model_weight=0.0,
        )

    def _log_runtime_config(self) -> None:
        self.logger.info(
            "runtime config elevator_id=%s port=%s baud=%s addr=%s sample_hz=%s detect_hz=%s reg_addr=%s reg_count=%s "
            "max_data_age_ms=%s reconnect_no_data_s=%s reconnect_backoff_s=%s output_data=%s output_alert=%s health_path=%s "
            "diagnosis_window_s=%s diagnosis_step_s=%s diagnosis_baseline_min_windows=%s diagnosis_baseline_max_windows=%s diagnosis_max_rows=%s "
            "alert_context_enabled=%s alert_context_dir=%s alert_context_pre_seconds=%s alert_context_max_rows=%s "
            "risk_enabled=%s risk_emit_on_normal=%s risk_emit_min_level=%s "
            "edge_sync_enabled=%s edge_sync_client_ready=%s edge_sync_base_url=%s edge_sync_queue_path=%s "
            "profile_path=%s profile_loaded=%s",
            self.args.elevator_id,
            self.args.port,
            self.args.baud,
            hex(self.args.addr),
            self.args.sample_hz,
            self.args.detect_hz,
            hex(self.args.reg_addr),
            self.args.reg_count,
            self.args.max_data_age_ms,
            self.args.reconnect_no_data_s,
            self.args.reconnect_backoff_s,
            self.args.output_data,
            self.args.output_alert,
            self.args.health_path,
            self.args.diagnosis_window_s,
            self.args.diagnosis_step_s,
            self.args.diagnosis_baseline_min_windows,
            self.args.diagnosis_baseline_max_windows,
            self.args.diagnosis_max_rows,
            self.args.alert_context_enabled,
            self.args.alert_context_dir,
            self.args.alert_context_pre_seconds,
            self.args.alert_context_max_rows,
            self.args.risk_enabled,
            self.args.risk_emit_on_normal,
            self.args.risk_emit_min_level,
            self.args.edge_sync_enabled,
            self.edge_sync_client is not None,
            self.args.edge_sync_base_url,
            self.args.edge_sync_queue_path,
            self.profile_path,
            self.profile_loaded,
        )

    @staticmethod
    def _build_logger(args: argparse.Namespace) -> logging.Logger:
        logger = logging.getLogger("elevator.monitor")
        logger.setLevel(getattr(logging, args.log_level.upper(), logging.INFO))
        logger.propagate = False
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        fh = RotatingFileHandler(
            str(log_path),
            maxBytes=args.log_max_bytes,
            backupCount=args.log_backups,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)
        return logger

    @staticmethod
    def _resolve_profile_path(template: str, elevator_id: str) -> str:
        if "{elevator_id}" in template:
            return template.replace("{elevator_id}", elevator_id)
        return template

    def _build_edge_sync_queue(self) -> Optional[EdgeSyncQueue]:
        if not self.args.edge_sync_enabled:
            return None
        try:
            return EdgeSyncQueue(self.args.edge_sync_queue_path)
        except Exception as ex:
            self.logger.warning("edge sync queue init failed err=%s", ex)
            return None

    def _build_edge_sync_client(self) -> Optional[CloudIngestClient]:
        if not self.args.edge_sync_enabled:
            return None

        base_url = str(self.args.edge_sync_base_url or "").strip()
        if not base_url:
            self.logger.warning("edge sync enabled but base_url missing; edge sync dispatch disabled")
            return None

        try:
            client = CloudIngestClient(
                base_url=base_url,
                api_token=self.args.edge_sync_api_token,
                timeout_s=self.args.edge_sync_timeout_s,
                verify_ssl=self.args.edge_sync_verify_ssl,
            )
            self.logger.info("edge sync client ready base_url=%s", client.base_url)
            return client
        except Exception as ex:
            self.logger.warning("edge sync client init failed err=%s", ex)
            return None

    def _apply_control_flags(
        self,
        *,
        stage: str,
        learning_enabled: bool,
        frozen: bool,
        alerts_enabled: bool,
        commissioning_confirmed: bool,
    ) -> None:
        if stage not in {"commissioning", "baseline_building", "monitoring"}:
            stage = "commissioning"
        self.lifecycle_stage = stage
        self.baseline_learning_enabled = bool(learning_enabled)
        self.baseline_frozen = bool(frozen)
        self.alerts_enabled = bool(alerts_enabled)
        self.commissioning_confirmed = bool(commissioning_confirmed)
        self.diagnosis_engine.set_learning_mode(enabled=self.baseline_learning_enabled, frozen=self.baseline_frozen)

    def _init_control_state(self) -> None:
        state = self.control_store.ensure_state(self.args.elevator_id)
        stage = str(state.get("lifecycle_stage", "")).strip()
        if stage not in {"commissioning", "baseline_building", "monitoring"}:
            stage = "monitoring" if self.diagnosis_engine.baseline_ready else "commissioning"
        if stage == "commissioning" and self.diagnosis_engine.baseline_ready:
            stage = "monitoring"

        frozen = bool(state.get("baseline_frozen", False))
        commissioning_confirmed = bool(state.get("commissioning_confirmed", stage != "commissioning"))
        learning_enabled = bool(state.get("baseline_learning_enabled", stage != "commissioning"))
        alerts_enabled = bool(state.get("alerts_enabled", stage == "monitoring"))
        if stage == "commissioning":
            learning_enabled = False
            alerts_enabled = False
        self._last_applied_command_seq = int(
            (state.get("last_applied_command", {}) if isinstance(state.get("last_applied_command"), dict) else {}).get("seq", 0) or 0
        )
        self._apply_control_flags(
            stage=stage,
            learning_enabled=learning_enabled,
            frozen=frozen,
            alerts_enabled=alerts_enabled,
            commissioning_confirmed=commissioning_confirmed,
        )
        self._sync_control_state()

    def _runtime_control_snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "elevator_id": self.args.elevator_id,
            "connected": self.device is not None,
            "records_written": self.records_written,
            "skipped_total": self.skipped_total,
            "alerts_emitted": self.alerts_emitted,
            "baseline_ready": self.diagnosis_engine.baseline_ready,
            "baseline_count": self.diagnosis_engine.baseline_count,
            "last_screening_status": self.last_screening_status,
            "last_system_score": self.last_system_score,
            "last_fault_type": self.last_fault_type,
            "last_fault_confidence": self.last_fault_confidence,
            "last_risk_score": self.last_risk_score,
            "last_risk_level_now": self.last_risk_level_now,
            "last_risk_24h": self.last_risk_24h,
            "last_risk_level_24h": self.last_risk_level_24h,
            "last_alert_context_path": self.last_alert_context_path,
            "profile_path": self.profile_path,
            "profile_loaded": self.profile_loaded,
            "lifecycle_stage": self.lifecycle_stage,
            "baseline_learning_enabled": self.baseline_learning_enabled,
            "baseline_frozen": self.baseline_frozen,
            "alerts_enabled": self.alerts_enabled,
            "commissioning_confirmed": self.commissioning_confirmed,
            "updated_at_ms": now_ts_ms(),
        }

    def _sync_control_state(self) -> None:
        self.control_store.save_state(
            self.args.elevator_id,
            {
                "lifecycle_stage": self.lifecycle_stage,
                "commissioning_confirmed": self.commissioning_confirmed,
                "baseline_learning_enabled": self.baseline_learning_enabled,
                "baseline_frozen": self.baseline_frozen,
                "alerts_enabled": self.alerts_enabled,
                "profile_path": self.profile_path,
                "profile_loaded": self.profile_loaded,
            },
        )
        self.control_store.sync_runtime_state(self.args.elevator_id, self._runtime_control_snapshot())

    def _delete_profile_file(self) -> None:
        try:
            path = Path(self.profile_path)
            if path.exists():
                path.unlink()
        except Exception as ex:
            self.logger.warning("delete profile failed path=%s err=%s", self.profile_path, ex)

    def _reset_learning_state(self, *, stage: str, learning_enabled: bool) -> None:
        self.diagnosis_engine.reset_state()
        self.risk_predictor.reset_state()
        self.profile_loaded = False
        self.profile_load_error = ""
        self.last_fault_type = "unknown"
        self.last_fault_confidence = 0.0
        self.last_fault_source = ""
        self.last_screening_status = "normal"
        self.last_system_score = 0.0
        self.last_risk_score = 0.0
        self.last_risk_level_now = "normal"
        self.last_risk_24h = 0.0
        self.last_risk_level_24h = "normal"
        self.last_degradation_slope = 0.0
        self.last_alert_context_path = ""
        self._last_level = "normal"
        self._last_alert_emit_ms = None
        self.alert_context_rows.clear()
        self._delete_profile_file()
        self._apply_control_flags(
            stage=stage,
            learning_enabled=learning_enabled,
            frozen=False,
            alerts_enabled=False,
            commissioning_confirmed=(stage != "commissioning"),
        )
        self._sync_control_state()

    def _poll_control_command(self) -> None:
        state = self.control_store.ensure_state(self.args.elevator_id)
        command = state.get("pending_command", {}) if isinstance(state.get("pending_command"), dict) else {}
        seq = int(command.get("seq", 0) or 0)
        if seq <= self._last_applied_command_seq:
            return

        action = str(command.get("action", "")).strip()
        message = "ignored"
        if action == "start_baseline":
            self._reset_learning_state(stage="baseline_building", learning_enabled=True)
            message = "baseline building started"
        elif action == "reset_baseline":
            self._reset_learning_state(stage="baseline_building", learning_enabled=True)
            message = "baseline reset and restarted"
        elif action == "freeze_baseline":
            self._apply_control_flags(
                stage="monitoring" if self.diagnosis_engine.baseline_ready else self.lifecycle_stage,
                learning_enabled=False,
                frozen=True,
                alerts_enabled=self.diagnosis_engine.baseline_ready or self.alerts_enabled,
                commissioning_confirmed=True,
            )
            self._sync_control_state()
            message = "baseline frozen"
        elif action == "resume_baseline":
            next_stage = self.lifecycle_stage
            if next_stage == "commissioning":
                next_stage = "baseline_building"
            self._apply_control_flags(
                stage=next_stage,
                learning_enabled=True,
                frozen=False,
                alerts_enabled=(next_stage == "monitoring"),
                commissioning_confirmed=(next_stage != "commissioning"),
            )
            self._sync_control_state()
            message = "baseline adaptive learning resumed"
        else:
            self.logger.warning("unknown control action=%s", action)
            message = f"unknown action: {action}"

        self._last_applied_command_seq = seq
        self.control_store.acknowledge_command(
            self.args.elevator_id,
            command,
            message=message,
            state_patch={
                "lifecycle_stage": self.lifecycle_stage,
                "commissioning_confirmed": self.commissioning_confirmed,
                "baseline_learning_enabled": self.baseline_learning_enabled,
                "baseline_frozen": self.baseline_frozen,
                "alerts_enabled": self.alerts_enabled,
            },
        )

    def _maybe_promote_to_monitoring(self) -> None:
        if self.lifecycle_stage != "baseline_building":
            return
        if not self.diagnosis_engine.baseline_ready:
            return
        self._apply_control_flags(
            stage="monitoring",
            learning_enabled=not self.baseline_frozen,
            frozen=self.baseline_frozen,
            alerts_enabled=True,
            commissioning_confirmed=True,
        )
        self._sync_control_state()

    def _edge_site_name(self) -> str:
        return str(self.args.edge_sync_site_name or "").strip()

    def _edge_device_id(self) -> str:
        return str(self.args.edge_sync_device_id or self.args.elevator_id).strip() or self.args.elevator_id

    def _edge_sync_pending_count(self) -> int:
        if self.edge_sync_queue is None:
            return 0
        try:
            return int(self.edge_sync_queue.count())
        except Exception:
            return 0

    def _drain_edge_sync(self, *, force: bool = False) -> None:
        if self.edge_sync_queue is None or self.edge_sync_client is None:
            return
        now_mono = time.monotonic()
        if not force and now_mono - self._last_edge_sync_drain < self.args.edge_sync_drain_every_s:
            return
        self._last_edge_sync_drain = now_mono
        try:
            result = self.edge_sync_queue.drain(
                client=self.edge_sync_client,
                limit=self.args.edge_sync_drain_batch_size,
            )
            self.edge_sync_dispatch_count += int(result.get("sent", 0))
            if result.get("failed", 0):
                self.last_edge_sync_status = "degraded"
                self.last_edge_sync_error = str(result.get("last_error", ""))
            elif result.get("sent", 0):
                self.last_edge_sync_status = "success"
                self.last_edge_sync_error = ""
        except Exception as ex:
            self.last_edge_sync_status = "drain_failed"
            self.last_edge_sync_error = f"{type(ex).__name__}:{ex}"
            self.logger.warning("edge sync drain failed err=%s", ex)

    def _enqueue_edge_heartbeat(self, health_payload: dict[str, Any]) -> None:
        if self.edge_sync_queue is None:
            return
        ts_ms = int(health_payload.get("updated_at_ms") or now_ts_ms())
        if self._last_edge_sync_heartbeat_ms is not None:
            min_interval_ms = int(self.args.edge_sync_heartbeat_every_s * 1000.0)
            if ts_ms - self._last_edge_sync_heartbeat_ms < min_interval_ms:
                return
        payload = build_heartbeat_payload(
            elevator_id=self.args.elevator_id,
            device_id=self._edge_device_id(),
            site_id=self.args.edge_sync_site_id,
            site_name=self._edge_site_name(),
            health_payload=health_payload,
        )
        delivery_id = f"heartbeat:{payload['device_id']}:{payload['elevator_id']}:{ts_ms}"
        try:
            if self.edge_sync_queue.enqueue(
                delivery_id=delivery_id,
                endpoint="/api/v1/ingest/heartbeat",
                body=payload,
            ):
                self._last_edge_sync_heartbeat_ms = ts_ms
                self.last_edge_sync_status = "queued"
        except Exception as ex:
            self.last_edge_sync_status = "queue_failed"
            self.last_edge_sync_error = f"{type(ex).__name__}:{ex}"
            self.logger.warning("edge heartbeat queue failed err=%s", ex)

    def _enqueue_edge_alert(self, alert_payload: dict[str, Any], health_payload: dict[str, Any]) -> str:
        if self.edge_sync_queue is None:
            return ""
        payload = build_alert_payload(
            elevator_id=self.args.elevator_id,
            device_id=self._edge_device_id(),
            site_id=self.args.edge_sync_site_id,
            site_name=self._edge_site_name(),
            alert_payload=alert_payload,
            health_payload=health_payload,
        )
        event_id = str(payload.get("event_id", "")).strip()
        if not event_id:
            return ""
        try:
            self.edge_sync_queue.enqueue(
                delivery_id=f"alert:{event_id}",
                endpoint="/api/v1/ingest/alert",
                body=payload,
            )
            self.last_edge_sync_status = "queued"
            return event_id
        except Exception as ex:
            self.last_edge_sync_status = "queue_failed"
            self.last_edge_sync_error = f"{type(ex).__name__}:{ex}"
            self.logger.warning("edge alert queue failed err=%s", ex)
            return ""

    def _enqueue_edge_context(self, *, event_id: str, ts_ms: int, context_path: str) -> None:
        if self.edge_sync_queue is None or not event_id or not str(context_path).strip():
            return
        try:
            payload = build_context_payload(
                event_id=event_id,
                site_id=self.args.edge_sync_site_id,
                site_name=self._edge_site_name(),
                device_id=self._edge_device_id(),
                elevator_id=self.args.elevator_id,
                ts_ms=ts_ms,
                context_path=context_path,
                max_raw_bytes=self.args.edge_sync_max_context_bytes,
            )
            self.edge_sync_queue.enqueue(
                delivery_id=f"context:{event_id}",
                endpoint="/api/v1/ingest/context",
                body=payload,
            )
            self.last_edge_sync_status = "queued"
        except FileNotFoundError:
            return
        except Exception as ex:
            self.last_edge_sync_status = "queue_failed"
            self.last_edge_sync_error = f"{type(ex).__name__}:{ex}"
            self.logger.warning("edge context queue failed err=%s", ex)

    def _build_health_snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "elevator_id": self.args.elevator_id,
            "connected": self.device is not None,
            "alerts_emitted": self.alerts_emitted,
            "records_written": self.records_written,
            "baseline_ready": self.diagnosis_engine.baseline_ready,
            "baseline_count": self.diagnosis_engine.baseline_count,
            "last_screening_status": self.last_screening_status,
            "last_system_score": self.last_system_score,
            "last_fault_type": self.last_fault_type,
            "last_fault_confidence": self.last_fault_confidence,
            "last_fault_source": self.last_fault_source,
            "last_risk_score": self.last_risk_score,
            "last_risk_level_now": self.last_risk_level_now,
            "last_risk_24h": self.last_risk_24h,
            "last_risk_level_24h": self.last_risk_level_24h,
            "last_degradation_slope": self.last_degradation_slope,
            "lifecycle_stage": self.lifecycle_stage,
            "baseline_learning_enabled": self.baseline_learning_enabled,
            "baseline_frozen": self.baseline_frozen,
            "alerts_enabled": self.alerts_enabled,
            "commissioning_confirmed": self.commissioning_confirmed,
        }

    def _build_profile_payload(self) -> dict[str, Any]:
        return {
            "version": 2,
            "elevator_id": self.args.elevator_id,
            "updated_at_ms": now_ts_ms(),
            "edge_diagnosis": self.diagnosis_engine.snapshot_state(max_items=self.args.profile_max_items),
            "risk_predictor": self.risk_predictor.snapshot_state(max_items=self.args.profile_max_items),
        }

    def _load_profile(self) -> None:
        path = Path(self.profile_path)
        if not path.exists():
            return

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            profile_elevator_id = payload.get("elevator_id")
            if profile_elevator_id is not None and str(profile_elevator_id) != str(self.args.elevator_id):
                self.profile_load_error = "elevator_id_mismatch"
                self.logger.warning(
                    "profile elevator mismatch file=%s profile=%s runtime=%s",
                    path,
                    profile_elevator_id,
                    self.args.elevator_id,
                )
                return

            self.diagnosis_engine.load_state(payload.get("edge_diagnosis"))
            self.risk_predictor.load_state(payload.get("risk_predictor"))
            self.profile_loaded = True
            self.logger.info(
                "profile loaded path=%s diagnosis_baseline_count=%s",
                path,
                self.diagnosis_engine.baseline_count,
            )
        except Exception as ex:
            self.profile_load_error = str(ex)
            self.logger.warning("profile load failed path=%s err=%s", path, ex)

    def _save_profile(self, force: bool = False) -> None:
        if not force and self.records_written - self._last_profile_save_records < max(1, self.args.profile_save_every_n):
            return

        try:
            path = Path(self.profile_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = self._build_profile_payload()
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(path)

            self._last_profile_save_records = self.records_written
            self.profile_save_count += 1
        except Exception as ex:
            self.logger.warning("profile save failed path=%s err=%s", self.profile_path, ex)

    def _setup_signals(self) -> None:
        def _handle_signal(signum, _frame):
            self.logger.info("received signal=%s, shutting down", signum)
            self.stop_event.set()

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

    def _build_data_record(self, ts_ms: int) -> tuple[dict[str, Any], Optional[int], bool, bool]:
        assert self.device is not None
        snapshot = self.device.get_snapshot(REG_MAP.values())

        record = {"elevator_id": self.args.elevator_id, "ts_ms": ts_ms, "ts": format_ts_ms(ts_ms)}
        for name, reg in REG_MAP.items():
            record[name] = snapshot.get(reg)

        data_ts_ms = self.device.get_last_update_ts_ms()
        has_core = any(record.get(k) is not None for k in CORE_FIELDS)

        is_new = data_ts_ms is not None and data_ts_ms != self.last_written_data_ts
        is_fresh = data_ts_ms is not None and (ts_ms - data_ts_ms) <= max(0, self.args.max_data_age_ms)
        record["data_ts_ms"] = data_ts_ms
        record["is_new_frame"] = 1 if is_new else 0
        return record, data_ts_ms, is_new, is_fresh and has_core

    def _connect_device(self) -> bool:
        self.status = "connecting"
        self.logger.info(
            "connecting device name=%s port=%s baud=%s addr=%s",
            self.args.device_name,
            self.args.port,
            self.args.baud,
            hex(self.args.addr),
        )

        self.device = DeviceModel(
            self.args.device_name,
            self.args.port,
            self.args.baud,
            self.args.addr,
            verbose=False,
        )

        if not self.device.openDevice():
            self.logger.error("open device failed")
            self.device = None
            return False

        if not self.args.no_set_detect_hz:
            ok = self.device.writeReg(0x65, int(self.args.detect_hz))
            if not ok:
                self.logger.warning("set detect_hz failed, continue")

        try:
            self.device.startLoopRead(
                regAddr=self.args.reg_addr,
                regCount=self.args.reg_count,
                period_s=1.0 / max(1.0, self.args.sample_hz),
            )
        except Exception:
            self.logger.exception("start loop read failed")
            try:
                self.device.closeDevice()
            except Exception:
                pass
            self.device = None
            return False

        got_data = self.device.wait_for_data(timeout_s=max(0.0, self.args.startup_timeout_s))
        if not got_data:
            self.logger.warning("startup timeout: no first frame within %.2fs", self.args.startup_timeout_s)

        self.last_data_monotonic = time.monotonic()
        self.status = "running"
        self.logger.info("device connected")
        return True

    def _disconnect_device(self, reason: str) -> None:
        if self.device is None:
            return

        self.logger.warning("disconnect device: %s", reason)
        try:
            self.device.stopLoopRead()
            self.device.closeDevice()
        except Exception:
            self.logger.exception("error while closing device")
        finally:
            self.device = None
            self.status = "reconnecting"

    @staticmethod
    def _safe_file_token(value: str) -> str:
        token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
        token = token.strip("_")
        return token or "unknown"

    def _write_alert_context_jsonl_gz(self, *, ts_ms: int, level: str, fault_type: str) -> str:
        if not self.args.alert_context_enabled:
            return ""
        if not self.alert_context_rows:
            return ""

        pre_ms = int(self.args.alert_context_pre_seconds * 1000.0)
        cutoff = int(ts_ms) - pre_ms
        selected = [
            row
            for row in self.alert_context_rows
            if int(row.get("ts_ms", 0)) >= cutoff and int(row.get("ts_ms", 0)) <= int(ts_ms)
        ]
        if not selected:
            return ""

        out_dir = Path(self.args.alert_context_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        file_name = (
            f"{self._safe_file_token(self.args.elevator_id)}_"
            f"{int(ts_ms)}_"
            f"{self._safe_file_token(fault_type)}_"
            f"{self._safe_file_token(level)}.jsonl.gz"
        )
        out_path = out_dir / file_name
        try:
            jsonl_text = dumps_jsonl(selected)
            out_path.write_bytes(gzip.compress(jsonl_text.encode("utf-8")))
            self.last_alert_context_path = str(out_path)
            return str(out_path)
        except Exception as ex:
            self.logger.warning("write alert context failed path=%s err=%s", out_path, ex)
            return ""

    def _maybe_emit_alert(
        self,
        alert_recorder: DataRecorder,
        ts_ms: int,
        anomaly_result: dict[str, Any],
        fault_result: dict[str, Any],
        risk_result: dict[str, Any],
    ) -> None:
        if not self.alerts_enabled:
            self._last_level = "normal"
            return
        anomaly_level = str(anomaly_result["level"])
        risk_level_24h = str(risk_result.get("risk_level_24h", "normal"))

        predictive_only = False
        level = anomaly_level
        if anomaly_level == "normal":
            if should_emit_predictive_alert(
                risk_level_24h=risk_level_24h,
                risk_emit_on_normal=self.args.risk_emit_on_normal,
                risk_emit_min_level=self.args.risk_emit_min_level,
            ):
                level = "warning"
                predictive_only = True
            else:
                self._last_level = "normal"
                return

        cooldown_ms = int(max(0.0, self.args.alert_cooldown_s) * 1000)
        should_emit = False
        if self._last_level != level:
            should_emit = True
        elif self._last_alert_emit_ms is None:
            should_emit = True
        elif ts_ms - self._last_alert_emit_ms >= cooldown_ms:
            should_emit = True

        self._last_level = level
        if not should_emit:
            return

        alert_context_path = self._write_alert_context_jsonl_gz(
            ts_ms=ts_ms,
            level=level,
            fault_type=str(fault_result.get("fault_type", "unknown")),
        )
        alert = build_alert_record(
            elevator_id=self.args.elevator_id,
            ts_ms=ts_ms,
            level=level,
            anomaly_level=anomaly_level,
            predictive_only=predictive_only,
            anomaly_result=anomaly_result,
            fault_result=fault_result,
            risk_result=risk_result,
            alert_context_path=alert_context_path,
            records_written=self.records_written,
            skipped_total=self.skipped_total,
        )

        alert_recorder.write(alert)
        self.last_alert_context_path = alert_context_path
        edge_event_id = self._enqueue_edge_alert(alert, self._build_health_snapshot())
        if edge_event_id and alert_context_path:
            self._enqueue_edge_context(event_id=edge_event_id, ts_ms=ts_ms, context_path=alert_context_path)
        self._drain_edge_sync(force=True)
        self._last_alert_emit_ms = ts_ms
        self.alerts_emitted += 1

        if level == "anomaly":
            self.logger.error(
                "ALERT anomaly score=%s reasons=%s fault_type=%s fault_confidence=%s risk_score=%s risk_24h=%s",
                alert["score"],
                alert["reasons"],
                alert["fault_type"],
                alert["fault_confidence"],
                alert["risk_score"],
                alert["risk_24h"],
            )
        else:
            self.logger.warning(
                "ALERT warning score=%s reasons=%s fault_type=%s fault_confidence=%s risk_score=%s risk_24h=%s predictive_only=%s",
                alert["score"],
                alert["reasons"],
                alert["fault_type"],
                alert["fault_confidence"],
                alert["risk_score"],
                alert["risk_24h"],
                alert["predictive_only"],
            )

    def _write_health(self, force: bool = False) -> None:
        now_mono = time.monotonic()
        if not force and now_mono - self._last_health_write < max(1.0, self.args.health_every_s):
            return

        payload = {
            "status": self.status,
            "elevator_id": self.args.elevator_id,
            "pid": os.getpid(),
            "uptime_s": round(now_mono - self.started_monotonic, 3),
            "connected": self.device is not None,
            "last_data_ts_ms": self.last_data_ts_ms,
            "records_written": self.records_written,
            "skipped_total": self.skipped_total,
            "alerts_emitted": self.alerts_emitted,
            "baseline_ready": self.diagnosis_engine.baseline_ready,
            "baseline_count": self.diagnosis_engine.baseline_count,
            "diagnosis_window_s": self.args.diagnosis_window_s,
            "diagnosis_step_s": self.args.diagnosis_step_s,
            "last_screening_status": self.last_screening_status,
            "last_system_score": self.last_system_score,
            "last_fault_type": self.last_fault_type,
            "last_fault_confidence": self.last_fault_confidence,
            "last_fault_source": self.last_fault_source,
            "risk_enabled": self.args.risk_enabled,
            "last_risk_score": self.last_risk_score,
            "last_risk_level_now": self.last_risk_level_now,
            "last_risk_24h": self.last_risk_24h,
            "last_risk_level_24h": self.last_risk_level_24h,
            "last_degradation_slope": self.last_degradation_slope,
            "profile_path": self.profile_path,
            "profile_loaded": self.profile_loaded,
            "profile_load_error": self.profile_load_error,
            "profile_save_count": self.profile_save_count,
            "lifecycle_stage": self.lifecycle_stage,
            "baseline_learning_enabled": self.baseline_learning_enabled,
            "baseline_frozen": self.baseline_frozen,
            "alerts_enabled": self.alerts_enabled,
            "commissioning_confirmed": self.commissioning_confirmed,
            "alert_context_enabled": self.args.alert_context_enabled,
            "alert_context_dir": self.args.alert_context_dir,
            "alert_context_pre_seconds": self.args.alert_context_pre_seconds,
            "last_alert_context_path": self.last_alert_context_path,
            "edge_sync_enabled": self.args.edge_sync_enabled,
            "edge_sync_client_ready": self.edge_sync_client is not None,
            "edge_sync_base_url": self.args.edge_sync_base_url,
            "edge_sync_queue_path": self.args.edge_sync_queue_path,
            "edge_sync_pending": self._edge_sync_pending_count(),
            "edge_sync_dispatch_count": self.edge_sync_dispatch_count,
            "last_edge_sync_status": self.last_edge_sync_status,
            "last_edge_sync_error": self.last_edge_sync_error,
            "updated_at_ms": now_ts_ms(),
        }

        out_path = Path(self.args.health_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(out_path)

        self._last_health_write = now_mono
        self.control_store.sync_runtime_state(self.args.elevator_id, payload)
        self._enqueue_edge_heartbeat(payload)
        self._drain_edge_sync(force=force)

    def _ensure_csv_schema(self, path: str, fieldnames: list[str]) -> None:
        out_path = Path(path)
        if not out_path.exists() or out_path.stat().st_size == 0:
            return
        try:
            with out_path.open("r", encoding="utf-8", newline="") as fp:
                header = fp.readline().strip()
        except Exception as ex:
            self.logger.warning("schema check failed path=%s err=%s", out_path, ex)
            return

        expected = ",".join(fieldnames)
        if header == expected:
            return

        backup = out_path.with_name(f"{out_path.stem}_legacy_{now_ts_ms()}{out_path.suffix}")
        try:
            out_path.replace(backup)
            self.logger.warning("csv schema changed, moved old file to %s", backup)
        except Exception as ex:
            self.logger.warning("csv schema rotate failed path=%s err=%s", out_path, ex)

    def run(self) -> int:
        self._setup_signals()
        self._log_runtime_config()
        self._ensure_csv_schema(self.args.output_alert, ALERT_FIELDS)

        period_s = 1.0 / max(1.0, self.args.sample_hz)
        started_wall = time.time()

        self.logger.info("monitor started")

        with DataRecorder(
            self.args.output_data,
            file_format="csv",
            fieldnames=DATA_FIELDS,
            flush=False,
            flush_every_n=self.args.flush_every_n,
        ) as data_recorder, DataRecorder(
            self.args.output_alert,
            file_format="csv",
            fieldnames=ALERT_FIELDS,
            flush=True,
        ) as alert_recorder:
            next_t = time.perf_counter()

            while not self.stop_event.is_set():
                if self.args.run_seconds is not None and time.time() - started_wall >= self.args.run_seconds:
                    self.logger.info("run_seconds reached: %.2f", self.args.run_seconds)
                    break

                if self.device is None:
                    self._poll_control_command()
                    if not self._connect_device():
                        self._write_health(force=True)
                        time.sleep(max(0.5, self.args.reconnect_backoff_s))
                        continue

                try:
                    self.total_loops += 1
                    ts_ms = now_ts_ms()
                    self._poll_control_command()

                    record, data_ts_ms, is_new, accept = self._build_data_record(ts_ms)

                    if data_ts_ms is not None:
                        self.last_data_ts_ms = data_ts_ms
                        self.last_data_monotonic = time.monotonic()

                    if accept and is_new:
                        data_recorder.write(record)
                        self.alert_context_rows.append(dict(record))
                        self.records_written += 1
                        self.last_written_data_ts = data_ts_ms

                        diagnosis = self.diagnosis_engine.update(ts_ms, record)
                        anomaly_result = diagnosis_to_anomaly_result(
                            diagnosis,
                            baseline_ready=self.diagnosis_engine.baseline_ready,
                            baseline_count=self.diagnosis_engine.baseline_count,
                        )
                        fault_result = diagnosis_to_fault_result(diagnosis)
                        self.last_fault_type = str(fault_result.get("fault_type", "unknown"))
                        self.last_fault_confidence = float(fault_result.get("fault_confidence", 0.0))
                        self.last_fault_source = str(fault_result.get("fault_source", ""))
                        self.last_screening_status = str(fault_result.get("fault_screening_status", "normal"))
                        self.last_system_score = float(fault_result.get("system_score", 0.0))

                        risk_result = self.risk_predictor.update(
                            ts_ms,
                            record,
                            anomaly_result,
                            fault_result,
                            model_probability=None,
                        )
                        self.last_risk_score = float(risk_result.get("risk_score", 0.0))
                        self.last_risk_level_now = str(risk_result.get("risk_level_now", "normal"))
                        self.last_risk_24h = float(risk_result.get("risk_24h", 0.0))
                        self.last_risk_level_24h = str(risk_result.get("risk_level_24h", "normal"))
                        self.last_degradation_slope = float(risk_result.get("degradation_slope", 0.0))
                        self._maybe_promote_to_monitoring()
                        self._maybe_emit_alert(alert_recorder, ts_ms, anomaly_result, fault_result, risk_result)
                        self._save_profile()

                        if self.args.print_every_n > 0 and self.records_written % self.args.print_every_n == 0:
                            self.logger.info(
                                "record #%s Ax=%s Ay=%s Az=%s Gx=%s Gy=%s Gz=%s t=%s screening=%s system_score=%.2f",
                                self.records_written,
                                record.get("Ax"),
                                record.get("Ay"),
                                record.get("Az"),
                                record.get("Gx"),
                                record.get("Gy"),
                                record.get("Gz"),
                                record.get("t"),
                                self.last_screening_status,
                                self.last_system_score,
                            )
                    else:
                        self.skipped_total += 1
                        if self.args.warn_every_n > 0 and self.skipped_total % self.args.warn_every_n == 0:
                            reasons = []
                            if not is_new:
                                reasons.append("non_new_frame")
                            if not accept:
                                reasons.append("stale_or_missing")
                            self.logger.warning(
                                "skip total=%s reason=%s",
                                self.skipped_total,
                                ",".join(reasons) if reasons else "unknown",
                            )

                    if self.device is not None:
                        no_data_s = time.monotonic() - self.last_data_monotonic
                        if no_data_s > max(1.0, self.args.reconnect_no_data_s):
                            self._disconnect_device(f"no data for {no_data_s:.1f}s")

                    self._write_health()

                except Exception:
                    self.logger.exception("tick failed")
                    self._disconnect_device("tick_exception")
                    time.sleep(max(0.5, self.args.reconnect_backoff_s))

                next_t += period_s
                sleep_s = next_t - time.perf_counter()
                if sleep_s > 0:
                    time.sleep(sleep_s)
                else:
                    next_t = time.perf_counter()

        if self.device is not None:
            self._disconnect_device("shutdown")

        self.status = "stopped"
        self._save_profile(force=True)
        self._write_health(force=True)
        self.logger.info(
            "monitor stopped loops=%s written=%s skipped=%s alerts=%s",
            self.total_loops,
            self.records_written,
            self.skipped_total,
            self.alerts_emitted,
        )
        return 0


def main() -> int:
    args = build_arg_parser().parse_args()
    monitor = RealtimeMonitor(args)
    return monitor.run()


if __name__ == "__main__":
    raise SystemExit(main())
