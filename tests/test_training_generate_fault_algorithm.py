import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elevator_monitor.training import generate_fault_algorithm


class TestGenerateFaultAlgorithm(unittest.TestCase):
    def test_generate_algorithm(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "dataset.csv"
            output = Path(tmp) / "generated_algo.json"
            with dataset.open("w", encoding="utf-8", newline="") as fp:
                writer = csv.DictWriter(
                    fp,
                    fieldnames=[
                        "elevator_id",
                        "window_start_ms",
                        "target_fault_type",
                        "A_mag_mean",
                        "G_mag_mean",
                        "T_mean",
                    ],
                )
                writer.writeheader()
                # normal
                writer.writerow({"elevator_id": "e1", "window_start_ms": 1, "target_fault_type": "normal", "A_mag_mean": 1.0, "G_mag_mean": 1.1, "T_mean": 25.0})
                writer.writerow({"elevator_id": "e1", "window_start_ms": 2, "target_fault_type": "normal", "A_mag_mean": 1.1, "G_mag_mean": 1.0, "T_mean": 24.8})
                writer.writerow({"elevator_id": "e1", "window_start_ms": 3, "target_fault_type": "normal", "A_mag_mean": 0.9, "G_mag_mean": 1.2, "T_mean": 25.2})
                # fault class
                writer.writerow({"elevator_id": "e1", "window_start_ms": 4, "target_fault_type": "door_stuck", "A_mag_mean": 4.0, "G_mag_mean": 4.2, "T_mean": 34.0})
                writer.writerow({"elevator_id": "e1", "window_start_ms": 5, "target_fault_type": "door_stuck", "A_mag_mean": 4.3, "G_mag_mean": 4.5, "T_mean": 34.5})
                writer.writerow({"elevator_id": "e1", "window_start_ms": 6, "target_fault_type": "door_stuck", "A_mag_mean": 3.8, "G_mag_mean": 4.1, "T_mean": 33.8})

            argv = [
                "generate_fault_algorithm",
                "--dataset-csv",
                str(dataset),
                "--output-json",
                str(output),
                "--min-class-samples",
                "3",
            ]
            with patch("sys.argv", argv):
                rc = generate_fault_algorithm.main()
            self.assertEqual(rc, 0)
            self.assertTrue(output.exists())

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["algorithm_type"], "generated_fault_algorithm_v1")
            labels = [item["label"] for item in payload["classes"]]
            self.assertEqual(labels, ["door_stuck"])


if __name__ == "__main__":
    unittest.main()
