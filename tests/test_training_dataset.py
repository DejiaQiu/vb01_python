import csv
import tempfile
import unittest
from pathlib import Path

from elevator_monitor.training.dataset_builder import (
    build_window_samples,
    discover_data_files,
    load_fault_events,
)


class TestTrainingDatasetBuilder(unittest.TestCase):
    def test_build_window_samples_with_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_path = tmp_path / "elevator_rt.csv"
            label_path = tmp_path / "labels.csv"

            with data_path.open("w", encoding="utf-8", newline="") as fp:
                writer = csv.DictWriter(
                    fp,
                    fieldnames=["elevator_id", "ts_ms", "Ax", "Ay", "Az", "Gx", "Gy", "Gz", "t"],
                )
                writer.writeheader()
                ts0 = 1_000_000
                for i in range(120):
                    writer.writerow(
                        {
                            "elevator_id": "elevator-001",
                            "ts_ms": ts0 + i * 1000,
                            "Ax": 0.01,
                            "Ay": 0.02,
                            "Az": -0.98,
                            "Gx": 0.1,
                            "Gy": 0.2,
                            "Gz": 0.3,
                            "t": 25.0,
                        }
                    )

            with label_path.open("w", encoding="utf-8", newline="") as fp:
                writer = csv.DictWriter(
                    fp,
                    fieldnames=["elevator_id", "start_ts_ms", "end_ts_ms", "fault_type", "confirmed"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "elevator_id": "elevator-001",
                        "start_ts_ms": 1_040_000,
                        "end_ts_ms": 1_055_000,
                        "fault_type": "door_stuck",
                        "confirmed": "1",
                    }
                )

            files = discover_data_files([str(data_path)])
            timelines = load_fault_events(str(label_path))
            samples = build_window_samples(
                data_files=files,
                event_timelines=timelines,
                window_s=10.0,
                step_s=5.0,
                horizon_s=30.0,
                min_samples=5,
            )

            self.assertGreater(len(samples), 5)
            self.assertIn("door_stuck", {s.target_fault_type for s in samples})
            self.assertIn(1, {s.target_fault_24h for s in samples})


if __name__ == "__main__":
    unittest.main()
