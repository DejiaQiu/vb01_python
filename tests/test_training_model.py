import tempfile
import unittest
from pathlib import Path

from elevator_monitor.training.centroid_model import CentroidModel, fit_centroid_classifier


class TestCentroidModel(unittest.TestCase):
    def test_fit_predict_and_persist(self):
        feature_names = ["f1", "f2"]
        x_train = [
            [0.0, 0.1],
            [0.1, 0.0],
            [0.2, 0.2],
            [9.8, 10.2],
            [10.0, 9.9],
            [10.2, 10.1],
        ]
        y_train = ["normal", "normal", "normal", "fault", "fault", "fault"]

        x_val = [
            [0.05, 0.05],
            [10.1, 10.0],
            [0.15, 0.1],
            [9.9, 10.3],
        ]
        y_val = ["normal", "fault", "normal", "fault"]

        model = fit_centroid_classifier(
            features=x_train,
            labels=y_train,
            feature_names=feature_names,
            task="fault_type",
            eval_features=x_val,
            eval_labels=y_val,
        )

        self.assertGreaterEqual(model.metrics.get("accuracy", 0.0), 0.9)

        pred, conf = model.predict_vec([10.0, 10.2])
        self.assertEqual(pred, "fault")
        self.assertGreater(conf, 0.5)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.json"
            model.save(str(path))
            loaded = CentroidModel.load(str(path))
            pred2, conf2 = loaded.predict_vec([0.0, 0.0])
            self.assertEqual(pred2, "normal")
            self.assertGreater(conf2, 0.5)


if __name__ == "__main__":
    unittest.main()
