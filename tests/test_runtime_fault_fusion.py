from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from elevator_monitor.monitor.args import build_arg_parser
from elevator_monitor.monitor.edge_diagnosis import diagnosis_to_anomaly_result, diagnosis_to_fault_result
from elevator_monitor.monitor.runtime import RealtimeMonitor


class TestEdgeDiagnosisMappings(unittest.TestCase):
    def test_candidate_faults_maps_to_anomaly_level(self):
        result = {
            "summary": {"sampling_condition": "good"},
            "screening": {"status": "candidate_faults", "quality_ok": True},
            "system_abnormality": {
                "score": 82.0,
                "gate_mode": "strict",
                "baseline_mode": "rolling_windows",
                "top_deviations": [{"key": "lateral_ratio", "score": 81.2}],
            },
        }

        anomaly = diagnosis_to_anomaly_result(result, baseline_ready=True, baseline_count=12)

        self.assertEqual("anomaly", anomaly["level"])
        self.assertEqual(82.0, anomaly["score"])
        self.assertTrue(anomaly["baseline_ready"])
        self.assertEqual(12, anomaly["baseline_count"])
        self.assertIn("screening:candidate_faults", anomaly["reasons"])
        self.assertIn("baseline:rolling_windows", anomaly["reasons"])

    def test_fault_result_uses_primary_issue_metadata(self):
        result = {
            "summary": {
                "sampling_condition": "good",
                "axis_mapping_signature": "vertical=Az;lateral_x=Ax;lateral_y=Ay",
            },
            "screening": {"status": "watch_only"},
            "system_abnormality": {
                "score": 67.5,
                "gate_mode": "watch",
                "baseline_mode": "rolling_windows",
                "baseline_match": True,
            },
            "primary_issue": {
                "fault_type": "rope_looseness",
                "score": 78.0,
                "reasons": ["lat_dom_freq_low", "lat_low_band_high"],
                "detector_family": "rope_primary",
                "attribution_margin": 9.5,
            },
            "candidate_faults": [],
            "watch_faults": [],
        }

        fault = diagnosis_to_fault_result(result)

        self.assertEqual("rope_looseness", fault["fault_type"])
        self.assertAlmostEqual(0.78, fault["fault_confidence"])
        self.assertEqual("edge_rule_engine_v2", fault["fault_source"])
        self.assertEqual("rope_looseness:78.0", fault["fault_candidates"])
        self.assertEqual("watch_only", fault["fault_screening_status"])
        self.assertEqual("rope_primary", fault["fault_detector_family"])
        self.assertAlmostEqual(9.5, fault["fault_attribution_margin"])
        self.assertEqual("rolling_windows", fault["baseline_mode"])
        self.assertEqual("good", fault["sampling_condition"])


class TestRealtimeMonitorSinglePath(unittest.TestCase):
    def _build_monitor(self, tmp_dir: str) -> RealtimeMonitor:
        args = build_arg_parser().parse_args([])
        args.log_file = str(Path(tmp_dir) / "monitor.log")
        args.health_path = str(Path(tmp_dir) / "health.json")
        args.output_data = str(Path(tmp_dir) / "data.csv")
        args.output_alert = str(Path(tmp_dir) / "alert.csv")
        args.profile_path = str(Path(tmp_dir) / "{elevator_id}.json")
        return RealtimeMonitor(args)

    @staticmethod
    def _close_monitor(monitor: RealtimeMonitor) -> None:
        for handler in list(monitor.logger.handlers):
            handler.close()
        monitor.logger.handlers.clear()

    def test_runtime_profile_payload_uses_edge_diagnosis_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._build_monitor(tmp)
            try:
                payload = monitor._build_profile_payload()
                health = monitor._build_health_snapshot()

                self.assertIn("edge_diagnosis", payload)
                self.assertIn("risk_predictor", payload)
                self.assertNotIn("anomaly_detector", payload)
                self.assertNotIn("fault_engine", payload)
                self.assertNotIn("feature_forecaster", payload)
                self.assertFalse(health["baseline_ready"])
                self.assertEqual(0, health["baseline_count"])
                self.assertEqual("normal", health["last_screening_status"])
            finally:
                self._close_monitor(monitor)

    def test_runtime_args_no_longer_expose_old_edge_chain(self):
        args = build_arg_parser().parse_args([])

        self.assertTrue(hasattr(args, "diagnosis_window_s"))
        self.assertTrue(hasattr(args, "diagnosis_step_s"))
        self.assertFalse(hasattr(args, "dify_enabled"))
        self.assertFalse(hasattr(args, "fault_model_path"))
        self.assertFalse(hasattr(args, "generated_algo_path"))
        self.assertFalse(hasattr(args, "risk_model_path"))
        self.assertFalse(hasattr(args, "fault_fusion_mode"))


if __name__ == "__main__":
    unittest.main()
