from __future__ import annotations

import argparse
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
from ..data_recorder import DataRecorder, format_ts_ms, now_ts_ms
from ..device_model import DeviceModel
from ..dify_client import DifyWorkflowClient
from ..fault_types import FaultTypeEngine
from ..generated_algorithm import ForecastResult, GeneratedAlgorithmPrediction, GeneratedFaultAlgorithmRunner, OnlineFeatureForecaster
from ..maintenance_workflow import build_maintenance_package, load_optional_json
from ..model_inference import CentroidModelRunner, ModelPrediction, OnlineWindowBuffer, is_non_fault_label
from ..risk_predictor import OnlineRiskPredictor
from .alerting import ALERT_FIELDS, build_alert_record, should_emit_predictive_alert
from .args import build_arg_parser
from .constants import DATA_FIELDS, RAIL_WEAR_FIELDS
from .pipeline import OnlineAnomalyDetector


class RealtimeMonitor:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.args.fault_model_min_confidence = min(1.0, max(0.0, float(self.args.fault_model_min_confidence)))
        self.args.risk_model_weight = max(0.0, float(self.args.risk_model_weight))
        self.args.fault_model_top_k = max(1, int(self.args.fault_model_top_k))
        if self.args.fault_fusion_mode not in {"rule_primary", "model_primary"}:
            self.args.fault_fusion_mode = "rule_primary"
        self.args.model_window_min_samples = max(1, int(self.args.model_window_min_samples))
        self.args.model_window_s = max(1.0, float(self.args.model_window_s))
        self.args.generated_algo_min_confidence = min(1.0, max(0.0, float(self.args.generated_algo_min_confidence)))
        self.args.generated_algo_top_k = max(1, int(self.args.generated_algo_top_k))
        self.args.generated_algo_horizon_s = max(1.0, float(self.args.generated_algo_horizon_s))
        self.args.generated_algo_forecast_min_points = max(3, int(self.args.generated_algo_forecast_min_points))
        self.args.alert_context_pre_seconds = max(1.0, float(self.args.alert_context_pre_seconds))
        self.args.alert_context_max_rows = max(100, int(self.args.alert_context_max_rows))
        self.args.reg_count = max(1, int(self.args.reg_count))
        self.args.reg_addr = int(self.args.reg_addr)
        self.args.dify_timeout_s = max(1.0, float(self.args.dify_timeout_s))
        self.args.dify_cooldown_s = max(0.0, float(self.args.dify_cooldown_s))
        if self.args.dify_response_mode not in {"blocking", "streaming"}:
            self.args.dify_response_mode = "blocking"
        if self.args.dify_min_level not in {"warning", "anomaly"}:
            self.args.dify_min_level = "warning"
        self.stop_event = Event()

        self.logger = self._build_logger(args)

        self.device: Optional[DeviceModel] = None
        self.detector = OnlineAnomalyDetector(
            baseline_size=args.baseline_size,
            baseline_min_records=args.baseline_min_records,
            baseline_refresh_every=args.baseline_refresh_every,
            stale_limit=args.stale_limit,
            warning_z=args.warning_z,
            anomaly_z=args.anomaly_z,
        )
        self.fault_engine = FaultTypeEngine(
            enabled=args.fault_type_enabled,
            min_level=args.fault_type_min_level,
            top_k=args.fault_type_top_k,
            stale_limit=args.stale_limit,
            baseline_size=args.fault_baseline_size,
            baseline_min_records=args.fault_baseline_min_records,
            vibration_warning_z=args.fault_vibration_warning_z,
            vibration_shock_z=args.fault_vibration_shock_z,
            temp_rise_c=args.fault_temp_rise_c,
            temp_overheat_c=args.fault_temp_overheat_c,
            sample_hz=args.sample_hz,
        )
        self.risk_predictor = OnlineRiskPredictor(
            enabled=args.risk_enabled,
            stale_limit=args.stale_limit,
            baseline_size=args.risk_baseline_size,
            baseline_min_records=args.risk_baseline_min_records,
            trend_window_s=args.risk_trend_window_s,
            smooth_alpha=args.risk_smooth_alpha,
            anomaly_scale=args.risk_anomaly_scale,
            fault_weight=args.risk_fault_weight,
            vibration_weight=args.risk_vibration_weight,
            temperature_weight=args.risk_temperature_weight,
            model_weight=args.risk_model_weight,
        )
        self.window_buffer = OnlineWindowBuffer(
            window_s=args.model_window_s,
            min_samples=args.model_window_min_samples,
            max_samples=max(args.profile_max_items, int(args.sample_hz * args.model_window_s * 2)),
        )
        self.generated_algo_runner = self._load_generated_algo_runner(args.generated_algo_path)
        self.feature_forecaster = OnlineFeatureForecaster(
            horizon_s=args.generated_algo_horizon_s,
            min_points=args.generated_algo_forecast_min_points,
            max_points=max(args.profile_max_items, args.generated_algo_forecast_min_points * 20),
        )
        self.fault_model_runner = self._load_model_runner(args.fault_model_path, kind="fault")
        self.risk_model_runner = self._load_model_runner(args.risk_model_path, kind="risk")
        self.alert_context_rows: deque[dict[str, Any]] = deque(maxlen=self.args.alert_context_max_rows)
        self._dify_manifest_payload: dict[str, Any] = self._load_dify_manifest()
        self.dify_client = self._build_dify_client()

        self.started_monotonic = time.monotonic()
        self.last_data_monotonic = 0.0
        self.last_data_ts_ms: Optional[int] = None
        self.last_written_data_ts: Optional[int] = None

        self.total_loops = 0
        self.records_written = 0
        self.skipped_total = 0
        self.alerts_emitted = 0
        self._last_alert_emit_ms: Optional[int] = None
        self._last_dify_emit_ms: Optional[int] = None
        self._last_level = "normal"
        self.last_fault_type = "unknown"
        self.last_fault_confidence = 0.0
        self.last_fault_model_pred = ""
        self.last_fault_model_confidence = 0.0
        self.last_fault_model_top_k = ""
        self.last_fault_generated_pred = ""
        self.last_fault_generated_confidence = 0.0
        self.last_fault_generated_top_k = ""
        self.last_forecast_a_mag = 0.0
        self.last_forecast_g_mag = 0.0
        self.last_forecast_t = 0.0
        self.last_forecast_confidence = 0.0
        self.last_risk_score = 0.0
        self.last_risk_level_now = "normal"
        self.last_risk_24h = 0.0
        self.last_risk_level_24h = "normal"
        self.last_degradation_slope = 0.0
        self.last_risk_model_score = 0.0
        self.last_alert_context_path = ""
        self.last_dify_status = ""
        self.last_dify_workflow_run_id = ""
        self.last_dify_task_id = ""
        self.last_dify_error = ""
        self.dify_dispatch_count = 0

        self._last_health_write = 0.0
        self.status = "starting"

        self.profile_path = self._resolve_profile_path(args.profile_path, args.elevator_id)
        self.profile_loaded = False
        self.profile_load_error = ""
        self.profile_save_count = 0
        self._last_profile_save_records = 0
        self._load_profile()

    def _log_runtime_config(self) -> None:
        self.logger.info(
            "runtime config elevator_id=%s port=%s baud=%s addr=%s sample_hz=%s detect_hz=%s reg_addr=%s reg_count=%s max_data_age_ms=%s "
            "reconnect_no_data_s=%s reconnect_backoff_s=%s output_data=%s output_alert=%s output_rail_wear_alert=%s health_path=%s "
            "alert_context_enabled=%s alert_context_dir=%s alert_context_pre_seconds=%s alert_context_max_rows=%s "
            "fault_enabled=%s fault_min_level=%s fault_top_k=%s "
            "fault_fusion_mode=%s "
            "risk_enabled=%s risk_emit_on_normal=%s risk_emit_min_level=%s "
            "model_window_s=%s model_window_min_samples=%s "
            "fault_model_path=%s fault_model_loaded=%s fault_model_min_conf=%s "
            "generated_algo_path=%s generated_algo_loaded=%s generated_algo_min_conf=%s generated_algo_horizon_s=%s "
            "risk_model_path=%s risk_model_loaded=%s risk_model_weight=%s risk_model_pos_label=%s "
            "dify_enabled=%s dify_client_ready=%s dify_base_url=%s dify_min_level=%s dify_cooldown_s=%s "
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
            self.args.output_rail_wear_alert,
            self.args.health_path,
            self.args.alert_context_enabled,
            self.args.alert_context_dir,
            self.args.alert_context_pre_seconds,
            self.args.alert_context_max_rows,
            self.args.fault_type_enabled,
            self.args.fault_type_min_level,
            self.args.fault_type_top_k,
            self.args.fault_fusion_mode,
            self.args.risk_enabled,
            self.args.risk_emit_on_normal,
            self.args.risk_emit_min_level,
            self.args.model_window_s,
            self.args.model_window_min_samples,
            self.args.fault_model_path,
            self.fault_model_runner is not None,
            self.args.fault_model_min_confidence,
            self.args.generated_algo_path,
            self.generated_algo_runner is not None,
            self.args.generated_algo_min_confidence,
            self.args.generated_algo_horizon_s,
            self.args.risk_model_path,
            self.risk_model_runner is not None,
            self.args.risk_model_weight,
            self.args.risk_model_positive_label,
            self.args.dify_enabled,
            self.dify_client is not None,
            self.args.dify_base_url,
            self.args.dify_min_level,
            self.args.dify_cooldown_s,
            self.profile_path,
            self.profile_loaded,
        )

    @staticmethod
    def _build_logger(args: argparse.Namespace) -> logging.Logger:
        logger = logging.getLogger("elevator.monitor")
        logger.setLevel(getattr(logging, args.log_level.upper(), logging.INFO))
        logger.handlers.clear()

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

    def _load_model_runner(self, model_path: str, kind: str) -> Optional[CentroidModelRunner]:
        path = str(model_path or "").strip()
        if not path:
            return None
        try:
            runner = CentroidModelRunner(path)
            self.logger.info("%s model loaded path=%s classes=%s", kind, runner.path, ",".join(runner.model.classes))
            return runner
        except Exception as ex:
            self.logger.warning("%s model load failed path=%s err=%s", kind, path, ex)
            return None

    def _load_generated_algo_runner(self, algorithm_path: str) -> Optional[GeneratedFaultAlgorithmRunner]:
        path = str(algorithm_path or "").strip()
        if not path:
            return None
        try:
            runner = GeneratedFaultAlgorithmRunner(path)
            self.logger.info(
                "generated algorithm loaded path=%s classes=%s",
                runner.path,
                ",".join(cls.label for cls in runner.classes),
            )
            return runner
        except Exception as ex:
            self.logger.warning("generated algorithm load failed path=%s err=%s", path, ex)
            return None

    def _load_dify_manifest(self) -> dict[str, Any]:
        manifest_path = str(self.args.dify_manifest_json or "").strip()
        if not manifest_path:
            return {}
        payload = load_optional_json(manifest_path)
        if not payload:
            self.logger.warning("dify manifest missing or invalid path=%s", manifest_path)
        return payload

    def _build_dify_client(self) -> Optional[DifyWorkflowClient]:
        if not self.args.dify_enabled:
            return None

        base_url = str(self.args.dify_base_url or "").strip()
        api_key = str(self.args.dify_api_key or "").strip()
        if not base_url or not api_key:
            self.logger.warning("dify enabled but base_url/api_key missing; dify dispatch disabled")
            return None

        try:
            client = DifyWorkflowClient(
                base_url=base_url,
                api_key=api_key,
                timeout_s=self.args.dify_timeout_s,
                verify_ssl=self.args.dify_verify_ssl,
            )
            self.logger.info("dify client ready endpoint=%s", client.endpoint)
            return client
        except Exception as ex:
            self.logger.warning("dify client init failed err=%s", ex)
            return None

    def _build_health_snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "elevator_id": self.args.elevator_id,
            "connected": self.device is not None,
            "alerts_emitted": self.alerts_emitted,
            "records_written": self.records_written,
            "baseline_ready": self.detector.baseline_ready,
            "last_fault_type": self.last_fault_type,
            "last_fault_confidence": self.last_fault_confidence,
            "last_risk_score": self.last_risk_score,
            "last_risk_level_now": self.last_risk_level_now,
            "last_risk_24h": self.last_risk_24h,
            "last_risk_level_24h": self.last_risk_level_24h,
            "last_degradation_slope": self.last_degradation_slope,
        }

    def _dispatch_dify_alert(
        self,
        *,
        ts_ms: int,
        level: str,
        alert_row: dict[str, Any],
    ) -> dict[str, Any]:
        empty = {
            "dify_dispatched": 0,
            "dify_status": "disabled",
            "dify_workflow_run_id": "",
            "dify_task_id": "",
            "dify_error": "",
        }
        if self.dify_client is None:
            return empty

        level_rank = {"normal": 0, "warning": 1, "anomaly": 2}
        current_rank = level_rank.get(str(level).strip().lower(), 0)
        min_rank = level_rank.get(str(self.args.dify_min_level).strip().lower(), 1)
        if current_rank < min_rank:
            empty["dify_status"] = "skipped_level"
            return empty

        cooldown_ms = int(max(0.0, self.args.dify_cooldown_s) * 1000.0)
        if self._last_dify_emit_ms is not None and ts_ms - self._last_dify_emit_ms < cooldown_ms:
            empty["dify_status"] = "skipped_cooldown"
            return empty

        package = build_maintenance_package(
            alert_rows=[{str(k): str(v) for k, v in alert_row.items()}],
            health_payload=self._build_health_snapshot(),
            site_name=str(self.args.dify_site_name or ""),
            alert_csv_path=self.args.output_alert,
            health_json_path=self.args.health_path,
            manifest_payload=self._dify_manifest_payload,
            manifest_path=self.args.dify_manifest_json,
        )
        inputs = dict(package.get("dify_inputs", {}))
        inputs["monitor_alert_level"] = level
        inputs["monitor_alert_ts_ms"] = int(ts_ms)
        inputs["monitor_alert_csv"] = self.args.output_alert
        inputs["maintenance_package"] = json.dumps(package, ensure_ascii=False)

        user = str(self.args.dify_user or "").strip() or self.args.elevator_id
        dispatch = self.dify_client.run_workflow(
            inputs=inputs,
            user=user,
            response_mode=self.args.dify_response_mode,
        )
        fields = dispatch.to_alert_fields()

        self.last_dify_status = str(fields.get("dify_status", ""))
        self.last_dify_workflow_run_id = str(fields.get("dify_workflow_run_id", ""))
        self.last_dify_task_id = str(fields.get("dify_task_id", ""))
        self.last_dify_error = str(fields.get("dify_error", ""))

        if dispatch.dispatched:
            self._last_dify_emit_ms = ts_ms
            self.dify_dispatch_count += 1
            self.logger.info(
                "dify dispatched workflow_run_id=%s task_id=%s latency_ms=%s",
                dispatch.workflow_run_id,
                dispatch.task_id,
                dispatch.latency_ms,
            )
        else:
            self.logger.warning(
                "dify dispatch failed status=%s http_status=%s error=%s",
                dispatch.status,
                dispatch.http_status,
                dispatch.error,
            )

        return fields

    def _predict_fault_model(self, features: Optional[dict[str, float]]) -> Optional[ModelPrediction]:
        if self.fault_model_runner is None or features is None:
            return None
        try:
            return self.fault_model_runner.predict(features, top_k=self.args.fault_model_top_k)
        except Exception as ex:
            self.logger.warning("fault model predict failed err=%s", ex)
            return None

    def _predict_generated_algo(self, features: Optional[dict[str, float]]) -> Optional[GeneratedAlgorithmPrediction]:
        if self.generated_algo_runner is None or features is None:
            return None
        try:
            return self.generated_algo_runner.predict(features, top_k=self.args.generated_algo_top_k)
        except Exception as ex:
            self.logger.warning("generated algorithm predict failed err=%s", ex)
            return None

    def _merge_fault_result(
        self,
        rule_result: dict[str, Any],
        model_pred: Optional[ModelPrediction],
        generated_pred: Optional[GeneratedAlgorithmPrediction],
        anomaly_result: dict[str, Any],
        features: Optional[dict[str, float]],
        forecast_result: Optional[ForecastResult],
    ) -> dict[str, Any]:
        merged = dict(rule_result)
        level = str(anomaly_result.get("level", "normal"))
        level_rank = {"normal": 0, "warning": 1, "anomaly": 2}.get(level, 0)
        min_level_rank = {"warning": 1, "anomaly": 2}.get(self.args.fault_type_min_level, 1)
        model_allowed = level_rank >= min_level_rank

        model_label = ""
        model_conf = 0.0
        model_top_k = ""
        use_model = False
        generated_label = ""
        generated_conf = 0.0
        generated_top_k = ""
        use_generated = False
        generated_reason = ""
        rule_fault = str(rule_result.get("fault_type", "unknown"))
        rule_conf = float(rule_result.get("fault_confidence", 0.0))
        rule_unknown = rule_fault in {"unknown", "disabled", ""}

        if model_pred is not None:
            model_label = model_pred.label
            model_conf = float(model_pred.confidence)
            model_top_k = model_pred.top_k
            if model_allowed and not is_non_fault_label(model_label) and model_conf >= self.args.fault_model_min_confidence:
                if self.args.fault_fusion_mode == "model_primary":
                    # 模型优先：已知规则类型也允许被高置信模型覆盖。
                    use_model = rule_unknown or model_conf >= rule_conf + 0.08
                else:
                    # 规则优先：只有规则无法给出类型时才启用模型主判。
                    use_model = rule_unknown

        if generated_pred is not None:
            generated_label = generated_pred.label
            generated_conf = float(generated_pred.confidence)
            generated_top_k = generated_pred.top_k
            generated_reason = f"generated_conf={generated_conf:.3f}|score={generated_pred.best_score:.3f}"
            if generated_pred.threshold > 0:
                generated_reason += f"|threshold={generated_pred.threshold:.3f}"

            if forecast_result is not None and features is not None and forecast_result.confidence >= 0.5:
                cur_a = float(features.get("A_mag_mean", 0.0))
                pred_a = float(forecast_result.values.get("A_mag_mean", cur_a))
                if cur_a > 0.0 and pred_a > cur_a * 1.10:
                    generated_conf = min(1.0, generated_conf + 0.06)
                    generated_reason += "|forecast_a_rise"

            if (
                model_allowed
                and not use_model
                and not is_non_fault_label(generated_label)
                and generated_conf >= self.args.generated_algo_min_confidence
                and rule_unknown
            ):
                use_generated = True

        # 融合优先级：
        # - rule_primary: 规则主判，模型/算法仅在规则未知时接管。
        # - model_primary: 模型主判，规则作为兜底。
        if use_model:
            merged["fault_type"] = model_label
            merged["fault_confidence"] = model_conf
            merged["fault_source"] = f"model:{self.fault_model_runner.name if self.fault_model_runner else 'fault'}"
            merged["fault_reasons"] = f"model_conf={model_conf:.3f}"
            merged["fault_candidates"] = model_top_k
        elif use_generated:
            merged["fault_type"] = generated_label
            merged["fault_confidence"] = generated_conf
            source_name = self.generated_algo_runner.name if self.generated_algo_runner is not None else "generated"
            merged["fault_source"] = f"generated_algo:{source_name}"
            merged["fault_reasons"] = generated_reason
            merged["fault_candidates"] = generated_top_k
        elif model_top_k:
            existing = str(merged.get("fault_candidates", ""))
            merged["fault_candidates"] = f"{existing}|model:{model_top_k}" if existing else f"model:{model_top_k}"

        if generated_top_k:
            existing = str(merged.get("fault_candidates", ""))
            marker = f"generated:{generated_top_k}"
            if marker not in existing:
                merged["fault_candidates"] = f"{existing}|{marker}" if existing else marker

        merged["fault_model_pred"] = model_label
        merged["fault_model_confidence"] = model_conf
        merged["fault_model_top_k"] = model_top_k
        merged["fault_generated_pred"] = generated_label
        merged["fault_generated_confidence"] = generated_conf
        merged["fault_generated_top_k"] = generated_top_k
        if forecast_result is not None:
            merged["forecast_a_mag"] = float(forecast_result.values.get("A_mag_mean", 0.0))
            merged["forecast_g_mag"] = float(forecast_result.values.get("G_mag_mean", 0.0))
            merged["forecast_t"] = float(forecast_result.values.get("T_mean", 0.0))
            merged["forecast_confidence"] = float(forecast_result.confidence)
        else:
            merged["forecast_a_mag"] = 0.0
            merged["forecast_g_mag"] = 0.0
            merged["forecast_t"] = 0.0
            merged["forecast_confidence"] = 0.0
        return merged

    def _predict_risk_model_probability(self, features: Optional[dict[str, float]]) -> Optional[float]:
        if self.risk_model_runner is None or features is None:
            return None
        try:
            pred = self.risk_model_runner.predict(features, top_k=2)
            if pred is None:
                return None
            pos_label = self.args.risk_model_positive_label
            if pos_label in pred.probabilities:
                return float(pred.probabilities[pos_label])
            if len(pred.probabilities) == 2:
                # Fallback for unexpected label naming: take larger probability as risk score.
                return float(max(pred.probabilities.values()))
            return None
        except Exception as ex:
            self.logger.warning("risk model predict failed err=%s", ex)
            return None

    def _build_profile_payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "elevator_id": self.args.elevator_id,
            "updated_at_ms": now_ts_ms(),
            "anomaly_detector": self.detector.snapshot_state(max_items=self.args.profile_max_items),
            "fault_engine": self.fault_engine.snapshot_state(max_items=self.args.profile_max_items),
            "feature_forecaster": self.feature_forecaster.snapshot_state(max_items=self.args.profile_max_items),
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

            self.detector.load_state(payload.get("anomaly_detector"))
            self.fault_engine.load_state(payload.get("fault_engine"))
            self.feature_forecaster.load_state(payload.get("feature_forecaster"))
            self.risk_predictor.load_state(payload.get("risk_predictor"))
            self.profile_loaded = True
            self.logger.info(
                "profile loaded path=%s baseline_count=%s",
                path,
                self.detector.baseline_count,
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

    def _write_alert_context_csv(self, *, ts_ms: int, level: str, fault_type: str) -> str:
        if not self.args.alert_context_enabled:
            return ""
        if not self.alert_context_rows:
            return ""

        pre_ms = int(self.args.alert_context_pre_seconds * 1000.0)
        cutoff = int(ts_ms) - pre_ms
        selected = [row for row in self.alert_context_rows if int(row.get("ts_ms", 0)) >= cutoff and int(row.get("ts_ms", 0)) <= int(ts_ms)]
        if not selected:
            return ""

        out_dir = Path(self.args.alert_context_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        file_name = (
            f"{self._safe_file_token(self.args.elevator_id)}_"
            f"{int(ts_ms)}_"
            f"{self._safe_file_token(fault_type)}_"
            f"{self._safe_file_token(level)}.csv"
        )
        out_path = out_dir / file_name
        try:
            with DataRecorder(str(out_path), file_format="csv", fieldnames=DATA_FIELDS, flush=True) as recorder:
                recorder.write_many(selected)
            self.last_alert_context_path = str(out_path)
            return str(out_path)
        except Exception as ex:
            self.logger.warning("write alert context failed path=%s err=%s", out_path, ex)
            return ""

    def _maybe_emit_alert(
        self,
        alert_recorder: DataRecorder,
        ts_ms: int,
        result: dict[str, Any],
        fault_result: dict[str, Any],
        risk_result: dict[str, Any],
    ) -> None:
        anomaly_level = str(result["level"])
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

        alert_context_csv = self._write_alert_context_csv(
            ts_ms=ts_ms,
            level=level,
            fault_type=str(fault_result.get("fault_type", "unknown")),
        )
        preview_alert = {
            "elevator_id": self.args.elevator_id,
            "ts_ms": ts_ms,
            "level": level,
            "anomaly_level": anomaly_level,
            "predictive_only": int(predictive_only),
            "fault_type": fault_result.get("fault_type", "unknown"),
            "fault_confidence": fault_result.get("fault_confidence", 0.0),
            "risk_score": risk_result.get("risk_score", 0.0),
            "risk_level_now": risk_result.get("risk_level_now", "normal"),
            "risk_24h": risk_result.get("risk_24h", 0.0),
            "risk_level_24h": risk_result.get("risk_level_24h", "normal"),
            "degradation_slope": risk_result.get("degradation_slope", 0.0),
            "alert_context_csv": alert_context_csv,
        }
        dify_result = self._dispatch_dify_alert(
            ts_ms=ts_ms,
            level=level,
            alert_row=preview_alert,
        )
        alert = build_alert_record(
            elevator_id=self.args.elevator_id,
            ts_ms=ts_ms,
            level=level,
            anomaly_level=anomaly_level,
            predictive_only=predictive_only,
            anomaly_result=result,
            fault_result=fault_result,
            risk_result=risk_result,
            alert_context_csv=alert_context_csv,
            dify_result=dify_result,
            records_written=self.records_written,
            skipped_total=self.skipped_total,
        )

        alert_recorder.write(alert)
        self._last_alert_emit_ms = ts_ms
        self.alerts_emitted += 1
        self.last_fault_type = str(fault_result.get("fault_type", "unknown"))
        self.last_fault_confidence = float(fault_result.get("fault_confidence", 0.0))
        self.last_fault_model_pred = str(fault_result.get("fault_model_pred", ""))
        self.last_fault_model_confidence = float(fault_result.get("fault_model_confidence", 0.0))
        self.last_fault_model_top_k = str(fault_result.get("fault_model_top_k", ""))
        self.last_fault_generated_pred = str(fault_result.get("fault_generated_pred", ""))
        self.last_fault_generated_confidence = float(fault_result.get("fault_generated_confidence", 0.0))
        self.last_fault_generated_top_k = str(fault_result.get("fault_generated_top_k", ""))
        self.last_forecast_a_mag = float(fault_result.get("forecast_a_mag", 0.0))
        self.last_forecast_g_mag = float(fault_result.get("forecast_g_mag", 0.0))
        self.last_forecast_t = float(fault_result.get("forecast_t", 0.0))
        self.last_forecast_confidence = float(fault_result.get("forecast_confidence", 0.0))

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
            "baseline_ready": self.detector.baseline_ready,
            "baseline_count": self.detector.baseline_count,
            "stale_repeat": self.detector.stale_repeat,
            "fault_type_enabled": self.args.fault_type_enabled,
            "fault_model_loaded": self.fault_model_runner is not None,
            "fault_model_path": self.args.fault_model_path,
            "fault_fusion_mode": self.args.fault_fusion_mode,
            "last_fault_type": self.last_fault_type,
            "last_fault_confidence": self.last_fault_confidence,
            "last_fault_model_pred": self.last_fault_model_pred,
            "last_fault_model_confidence": self.last_fault_model_confidence,
            "last_fault_model_top_k": self.last_fault_model_top_k,
            "generated_algo_path": self.args.generated_algo_path,
            "generated_algo_loaded": self.generated_algo_runner is not None,
            "last_fault_generated_pred": self.last_fault_generated_pred,
            "last_fault_generated_confidence": self.last_fault_generated_confidence,
            "last_fault_generated_top_k": self.last_fault_generated_top_k,
            "last_forecast_a_mag": self.last_forecast_a_mag,
            "last_forecast_g_mag": self.last_forecast_g_mag,
            "last_forecast_t": self.last_forecast_t,
            "last_forecast_confidence": self.last_forecast_confidence,
            "risk_enabled": self.args.risk_enabled,
            "risk_model_loaded": self.risk_model_runner is not None,
            "risk_model_path": self.args.risk_model_path,
            "last_risk_score": self.last_risk_score,
            "last_risk_level_now": self.last_risk_level_now,
            "last_risk_24h": self.last_risk_24h,
            "last_risk_level_24h": self.last_risk_level_24h,
            "last_degradation_slope": self.last_degradation_slope,
            "last_risk_model_score": self.last_risk_model_score,
            "profile_path": self.profile_path,
            "profile_loaded": self.profile_loaded,
            "profile_load_error": self.profile_load_error,
            "profile_save_count": self.profile_save_count,
            "alert_context_enabled": self.args.alert_context_enabled,
            "alert_context_dir": self.args.alert_context_dir,
            "alert_context_pre_seconds": self.args.alert_context_pre_seconds,
            "last_alert_context_path": self.last_alert_context_path,
            "dify_enabled": self.args.dify_enabled,
            "dify_client_ready": self.dify_client is not None,
            "dify_base_url": self.args.dify_base_url,
            "dify_min_level": self.args.dify_min_level,
            "dify_dispatch_count": self.dify_dispatch_count,
            "last_dify_status": self.last_dify_status,
            "last_dify_workflow_run_id": self.last_dify_workflow_run_id,
            "last_dify_task_id": self.last_dify_task_id,
            "last_dify_error": self.last_dify_error,
            "updated_at_ms": now_ts_ms(),
        }

        out_path = Path(self.args.health_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(out_path)

        self._last_health_write = now_mono

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
        self._ensure_csv_schema(self.args.output_rail_wear_alert, RAIL_WEAR_FIELDS)

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
        ) as alert_recorder, DataRecorder(
            self.args.output_rail_wear_alert,
            file_format="csv",
            fieldnames=RAIL_WEAR_FIELDS,
            flush=True,
        ) as rail_wear_recorder:
            next_t = time.perf_counter()

            while not self.stop_event.is_set():
                if self.args.run_seconds is not None and time.time() - started_wall >= self.args.run_seconds:
                    self.logger.info("run_seconds reached: %.2f", self.args.run_seconds)
                    break

                if self.device is None:
                    if not self._connect_device():
                        self._write_health(force=True)
                        time.sleep(max(0.5, self.args.reconnect_backoff_s))
                        continue

                try:
                    self.total_loops += 1
                    ts_ms = now_ts_ms()

                    record, data_ts_ms, is_new, accept = self._build_data_record(ts_ms)

                    if data_ts_ms is not None:
                        self.last_data_ts_ms = data_ts_ms
                        self.last_data_monotonic = time.monotonic()

                    if accept and is_new:
                        data_recorder.write(record)
                        self.alert_context_rows.append(dict(record))
                        self.records_written += 1
                        self.last_written_data_ts = data_ts_ms

                        features = self.window_buffer.update(ts_ms, record)
                        result = self.detector.update(record)
                        fault_rule = self.fault_engine.update(record, result)
                        rail_wear_row = self.fault_engine.get_rail_wear_export_row()
                        if rail_wear_row:
                            rail_wear_recorder.write(rail_wear_row)
                        fault_model_pred = self._predict_fault_model(features)
                        generated_pred = self._predict_generated_algo(features)
                        forecast_result = self.feature_forecaster.update(ts_ms, features)
                        fault_result = self._merge_fault_result(
                            fault_rule,
                            fault_model_pred,
                            generated_pred,
                            result,
                            features,
                            forecast_result,
                        )
                        self.last_fault_generated_pred = str(fault_result.get("fault_generated_pred", ""))
                        self.last_fault_generated_confidence = float(fault_result.get("fault_generated_confidence", 0.0))
                        self.last_fault_generated_top_k = str(fault_result.get("fault_generated_top_k", ""))
                        self.last_forecast_a_mag = float(fault_result.get("forecast_a_mag", 0.0))
                        self.last_forecast_g_mag = float(fault_result.get("forecast_g_mag", 0.0))
                        self.last_forecast_t = float(fault_result.get("forecast_t", 0.0))
                        self.last_forecast_confidence = float(fault_result.get("forecast_confidence", 0.0))

                        risk_model_prob = self._predict_risk_model_probability(features)
                        self.last_risk_model_score = float(risk_model_prob or 0.0)
                        risk_result = self.risk_predictor.update(
                            ts_ms,
                            record,
                            result,
                            fault_result,
                            model_probability=risk_model_prob,
                        )
                        risk_result["risk_model_score"] = float(risk_model_prob or 0.0)
                        self.last_risk_score = float(risk_result.get("risk_score", 0.0))
                        self.last_risk_level_now = str(risk_result.get("risk_level_now", "normal"))
                        self.last_risk_24h = float(risk_result.get("risk_24h", 0.0))
                        self.last_risk_level_24h = str(risk_result.get("risk_level_24h", "normal"))
                        self.last_degradation_slope = float(risk_result.get("degradation_slope", 0.0))
                        self._maybe_emit_alert(alert_recorder, ts_ms, result, fault_result, risk_result)
                        self._save_profile()

                        if self.args.print_every_n > 0 and self.records_written % self.args.print_every_n == 0:
                            self.logger.info(
                                "record #%s Ax=%s Ay=%s Az=%s Gx=%s Gy=%s Gz=%s t=%s",
                                self.records_written,
                                record.get("Ax"),
                                record.get("Ay"),
                                record.get("Az"),
                                record.get("Gx"),
                                record.get("Gy"),
                                record.get("Gz"),
                                record.get("t"),
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

        self.status = "stopped"
        self._save_profile(force=True)
        self._write_health(force=True)

        if self.device is not None:
            self._disconnect_device("shutdown")

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
