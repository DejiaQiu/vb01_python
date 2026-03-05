import unittest

from elevator_monitor.training.train_utils import LabeledSample, sample_group_key, split_train_val


class TestTrainingSplit(unittest.TestCase):
    def test_sample_group_key_prefers_source_file(self):
        row = {
            "elevator_id": "e1",
            "window_start_ms": "1000",
            "source_file": "/tmp/capture_a.csv",
        }
        self.assertEqual(sample_group_key(row), "source_file:/tmp/capture_a.csv")

    def test_split_train_val_keeps_same_source_file_together(self):
        samples = [
            LabeledSample(row={"elevator_id": "e1", "window_start_ms": "1", "source_file": "capture_a.csv"}, label="normal"),
            LabeledSample(row={"elevator_id": "e1", "window_start_ms": "2", "source_file": "capture_a.csv"}, label="normal"),
            LabeledSample(row={"elevator_id": "e1", "window_start_ms": "3", "source_file": "capture_b.csv"}, label="fault"),
            LabeledSample(row={"elevator_id": "e1", "window_start_ms": "4", "source_file": "capture_b.csv"}, label="fault"),
        ]

        train, val = split_train_val(samples, val_ratio=0.5, seed=2026)
        self.assertTrue(train)
        self.assertTrue(val)

        train_groups = {sample.row["source_file"] for sample in train}
        val_groups = {sample.row["source_file"] for sample in val}
        self.assertFalse(train_groups & val_groups)


if __name__ == "__main__":
    unittest.main()
