import json
import os
import tempfile
import unittest
from base64 import b64encode
from gzip import compress
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from elevator_monitor.api_service import app


class TestAPIService(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_meta_includes_diagnosis_report_capability(self):
        response = self.client.get("/api/v1/meta")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("diagnosis_report", payload.get("capabilities", []))
        self.assertIn("waveform_plot", payload.get("capabilities", []))
        self.assertIn("batch_diagnosis", payload.get("capabilities", []))
        self.assertIn("latest_status", payload.get("capabilities", []))

    def test_rule_engine_accepts_inline_rows(self):
        rows = []
        ts0 = 1_000_000
        for i in range(24):
            rows.append(
                {
                    "ts_ms": ts0 + i * 1000,
                    "Ax": 0.01,
                    "Ay": 0.02,
                    "Az": -0.98,
                    "Gx": 0.1,
                    "Gy": 0.2,
                    "Gz": 0.3,
                    "t": 25.0,
                    "is_new_frame": 1,
                }
            )

        response = self.client.post("/api/v1/diagnostics/rule-engine", json={"rows": rows})
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertIn("top_fault", payload)
        self.assertIn("rope_primary", payload)
        self.assertIn("auxiliary_results", payload)
        self.assertIn("results", payload)
        self.assertEqual(payload["input"], "inline_rows")

    def test_maintenance_package_accepts_inline_payloads(self):
        response = self.client.post(
            "/api/v1/workflows/maintenance-package",
            json={
                "site_name": "Tower B",
                "alert_rows": [
                    {
                        "elevator_id": "elevator-002",
                        "ts_ms": "1000",
                        "level": "warning",
                        "predictive_only": "1",
                        "fault_type": "rail_wear",
                        "fault_confidence": "0.71",
                        "risk_score": "0.55",
                        "risk_level_now": "watch",
                        "risk_24h": "0.70",
                        "risk_level_24h": "high",
                    }
                ],
                "health_payload": {
                    "status": "running",
                    "elevator_id": "elevator-002",
                    "connected": True,
                },
                "manifest_payload": {
                    "models": [
                        {"id": "manifest-1", "name": "fault_model_latest"},
                    ]
                },
            },
        )
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertEqual(payload["elevator_id"], "elevator-002")
        self.assertEqual(payload["priority"], "P2")
        self.assertEqual(payload["dify_inputs"]["ticket_priority"], "P2")
        self.assertIn("manifest-1", payload["dify_inputs"]["model_ids"])

    def test_waveform_plot_accepts_inline_rows(self):
        rows = []
        ts0 = 1_000_000
        for i in range(24):
            rows.append(
                {
                    "ts_ms": ts0 + i * 1000,
                    "Ax": 0.02 + 0.001 * i,
                    "Ay": 0.03,
                    "Az": -0.98 + 0.002 * i,
                    "Gx": 0.1,
                    "Gy": 0.2,
                    "Gz": 0.3,
                    "is_new_frame": 1,
                }
            )

        response = self.client.post("/api/v1/diagnostics/waveform-plot", json={"rows": rows, "max_points": 64})
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertIn("plots", payload)
        self.assertIn("acceleration", payload["plots"])
        self.assertIn("gyroscope", payload["plots"])
        self.assertIn("acceleration_magnitude", payload["plots"])
        self.assertIn("echarts", payload)
        self.assertIn("acceleration", payload["echarts"])
        self.assertIn("```echarts", payload["markdown_echarts"])
        self.assertIn("data:image/svg+xml;base64,", payload["plots"]["acceleration"]["data_uri"])
        self.assertIn("## 波形图", payload["markdown"])

    def test_latest_status_reads_saved_payload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            status_path = Path(tmp_dir) / "latest_status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "workflow_type": "scheduled_batch_diagnosis_v1",
                        "status": "watch_only",
                        "preferred_issue": {"fault_type": "rope_looseness", "score": 58.0},
                        "risk": {"risk_level_now": "watch"},
                        "report_markdown_draft": "# 测试报告\n\n## 1. 一句话结论\n存在可疑异常。\n",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            response = self.client.get(
                "/api/v1/diagnostics/latest-status",
                params={"latest_json": str(status_path)},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "watch_only")
        self.assertEqual(payload["preferred_issue"]["fault_type"], "rope_looseness")
        self.assertEqual(payload["risk"]["risk_level_now"], "watch")
        self.assertIn("report_markdown_draft", payload)
        self.assertIn("一句话结论", payload["report_markdown_draft"])
        self.assertEqual(payload["latest_json"], str(status_path.resolve()))

    def test_latest_status_can_resolve_elevator_id_from_root(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            status_path = root / "elevator_002" / "latest_status.json"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(
                json.dumps(
                    {
                        "workflow_type": "scheduled_batch_diagnosis_v1",
                        "status": "watch_only",
                        "primary_issue": {"fault_type": "rope_tension_abnormal", "score": 58.0},
                        "preferred_issue": {"fault_type": "rope_tension_abnormal", "score": 58.0},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            response = self.client.get(
                "/api/v1/diagnostics/latest-status",
                params={"elevator_id": "002", "latest_root": str(root)},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "watch_only")
        self.assertEqual(payload["primary_issue"]["fault_type"], "rope_tension_abnormal")
        self.assertEqual(payload["latest_json"], str(status_path.resolve()))
        self.assertEqual(payload["requested_elevator_id"], "002")

    def test_latest_status_can_include_waveforms_from_latest_csv(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "latest_capture.csv"
            rows = ["ts_ms,Ax,Ay,Az,Gx,Gy,Gz,is_new_frame"]
            for i in range(24):
                rows.append(f"{1_000_000 + i * 1000},0.01,0.02,{-0.98 + i * 0.001},0.1,0.2,0.3,1")
            csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

            status_path = Path(tmp_dir) / "latest_status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "workflow_type": "scheduled_batch_diagnosis_v1",
                        "status": "watch_only",
                        "latest_file": str(csv_path),
                        "latest_file_name": csv_path.name,
                        "preferred_issue": {"fault_type": "rope_looseness", "score": 58.0},
                        "risk": {"risk_level_now": "watch"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            response = self.client.get(
                "/api/v1/diagnostics/latest-status",
                params={"latest_json": str(status_path), "include_waveforms": True},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("waveform_payload", payload)
        self.assertIn("plots", payload["waveform_payload"])
        self.assertIn("markdown_echarts", payload["waveform_payload"])
        self.assertIn("```echarts", payload["waveform_payload"]["markdown_echarts"])

    def test_batch_run_endpoint_returns_payload(self):
        fake_payload = {
            "workflow_type": "scheduled_batch_diagnosis_v1",
            "status": "candidate_faults",
            "preferred_issue": {"fault_type": "rubber_hardening", "score": 74.5},
            "risk": {"risk_level_now": "high", "risk_score": 0.78},
        }
        with patch("elevator_monitor.api.routers.diagnostics.run_batch_diagnosis", return_value=fake_payload) as mocked:
            response = self.client.post(
                "/api/v1/diagnostics/batch-run",
                json={
                    "input_dir": "/tmp/demo",
                    "max_files": 6,
                    "baseline_dir": "/tmp/baseline",
                    "write_outputs": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "candidate_faults")
        self.assertEqual(payload["preferred_issue"]["fault_type"], "rubber_hardening")
        mocked.assert_called_once()

    def test_diagnosis_report_builds_dify_inputs(self):
        rows = []
        ts0 = 1_000_000
        for i in range(24):
            rows.append(
                {
                    "ts_ms": ts0 + i * 1000,
                    "Ax": 0.02,
                    "Ay": 0.02,
                    "Az": -0.97,
                    "Gx": 0.1,
                    "Gy": 0.2,
                    "Gz": 0.3,
                    "t": 26.0,
                    "is_new_frame": 1,
                }
            )

        response = self.client.post(
            "/api/v1/workflows/diagnosis-report",
            json={
                "site_name": "Tower C",
                "rows": rows,
                "maintenance_package": {
                    "site_name": "Tower C",
                    "elevator_id": "elevator-003",
                    "priority": "P2",
                    "summary": "demo summary",
                    "recommended_actions": ["action1", "action2"],
                    "suggested_parts": ["part1"],
                    "risk": {
                        "risk_score": 0.52,
                        "risk_level_now": "watch",
                        "risk_24h": 0.68,
                        "risk_level_24h": "high",
                    },
                },
                "include_waveforms": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("dify_report_inputs", payload)
        self.assertIn("dify_prompt_template", payload)
        self.assertIn("report_markdown_draft", payload)
        self.assertEqual(payload["dify_report_inputs"]["site_name"], "Tower C")
        self.assertIn("waveform_payload", payload)
        self.assertIn("plots", payload["waveform_payload"])
        self.assertIn("markdown_echarts", payload["waveform_payload"])

    def test_diagnosis_report_keeps_preferred_issue_empty_when_status_is_normal(self):
        diagnosis_result = {
            "summary": {"n_raw": 24, "n_effective": 24, "fs_hz": 1.0},
            "screening": {"status": "normal"},
            "top_fault": {"fault_type": "rubber_hardening", "score": 28.0, "level": "normal"},
            "top_candidate": {},
            "watch_faults": [],
        }
        response = self.client.post(
            "/api/v1/workflows/diagnosis-report",
            json={
                "site_name": "Tower C",
                "diagnosis_result": diagnosis_result,
                "maintenance_package": {
                    "site_name": "Tower C",
                    "elevator_id": "elevator-003",
                    "priority": "P4",
                    "summary": "demo summary",
                    "recommended_actions": [],
                    "suggested_parts": [],
                    "risk": {
                        "risk_score": 0.10,
                        "risk_level_now": "normal",
                        "risk_24h": 0.12,
                        "risk_level_24h": "normal",
                    },
                },
                "include_waveforms": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["preferred_issue"]["fault_type"], "unknown")
        self.assertEqual(payload["preferred_issue"]["score"], 0.0)
        self.assertEqual(payload["dify_report_inputs"]["preferred_fault_type"], "unknown")
        self.assertIn("当前最值得关注的问题：暂无明确故障类型", payload["report_markdown_draft"])
        self.assertIn("系统故障标签：unknown", payload["report_markdown_draft"])
        self.assertNotIn("当前最值得关注的问题：减振橡胶硬化", payload["report_markdown_draft"])

    def test_dify_workflow_online_status_includes_detection_date(self):
        workflow_path = Path("docs/dify_workflows/elevator_diagnosis_report_with_waveform_v2.yml")
        workflow_text = workflow_path.read_text(encoding="utf-8")

        self.assertIn("generated_at_ms", workflow_text)
        self.assertIn("检测日期：", workflow_text)
        self.assertIn("固定输出五行", workflow_text)
        self.assertIn("include_waveforms=true", workflow_text)
        self.assertIn("## 波形图", workflow_text)
        self.assertIn("### 加速度三轴", workflow_text)
        self.assertIn("### 角速度三轴", workflow_text)
        self.assertIn("### 加速度合成幅值", workflow_text)
        self.assertIn("status_parse.acc_chart", workflow_text)
        self.assertIn("status_parse.gyro_chart", workflow_text)
        self.assertIn("status_parse.mag_chart", workflow_text)
        self.assertIn("answer: |", workflow_text)
        self.assertIn("```echarts", workflow_text)
        self.assertIn("{{#status_parse.acc_chart#}}", workflow_text)

    def test_ingest_heartbeat_updates_edge_latest_status(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {"ELEVATOR_CLOUD_STORE_DIR": tmp_dir}):
            response = self.client.post(
                "/api/v1/ingest/heartbeat",
                json={
                    "device_id": "edge-001",
                    "elevator_id": "elevator-100",
                    "site_name": "Tower D",
                    "health_payload": {
                        "status": "running",
                        "connected": True,
                        "last_risk_score": 0.22,
                        "last_risk_level_now": "normal",
                        "last_risk_24h": 0.41,
                        "last_risk_level_24h": "watch",
                        "updated_at_ms": 123456,
                    },
                },
            )
            self.assertEqual(response.status_code, 200)

            latest = self.client.get("/api/v1/elevators/elevator-100/latest-status")
            self.assertEqual(latest.status_code, 200)
            payload = latest.json()
            self.assertEqual(payload["elevator_id"], "elevator-100")
            self.assertEqual(payload["site_name"], "Tower D")
            self.assertEqual(payload["health_payload"]["status"], "running")
            self.assertEqual(payload["risk"]["risk_level_24h"], "watch")

    def test_ingest_alert_context_and_report_by_event(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {"ELEVATOR_CLOUD_STORE_DIR": tmp_dir}):
            alert_payload = {
                "elevator_id": "elevator-101",
                "ts_ms": 1_000_000,
                "level": "warning",
                "predictive_only": 0,
                "fault_type": "rope_looseness",
                "fault_confidence": 0.78,
                "risk_score": 0.61,
                "risk_level_now": "watch",
                "risk_24h": 0.83,
                "risk_level_24h": "high",
                "degradation_slope": 0.0032,
            }
            event_id = "elevator-101-1000000-rope"
            response = self.client.post(
                "/api/v1/ingest/alert",
                json={
                    "event_id": event_id,
                    "device_id": "edge-101",
                    "elevator_id": "elevator-101",
                    "site_name": "Tower E",
                    "ts_ms": 1_000_000,
                    "alert_payload": alert_payload,
                    "health_payload": {
                        "status": "running",
                        "connected": True,
                        "baseline_ready": True,
                        "last_fault_type": "rope_looseness",
                        "last_fault_confidence": 0.78,
                        "last_risk_score": 0.61,
                        "last_risk_level_now": "watch",
                        "last_risk_24h": 0.83,
                        "last_risk_level_24h": "high",
                    },
                },
            )
            self.assertEqual(response.status_code, 200)

            csv_text = "\n".join(
                [
                    "ts_ms,Ax,Ay,Az,Gx,Gy,Gz,is_new_frame",
                    "1000000,0.01,0.02,-0.98,0.1,0.2,0.3,1",
                    "1001000,0.02,0.02,-0.97,0.1,0.2,0.3,1",
                    "1002000,0.03,0.02,-0.96,0.1,0.2,0.3,1",
                    "1003000,0.04,0.02,-0.95,0.1,0.2,0.3,1",
                    "1004000,0.05,0.02,-0.94,0.1,0.2,0.3,1",
                    "1005000,0.04,0.02,-0.95,0.1,0.2,0.3,1",
                    "1006000,0.03,0.02,-0.96,0.1,0.2,0.3,1",
                    "1007000,0.02,0.02,-0.97,0.1,0.2,0.3,1",
                    "1008000,0.01,0.02,-0.98,0.1,0.2,0.3,1",
                ]
            )
            context_response = self.client.post(
                "/api/v1/ingest/context",
                json={
                    "event_id": event_id,
                    "device_id": "edge-101",
                    "elevator_id": "elevator-101",
                    "site_name": "Tower E",
                    "ts_ms": 1_000_000,
                    "file_name": "alert_context.csv.gz",
                    "content_type": "text/csv",
                    "compression": "gzip",
                    "content_b64": b64encode(compress(csv_text.encode("utf-8"))).decode("ascii"),
                },
            )
            self.assertEqual(context_response.status_code, 200)

            alerts = self.client.get("/api/v1/elevators/elevator-101/alerts")
            self.assertEqual(alerts.status_code, 200)
            self.assertEqual(alerts.json()["count"], 1)

            detail = self.client.get(f"/api/v1/alerts/{event_id}")
            self.assertEqual(detail.status_code, 200)
            detail_payload = detail.json()
            self.assertEqual(detail_payload["alert_payload"]["fault_type"], "rope_looseness")
            self.assertTrue(detail_payload["context"]["stored_path"].endswith(".gz"))

            report = self.client.post(
                "/api/v1/workflows/diagnosis-report-by-event",
                json={"event_id": event_id, "include_waveforms": True},
            )
            self.assertEqual(report.status_code, 200)
            report_payload = report.json()
            self.assertEqual(report_payload["event_id"], event_id)
            self.assertIn("dify_report_inputs", report_payload)
            self.assertIn("report_markdown_draft", report_payload)
            self.assertIn("波形图", report_payload["report_markdown_draft"])
            self.assertIn("waveform_payload", report_payload)


if __name__ == "__main__":
    unittest.main()
