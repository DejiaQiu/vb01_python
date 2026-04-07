import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from elevator_monitor.api_service import app
from elevator_monitor.control_plane import get_control_store
from elevator_monitor.realtime_monitor import RealtimeMonitor, build_arg_parser


class TestControlPlaneStore(unittest.TestCase):
    def test_issue_and_acknowledge_command(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(os.environ, {"MONITOR_CONTROL_DIR": str(Path(tmp_dir) / "control")}, clear=False):
                store = get_control_store()
                state = store.issue_command("elevator-007", "start_baseline")
                self.assertEqual(state["pending_command"]["action"], "start_baseline")
                self.assertEqual(state["lifecycle_stage"], "commissioning")

                store.acknowledge_command(
                    "elevator-007",
                    state["pending_command"],
                    message="baseline building started",
                    state_patch={"lifecycle_stage": "baseline_building"},
                )
                final_state = store.read_state("elevator-007")
                self.assertEqual(final_state["pending_command"], {})
                self.assertEqual(final_state["last_applied_command"]["action"], "start_baseline")
                self.assertEqual(final_state["lifecycle_stage"], "baseline_building")


class TestRealtimeMonitorControlFlow(unittest.TestCase):
    def _build_args(self, root: Path):
        args = build_arg_parser().parse_args([])
        args.elevator_id = "elevator-ctrl"
        args.log_file = str(root / "logs" / "realtime.log")
        args.output_data = str(root / "data" / "rt.csv")
        args.output_alert = str(root / "data" / "alerts.csv")
        args.alert_context_dir = str(root / "data" / "alert_context")
        args.health_path = str(root / "data" / "monitor_health.json")
        args.profile_path = str(root / "data" / "profiles" / "{elevator_id}.json")
        args.edge_sync_enabled = False
        return args

    def test_monitor_applies_control_commands(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            with patch.dict(os.environ, {"MONITOR_CONTROL_DIR": str(root / "control")}, clear=False):
                monitor = RealtimeMonitor(self._build_args(root))
                self.assertEqual(monitor.lifecycle_stage, "commissioning")
                self.assertFalse(monitor.baseline_learning_enabled)
                self.assertFalse(monitor.alerts_enabled)

                store = get_control_store()
                store.issue_command("elevator-ctrl", "start_baseline")
                monitor._poll_control_command()
                self.assertEqual(monitor.lifecycle_stage, "baseline_building")
                self.assertTrue(monitor.baseline_learning_enabled)
                self.assertFalse(monitor.alerts_enabled)

                for _ in range(monitor.args.diagnosis_baseline_min_windows):
                    monitor.diagnosis_engine._healthy_feature_rows.append({"n": 64})
                monitor._maybe_promote_to_monitoring()
                self.assertEqual(monitor.lifecycle_stage, "monitoring")
                self.assertTrue(monitor.alerts_enabled)

                store.issue_command("elevator-ctrl", "freeze_baseline")
                monitor._poll_control_command()
                self.assertTrue(monitor.baseline_frozen)


class TestControlPanelAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_control_endpoints_and_panel(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            control_dir = root / "control"
            cloud_dir = root / "cloud_ingest"
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch.dict(
                    os.environ,
                    {
                        "MONITOR_CONTROL_DIR": str(control_dir),
                        "ELEVATOR_CLOUD_STORE_DIR": str(cloud_dir),
                    },
                    clear=False,
                ):
                    store = get_control_store()
                    store.save_state(
                        "elevator-101",
                        {
                            "lifecycle_stage": "monitoring",
                            "baseline_ready": True,
                            "baseline_count": 12,
                            "alerts_enabled": True,
                            "monitor": {
                                "status": "running",
                                "connected": True,
                                "last_fault_type": "rope_looseness",
                                "last_screening_status": "watch_only",
                                "last_risk_level_24h": "watch",
                                "updated_at_ms": 1_700_000_000_000,
                            },
                        },
                    )

                    response = self.client.get("/api/v1/control/elevators")
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    self.assertEqual(payload["count"], 1)
                    self.assertEqual(payload["items"][0]["elevator_id"], "elevator-101")

                    response = self.client.post("/api/v1/control/elevators/elevator-101/baseline/reset")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["pending_command"]["action"], "reset_baseline")

                    response = self.client.get("/api/v1/control/elevators/elevator-101/state")
                    self.assertEqual(response.status_code, 200)
                    state_payload = response.json()
                    self.assertEqual(state_payload["control_state"]["pending_command"]["action"], "reset_baseline")
                    self.assertIn("report_url", state_payload)

                    diag_root = root / "data" / "diagnosis" / "elevator-101"
                    diag_root.mkdir(parents=True, exist_ok=True)
                    context_path = diag_root / "alert_context.jsonl.gz"
                    context_path.write_bytes(
                        __import__("gzip").compress(
                            "\n".join(
                                json.dumps(row, ensure_ascii=False)
                                for row in [
                                    {"ts_ms": 1000000, "Ax": 0.01, "Ay": 0.02, "Az": -0.98, "Gx": 0.1, "Gy": 0.2, "Gz": 0.3, "is_new_frame": 1},
                                    {"ts_ms": 1000250, "Ax": 0.02, "Ay": 0.03, "Az": -0.97, "Gx": 0.1, "Gy": 0.2, "Gz": 0.3, "is_new_frame": 1},
                                    {"ts_ms": 1000500, "Ax": 0.03, "Ay": 0.03, "Az": -0.96, "Gx": 0.1, "Gy": 0.2, "Gz": 0.3, "is_new_frame": 1},
                                    {"ts_ms": 1000750, "Ax": 0.04, "Ay": 0.03, "Az": -0.95, "Gx": 0.1, "Gy": 0.2, "Gz": 0.3, "is_new_frame": 1},
                                    {"ts_ms": 1001000, "Ax": 0.05, "Ay": 0.03, "Az": -0.94, "Gx": 0.1, "Gy": 0.2, "Gz": 0.3, "is_new_frame": 1},
                                    {"ts_ms": 1001250, "Ax": 0.04, "Ay": 0.03, "Az": -0.95, "Gx": 0.1, "Gy": 0.2, "Gz": 0.3, "is_new_frame": 1},
                                    {"ts_ms": 1001500, "Ax": 0.03, "Ay": 0.03, "Az": -0.96, "Gx": 0.1, "Gy": 0.2, "Gz": 0.3, "is_new_frame": 1},
                                    {"ts_ms": 1001750, "Ax": 0.02, "Ay": 0.03, "Az": -0.97, "Gx": 0.1, "Gy": 0.2, "Gz": 0.3, "is_new_frame": 1},
                                    {"ts_ms": 1002000, "Ax": 0.01, "Ay": 0.03, "Az": -0.98, "Gx": 0.1, "Gy": 0.2, "Gz": 0.3, "is_new_frame": 1},
                                ]
                            ).encode("utf-8")
                        )
                    )
                    (diag_root / "latest_status.json").write_text(
                        json.dumps(
                            {
                                "workflow_type": "scheduled_batch_diagnosis_v1",
                                "status": "watch_only",
                                "elevator_id": "elevator-101",
                                "generated_at_ms": 1_700_000_000_000,
                                "preferred_issue": {"fault_type": "rope_looseness", "score": 58.0, "level": "watch"},
                                "top_candidate": {"fault_type": "rope_looseness", "score": 58.0, "level": "watch"},
                                "risk": {"risk_level_now": "watch", "risk_level_24h": "high", "risk_score": 0.62, "risk_24h": 0.81},
                                "context": {
                                    "file_name": "alert_context.jsonl.gz",
                                    "stored_path": str(context_path),
                                    "local_path": str(context_path),
                                    "content_type": "application/x-ndjson",
                                    "compression": "gzip",
                                },
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

                    response = self.client.get("/api/v1/control/elevators/elevator-101/preview")
                    self.assertEqual(response.status_code, 200)
                    preview_payload = response.json()
                    self.assertIn("report_markdown_draft", preview_payload)
                    self.assertIn("waveform_payload", preview_payload)
                    self.assertIn("plots", preview_payload["waveform_payload"])
                    self.assertEqual(preview_payload["latest_file_name"], "alert_context.jsonl.gz")

                    response = self.client.get("/panel")
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("电梯设备首装与基线控制面板", response.text)
                    self.assertIn("/api/v1/control/elevators/", response.text)
                    self.assertIn("/preview", response.text)
                    self.assertIn("/api/v1/control/elevators", response.text)
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
