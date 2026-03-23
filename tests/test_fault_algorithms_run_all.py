import unittest
from unittest.mock import patch

from report.fault_algorithms import run_all as run_all_module


def _rows(count: int = 463, *, step_ms: int = 25):
    ts0 = 1_000_000
    rows = []
    for i in range(count):
        rows.append(
            {
                "ts_ms": ts0 + i * step_ms,
                "Ax": 0.01,
                "Ay": 0.02,
                "Az": -0.98,
                "Gx": 0.1,
                "Gy": 0.2,
                "Gz": 0.3,
                "t": 25.0,
                "is_new_frame": 1,
            }
        )
    return rows


def _result(fault_type: str, score: float, *, triggered: bool, quality_factor: float = 1.0) -> dict:
    if score >= 80.0:
        level = "alarm"
    elif score >= 60.0:
        level = "warning"
    elif score >= 35.0:
        level = "watch"
    else:
        level = "normal"
    return {
        "fault_type": fault_type,
        "score": score,
        "level": level,
        "triggered": triggered,
        "quality_factor": quality_factor,
        "reasons": [f"score={score:.2f}", "sampling_condition=on_target_40hz"],
        "feature_snapshot": {
            "n": 463,
            "sampling_ok_40hz": True,
            "sampling_condition": "on_target_40hz",
            "axis_mapping_signature": "vertical=Az|lateral_x=Ax|lateral_y=Ay",
        },
        "sampling_condition": "on_target_40hz",
        "axis_mapping_signature": "vertical=Az|lateral_x=Ax|lateral_y=Ay",
    }


class TestFaultAlgorithmsRunAll(unittest.TestCase):
    def test_default_detectors_only_keep_rope_and_rubber(self):
        self.assertEqual(
            [detector.__module__.rsplit(".", 1)[-1] for detector in run_all_module.DETECTORS],
            ["detect_rope_looseness"],
        )

    def test_normal_sample_returns_no_high_confidence_candidates(self):
        fake_detectors = [
            lambda features: _result("rope_looseness", 33.0, triggered=False),
            lambda features: _result("rubber_hardening", 41.0, triggered=False),
        ]

        with patch.object(run_all_module, "DETECTORS", fake_detectors):
            payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "normal")
        self.assertEqual(payload["candidate_faults"], [])
        self.assertEqual(payload["watch_faults"], [])
        self.assertEqual(payload["top_fault"]["fault_type"], "rubber_hardening")
        self.assertEqual(payload["top_candidate"], {})
        self.assertEqual(payload["rope_primary"]["fault_type"], "rope_looseness")
        self.assertEqual(payload["rubber_primary"]["fault_type"], "rubber_hardening")
        self.assertTrue(payload["summary"]["sampling_ok_40hz"])

    def test_rope_fault_can_be_promoted_to_candidate(self):
        fake_detectors = [
            lambda features: _result("rope_looseness", 72.0, triggered=True),
            lambda features: _result("rubber_hardening", 48.0, triggered=False),
        ]

        with patch.object(
            run_all_module,
            "_system_abnormality",
            return_value={
                "status": "candidate_faults",
                "score": 70.0,
                "shared_abnormal_score": 71.0,
                "baseline_mode": "robust_baseline",
                "baseline_weight": 0.85,
                "baseline_features": 3,
                "baseline_match": True,
                "run_state_score": 62.0,
                "gate_mode": "running",
                "sampling_ok_40hz": True,
                "sampling_condition": "on_target_40hz",
            },
        ):
            with patch.object(run_all_module, "DETECTORS", fake_detectors):
                payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "candidate_faults")
        self.assertEqual(payload["top_candidate"]["fault_type"], "rope_looseness")
        self.assertEqual(payload["rope_primary"]["fault_type"], "rope_looseness")
        self.assertEqual(len(payload["candidate_faults"]), 1)
        self.assertEqual(payload["candidate_faults"][0]["screening"], "high_confidence")
        self.assertEqual(payload["watch_faults"], [])
        self.assertEqual(payload["rubber_primary"]["fault_type"], "rubber_hardening")

    def test_auxiliary_rubber_fault_does_not_replace_primary_rope_status(self):
        fake_detectors = [
            lambda features: _result("rope_looseness", 44.0, triggered=False),
            lambda features: _result("rubber_hardening", 69.0, triggered=True),
        ]

        with patch.object(run_all_module, "DETECTORS", fake_detectors):
            payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "normal")
        self.assertEqual(payload["top_fault"]["fault_type"], "rubber_hardening")
        self.assertEqual(payload["rope_primary"]["fault_type"], "rope_looseness")
        self.assertEqual(payload["top_candidate"], {})
        self.assertEqual(payload["candidate_faults"], [])
        self.assertEqual(payload["rubber_primary"]["fault_type"], "rubber_hardening")
        self.assertEqual(payload["top_fault"]["score"], 69.0)

    def test_low_quality_window_suppresses_candidates(self):
        fake_detectors = [
            lambda features: _result("rope_looseness", 88.0, triggered=True, quality_factor=1.0),
        ]

        with patch.object(run_all_module, "DETECTORS", fake_detectors):
            payload = run_all_module.run_all_rows(_rows(count=100), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "low_quality")
        self.assertEqual(payload["candidate_faults"], [])
        self.assertEqual(payload["top_candidate"], {})
        self.assertEqual(payload["top_fault"]["fault_type"], "rope_looseness")
        self.assertFalse(payload["summary"]["sampling_ok_40hz"])

    def test_rubber_candidate_stays_auxiliary_when_rope_only_mainline(self):
        fake_detectors = [
            lambda features: _result("rope_looseness", 46.0, triggered=False),
            lambda features: _result("rubber_hardening", 73.0, triggered=True),
        ]

        with patch.object(
            run_all_module,
            "_system_abnormality",
            return_value={
                "status": "candidate_faults",
                "score": 72.0,
                "shared_abnormal_score": 74.0,
                "baseline_mode": "robust_baseline",
                "baseline_weight": 0.85,
                "baseline_features": 3,
                "baseline_match": True,
                "run_state_score": 66.0,
                "gate_mode": "running",
                "sampling_ok_40hz": True,
                "sampling_condition": "on_target_40hz",
            },
        ):
            with patch.object(run_all_module, "DETECTORS", fake_detectors):
                payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "watch_only")
        self.assertEqual(payload["top_fault"]["fault_type"], "rope_looseness")
        self.assertEqual(payload["primary_issue"]["fault_type"], "rope_looseness")
        self.assertEqual(payload["candidate_faults"], [])
        self.assertEqual(payload["rubber_primary"]["fault_type"], "rubber_hardening")

    def test_system_watch_without_type_specific_signal_returns_unknown_watch(self):
        fake_detectors = [
            lambda features: _result("rope_looseness", 43.0, triggered=False),
            lambda features: _result("rubber_hardening", 44.0, triggered=False),
        ]

        with patch.object(
            run_all_module,
            "_system_abnormality",
            return_value={
                "status": "watch_only",
                "score": 52.0,
                "shared_abnormal_score": 58.0,
                "baseline_mode": "robust_baseline",
                "baseline_weight": 0.85,
                "baseline_features": 3,
                "baseline_match": True,
                "run_state_score": 61.0,
                "gate_mode": "running",
                "sampling_ok_40hz": True,
                "sampling_condition": "on_target_40hz",
            },
        ):
            with patch.object(run_all_module, "DETECTORS", fake_detectors):
                payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "watch_only")
        self.assertEqual(payload["primary_issue"]["fault_type"], "unknown")
        self.assertEqual(payload["top_fault"]["fault_type"], "unknown")
        self.assertEqual(payload["watch_faults"][0]["fault_type"], "unknown")


if __name__ == "__main__":
    unittest.main()
