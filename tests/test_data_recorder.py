import csv
import json
import tempfile
import unittest
from pathlib import Path

from elevator_monitor.data_recorder import DataRecorder


class TestDataRecorder(unittest.TestCase):
    def test_csv_write_and_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.csv"
            with DataRecorder(str(path), file_format="csv", fieldnames=["a", "b"], flush_every_n=1) as rec:
                rec.write({"a": 1, "b": 2})
                rec.write({"a": 3, "b": 4})

            with path.open("r", encoding="utf-8", newline="") as fp:
                rows = list(csv.DictReader(fp))

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["a"], "1")
            self.assertEqual(rows[1]["b"], "4")

    def test_jsonl_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.jsonl"
            with DataRecorder(str(path), file_format="jsonl", flush=False) as rec:
                rec.write({"x": 1})
                rec.write({"x": 2})

            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["x"], 1)

    def test_invalid_flush_every_n(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.csv"
            with DataRecorder(str(path), file_format="csv", fieldnames=["a"], flush_every_n=0) as rec:
                with self.assertRaises(ValueError):
                    rec.write({"a": 1})


if __name__ == "__main__":
    unittest.main()
