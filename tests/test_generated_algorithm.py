import json
import tempfile
import unittest
from pathlib import Path

from elevator_monitor.generated_algorithm import GeneratedFaultAlgorithmRunner, OnlineFeatureForecaster


class TestGeneratedAlgorithm(unittest.TestCase):
    def test_runner_predict(self):
        payload = {
            "version": 1,
            "algorithm_type": "generated_fault_algorithm_v1",
            "feature_names": ["A_mag_mean", "G_mag_mean", "T_mean"],
            "normal_stats": {
                "mean": {"A_mag_mean": 1.0, "G_mag_mean": 1.0, "T_mean": 25.0},
                "std": {"A_mag_mean": 0.5, "G_mag_mean": 0.5, "T_mean": 2.0},
            },
            "classes": [
                {
                    "label": "door_stuck",
                    "sample_count": 5,
                    "prototype": {"A_mag_mean": 2.0, "G_mag_mean": 2.5, "T_mean": 30.0},
                    "weights": {"A_mag_mean": 1.5, "G_mag_mean": 1.2, "T_mean": 0.8},
                    "min_score": 0.3,
                },
                {
                    "label": "brake_abnormal",
                    "sample_count": 5,
                    "prototype": {"A_mag_mean": 5.0, "G_mag_mean": 5.5, "T_mean": 38.0},
                    "weights": {"A_mag_mean": 1.8, "G_mag_mean": 1.5, "T_mean": 1.0},
                    "min_score": 0.3,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generated_algo.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            runner = GeneratedFaultAlgorithmRunner(str(path))
            pred = runner.predict({"A_mag_mean": 4.9, "G_mag_mean": 5.2, "T_mean": 37.0}, top_k=2)
            self.assertIsNotNone(pred)
            assert pred is not None
            self.assertEqual(pred.label, "brake_abnormal")
            self.assertGreater(pred.confidence, 0.5)

    def test_forecaster_predicts_uptrend(self):
        forecaster = OnlineFeatureForecaster(horizon_s=30.0, min_points=5, max_points=50)
        result = None
        base_ts = 1_770_000_000_000
        for i in range(8):
            result = forecaster.update(
                base_ts + i * 1000,
                {"A_mag_mean": 1.0 + 0.1 * i, "G_mag_mean": 2.0 + 0.05 * i, "T_mean": 25.0 + 0.02 * i},
            )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertGreater(result.values["A_mag_mean"], 1.0 + 0.1 * 7)
        self.assertGreater(result.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
