import json
import tempfile
import unittest
from pathlib import Path

from elevator_monitor.training.model_registry import build_manifest, summarize_model
from elevator_monitor.training.release_gate import GateConfig, evaluate_gate


class TestTrainingReleaseGate(unittest.TestCase):
    def _write_model(self, path: Path, task: str = "fault_type") -> None:
        payload = {
            "model_type": "centroid_classifier_v1",
            "task": task,
            "feature_names": ["f1", "f2"],
            "classes": ["normal", "fault"],
            "metrics": {
                "accuracy": 0.82,
                "macro_f1": 0.78,
                "weighted_f1": 0.80,
                "support": 120,
                "per_class": {
                    "normal": {"precision": 0.85, "recall": 0.80, "f1": 0.82, "support": 90},
                    "fault": {"precision": 0.70, "recall": 0.75, "f1": 0.72, "support": 30},
                },
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_model_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fault_model.json"
            self._write_model(path)

            summary = summarize_model(str(path))
            self.assertEqual(summary["task"], "fault_type")
            self.assertEqual(summary["class_count"], 2)
            self.assertTrue(summary["sha256"])

            manifest = build_manifest([str(path)], project="demo", environment="prod", created_by="qa")
            self.assertEqual(manifest["project"], "demo")
            self.assertEqual(len(manifest["models"]), 1)

    def test_release_gate_pass_and_fail(self):
        payload = {
            "task": "risk_24h",
            "metrics": {
                "accuracy": 0.83,
                "macro_f1": 0.80,
                "weighted_f1": 0.82,
                "support": 100,
                "per_class": {
                    "0": {"precision": 0.90, "recall": 0.85, "f1": 0.87, "support": 70},
                    "1": {"precision": 0.68, "recall": 0.76, "f1": 0.72, "support": 30},
                },
            },
        }

        pass_cfg = GateConfig(
            expected_task="risk_24h",
            min_accuracy=0.75,
            min_macro_f1=0.75,
            min_weighted_f1=0.78,
            min_support=80,
            positive_label="1",
            min_positive_recall=0.70,
        )
        pass_result = evaluate_gate(payload, pass_cfg)
        self.assertTrue(pass_result["pass"])

        fail_cfg = GateConfig(
            expected_task="risk_24h",
            min_accuracy=0.9,
            positive_label="1",
            min_positive_precision=0.8,
        )
        fail_result = evaluate_gate(payload, fail_cfg)
        self.assertFalse(fail_result["pass"])
        self.assertGreaterEqual(len(fail_result["failures"]), 1)


if __name__ == "__main__":
    unittest.main()
