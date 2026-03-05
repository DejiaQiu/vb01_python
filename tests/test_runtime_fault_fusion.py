from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from elevator_monitor.generated_algorithm import GeneratedAlgorithmPrediction
from elevator_monitor.model_inference import ModelPrediction
from elevator_monitor.monitor.args import build_arg_parser
from elevator_monitor.monitor.runtime import RealtimeMonitor


class TestRuntimeFaultFusion(unittest.TestCase):
    def _build_monitor(self, tmp_dir: str, fusion_mode: str) -> RealtimeMonitor:
        args = build_arg_parser().parse_args([])
        args.log_file = str(Path(tmp_dir) / "monitor.log")
        args.health_path = str(Path(tmp_dir) / "health.json")
        args.output_data = str(Path(tmp_dir) / "data.csv")
        args.output_alert = str(Path(tmp_dir) / "alert.csv")
        args.profile_path = str(Path(tmp_dir) / "{elevator_id}.json")
        args.fault_fusion_mode = fusion_mode
        return RealtimeMonitor(args)

    @staticmethod
    def _close_monitor(monitor: RealtimeMonitor) -> None:
        for handler in list(monitor.logger.handlers):
            handler.close()
        monitor.logger.handlers.clear()

    def test_rule_primary_keeps_rule_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._build_monitor(tmp, "rule_primary")
            try:
                rule_result = {
                    "fault_type": "vibration_increase",
                    "fault_confidence": 0.62,
                    "fault_source": "vibration_rules",
                    "fault_candidates": "vibration_increase:0.620@vibration_rules",
                    "fault_reasons": "max_z=4.8",
                }
                model_pred = ModelPrediction(
                    label="door_stuck",
                    confidence=0.95,
                    top_k="door_stuck:0.950|normal:0.050",
                    probabilities={"door_stuck": 0.95, "normal": 0.05},
                )

                merged = monitor._merge_fault_result(
                    rule_result,
                    model_pred,
                    None,
                    {"level": "warning"},
                    None,
                    None,
                )

                self.assertEqual("vibration_increase", merged["fault_type"])
                self.assertEqual("door_stuck", merged["fault_model_pred"])
            finally:
                self._close_monitor(monitor)

    def test_rule_primary_uses_generated_when_rule_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._build_monitor(tmp, "rule_primary")
            try:
                rule_result = {
                    "fault_type": "unknown",
                    "fault_confidence": 0.0,
                    "fault_source": "none",
                    "fault_candidates": "",
                    "fault_reasons": "",
                }
                generated_pred = GeneratedAlgorithmPrediction(
                    label="bolt_loosen",
                    confidence=0.76,
                    top_k="bolt_loosen:0.760|unknown:0.240",
                    probabilities={"bolt_loosen": 0.76, "unknown": 0.24},
                    best_score=0.71,
                    threshold=0.40,
                )

                merged = monitor._merge_fault_result(
                    rule_result,
                    None,
                    generated_pred,
                    {"level": "warning"},
                    {"A_mag_mean": 1.0},
                    None,
                )

                self.assertEqual("bolt_loosen", merged["fault_type"])
                self.assertTrue(str(merged["fault_source"]).startswith("generated_algo:"))
            finally:
                self._close_monitor(monitor)

    def test_model_primary_can_override_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._build_monitor(tmp, "model_primary")
            try:
                rule_result = {
                    "fault_type": "vibration_increase",
                    "fault_confidence": 0.40,
                    "fault_source": "vibration_rules",
                    "fault_candidates": "vibration_increase:0.400@vibration_rules",
                    "fault_reasons": "max_z=3.2",
                }
                model_pred = ModelPrediction(
                    label="door_stuck",
                    confidence=0.90,
                    top_k="door_stuck:0.900|normal:0.100",
                    probabilities={"door_stuck": 0.90, "normal": 0.10},
                )

                merged = monitor._merge_fault_result(
                    rule_result,
                    model_pred,
                    None,
                    {"level": "warning"},
                    None,
                    None,
                )

                self.assertEqual("door_stuck", merged["fault_type"])
                self.assertTrue(str(merged["fault_source"]).startswith("model:"))
            finally:
                self._close_monitor(monitor)


if __name__ == "__main__":
    unittest.main()
