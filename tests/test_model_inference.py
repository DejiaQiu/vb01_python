import tempfile
import unittest
from pathlib import Path

from elevator_monitor.model_inference import CentroidModelRunner, OnlineWindowBuffer
from elevator_monitor.training.centroid_model import fit_centroid_classifier


def _row(ts, ax=0.0, ay=0.0, az=-1.0, gx=0.1, gy=0.2, gz=0.3, t=25.0):
    return {
        "ts_ms": ts,
        "ts": f"t{ts}",
        "Ax": ax,
        "Ay": ay,
        "Az": az,
        "Gx": gx,
        "Gy": gy,
        "Gz": gz,
        "t": t,
        "vx": 0.0,
        "vy": 0.0,
        "vz": 0.0,
        "ax": 0.0,
        "ay": 0.0,
        "az": 0.0,
        "sx": 0.0,
        "sy": 0.0,
        "sz": 0.0,
        "fx": 0.0,
        "fy": 0.0,
        "fz": 0.0,
    }


class TestModelInference(unittest.TestCase):
    def test_online_window_buffer(self):
        buf = OnlineWindowBuffer(window_s=10.0, min_samples=5)

        features = None
        for i in range(8):
            features = buf.update(1000 + i * 1000, _row(i, ax=0.01 + i * 0.001))

        self.assertIsNotNone(features)
        assert features is not None
        self.assertIn("A_mag_mean", features)
        self.assertGreater(features["sample_count"], 4)

    def test_centroid_model_runner(self):
        feature_names = ["f1", "f2"]
        model = fit_centroid_classifier(
            features=[[0.0, 0.0], [0.1, 0.1], [8.0, 8.0], [8.2, 7.9]],
            labels=["normal", "normal", "fault", "fault"],
            feature_names=feature_names,
            task="fault_type",
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fault_model.json"
            model.save(str(path))

            runner = CentroidModelRunner(str(path))
            pred = runner.predict({"f1": 8.1, "f2": 8.0}, top_k=2)

            self.assertIsNotNone(pred)
            assert pred is not None
            self.assertEqual(pred.label, "fault")
            self.assertGreater(pred.confidence, 0.5)
            self.assertIn("fault", pred.top_k)


if __name__ == "__main__":
    unittest.main()
