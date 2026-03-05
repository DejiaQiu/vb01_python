import csv
import json
import tempfile
import unittest
from pathlib import Path

from elevator_monitor.maintenance_workflow import (
    build_maintenance_package,
    load_optional_json,
    load_recent_alerts,
    render_markdown,
)


class TestMaintenanceWorkflow(unittest.TestCase):
    def test_build_maintenance_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            alert_csv = tmp_path / "alerts.csv"
            health_json = tmp_path / "health.json"
            manifest_json = tmp_path / "manifest.json"

            with alert_csv.open("w", encoding="utf-8", newline="") as fp:
                writer = csv.DictWriter(
                    fp,
                    fieldnames=[
                        "elevator_id",
                        "ts_ms",
                        "level",
                        "predictive_only",
                        "fault_type",
                        "fault_confidence",
                        "risk_score",
                        "risk_level_now",
                        "risk_24h",
                        "risk_level_24h",
                        "degradation_slope",
                        "alert_context_csv",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "elevator_id": "elevator-001",
                        "ts_ms": "1000",
                        "level": "warning",
                        "predictive_only": "1",
                        "fault_type": "bearing_wear",
                        "fault_confidence": "0.82",
                        "risk_score": "0.58",
                        "risk_level_now": "watch",
                        "risk_24h": "0.88",
                        "risk_level_24h": "critical",
                        "degradation_slope": "0.04",
                        "alert_context_csv": "/tmp/context.csv",
                    }
                )

            health_json.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "elevator_id": "elevator-001",
                        "connected": True,
                        "alerts_emitted": 3,
                        "records_written": 1200,
                        "baseline_ready": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manifest_json.write_text(
                json.dumps(
                    {
                        "models": [
                            {"id": "fault-model-123", "name": "fault_model_latest"},
                            {"id": "risk-model-456", "name": "risk_model_latest"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            package = build_maintenance_package(
                alert_rows=load_recent_alerts(str(alert_csv)),
                health_payload=load_optional_json(str(health_json)),
                site_name="Tower A",
                alert_csv_path=str(alert_csv),
                health_json_path=str(health_json),
                manifest_payload=load_optional_json(str(manifest_json)),
                manifest_path=str(manifest_json),
            )
            markdown = render_markdown(package)

            self.assertEqual(package["priority"], "P1")
            self.assertEqual(package["maintenance_mode"], "urgent_inspection")
            self.assertEqual(package["current_fault_type"], "bearing_wear")
            self.assertEqual(package["dify_inputs"]["ticket_priority"], "P1")
            self.assertIn("fault-model-123", package["dify_inputs"]["model_ids"])
            self.assertIn("bearing_wear", markdown)


if __name__ == "__main__":
    unittest.main()
