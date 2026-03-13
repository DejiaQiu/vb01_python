import json
import tempfile
import unittest
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
        self.assertEqual(payload["latest_json"], str(status_path))

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
        self.assertIn("## 1. 一句话结论", payload["report_markdown_draft"])
        self.assertIn("## 2. 给非专业人员的解释", payload["report_markdown_draft"])
        self.assertIn("## 3. 建议怎么做", payload["report_markdown_draft"])
        self.assertIn("本报告属于振动筛查结果", payload["report_markdown_draft"])
        self.assertNotIn("## Executive Summary", payload["report_markdown_draft"])
        self.assertIn("## 波形图", payload["report_markdown_draft"])

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


if __name__ == "__main__":
    unittest.main()
