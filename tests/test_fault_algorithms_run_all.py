import unittest
from unittest.mock import patch

from report.fault_algorithms import run_all as run_all_module


def _rows(count: int = 12):
    ts0 = 1_000_000
    rows = []
    for i in range(count):
        rows.append(
            {
                "ts_ms": ts0 + i * 1000,
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
        "reasons": [f"score={score:.2f}"],
        "feature_snapshot": {"n": 12},
    }


class TestFaultAlgorithmsRunAll(unittest.TestCase):
    def test_default_detectors_only_keep_rope_and_rubber(self):
        self.assertEqual(
            [detector.__module__.rsplit(".", 1)[-1] for detector in run_all_module.DETECTORS],
            ["detect_rope_looseness", "detect_rubber_hardening"],
        )

    def test_normal_sample_returns_no_high_confidence_candidates(self):
        fake_detectors = [
            lambda features: _result("mechanical_looseness", 18.0, triggered=False),
            lambda features: _result("rope_looseness", 33.0, triggered=False),
            lambda features: _result("impact_shock", 11.0, triggered=False),
        ]

        with patch.object(run_all_module, "DETECTORS", fake_detectors):
            payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "normal")
        self.assertEqual(payload["candidate_faults"], [])
        self.assertEqual(payload["watch_faults"], [])
        self.assertEqual(payload["top_fault"]["fault_type"], "rope_looseness")
        self.assertEqual(payload["top_candidate"], {})

    def test_rope_fault_can_be_promoted_to_candidate(self):
        fake_detectors = [
            lambda features: _result("mechanical_looseness", 22.0, triggered=False),
            lambda features: _result("rope_looseness", 72.0, triggered=True),
            lambda features: _result("guide_rail_wear", 48.0, triggered=False),
        ]

        with patch.object(run_all_module, "DETECTORS", fake_detectors):
            payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "candidate_faults")
        self.assertEqual(payload["top_candidate"]["fault_type"], "rope_looseness")
        self.assertEqual(len(payload["candidate_faults"]), 1)
        self.assertEqual(payload["candidate_faults"][0]["screening"], "high_confidence")
        self.assertEqual(len(payload["watch_faults"]), 1)
        self.assertEqual(payload["watch_faults"][0]["fault_type"], "guide_rail_wear")

    def test_rubber_fault_can_be_promoted_to_candidate(self):
        fake_detectors = [
            lambda features: _result("mechanical_looseness", 18.0, triggered=False),
            lambda features: _result("rubber_hardening", 69.0, triggered=True),
            lambda features: _result("rope_looseness", 44.0, triggered=False),
        ]

        with patch.object(run_all_module, "DETECTORS", fake_detectors):
            payload = run_all_module.run_all_rows(_rows(), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "candidate_faults")
        self.assertEqual(payload["top_candidate"]["fault_type"], "rubber_hardening")
        self.assertEqual(len(payload["candidate_faults"]), 1)
        self.assertEqual(payload["candidate_faults"][0]["fault_type"], "rubber_hardening")
        self.assertEqual(payload["watch_faults"], [])

    def test_low_quality_window_suppresses_candidates(self):
        fake_detectors = [
            lambda features: _result("rope_looseness", 88.0, triggered=True, quality_factor=1.0),
        ]

        with patch.object(run_all_module, "DETECTORS", fake_detectors):
            payload = run_all_module.run_all_rows(_rows(count=4), source="inline_rows")

        self.assertEqual(payload["screening"]["status"], "low_quality")
        self.assertEqual(payload["candidate_faults"], [])
        self.assertEqual(payload["top_candidate"], {})
        self.assertEqual(payload["top_fault"]["fault_type"], "rope_looseness")


if __name__ == "__main__":
    unittest.main()
