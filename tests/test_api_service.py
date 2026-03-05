import unittest

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
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("dify_report_inputs", payload)
        self.assertIn("dify_prompt_template", payload)
        self.assertIn("report_markdown_draft", payload)
        self.assertEqual(payload["dify_report_inputs"]["site_name"], "Tower C")


if __name__ == "__main__":
    unittest.main()
