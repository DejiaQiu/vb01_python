import unittest

from report.fault_algorithms.fault_detectors import run_fault_detectors


def _features(**overrides):
    payload = {
        "n": 720,
        "fs_hz": 40.0,
        "duration_s": 18.0,
        "sampling_ok": True,
        "sampling_ok_40hz": True,
        "sampling_condition": "sampling_ok",
        "used_new_only": True,
        "new_ratio": 1.0,
        "axis_mapping_signature": "vertical=Az|lateral_x=Ax|lateral_y=Ay",
        "energy_x_over_y": 0.93,
        "corr_xy": -0.15,
        "corr_xz": 0.12,
        "a_pca_primary_ratio": 0.50,
        "z_peak_ratio": 0.13,
        "az_cv": 1.05,
        "az_jerk_rms": 1.02,
        "lateral_ratio": 0.96,
        "lat_dom_freq_hz": 1.2,
    }
    payload.update(overrides)
    return payload


def _system(status: str, score: float = 60.0):
    return {"status": status, "score": score}


class TestFaultDetectors(unittest.TestCase):
    def test_returns_empty_when_window_is_not_abnormal(self):
        result = run_fault_detectors(_features(), system_abnormality=_system("normal", 22.0))

        self.assertEqual(result["selected_issue"], {})
        self.assertEqual(result["detector_results"], [])

    def test_selects_rope_when_rope_detector_clearly_leads(self):
        result = run_fault_detectors(_features(), system_abnormality=_system("candidate_faults", 68.0))

        self.assertEqual(result["selected_issue"]["fault_type"], "rope_looseness")
        self.assertEqual(result["detector_results"][0]["fault_type"], "rope_looseness")
        self.assertTrue(result["detector_results"][0]["type_candidate_ready"])

    def test_keeps_unknown_when_two_detectors_are_mixed(self):
        result = run_fault_detectors(
            _features(
                energy_x_over_y=0.84,
                corr_xy=-0.33,
                corr_xz=-0.19,
                a_pca_primary_ratio=0.56,
                z_peak_ratio=0.10,
                az_cv=0.91,
                az_jerk_rms=0.95,
                lateral_ratio=1.18,
                lat_dom_freq_hz=2.2,
            ),
            system_abnormality=_system("watch_only", 48.0),
        )

        self.assertEqual(result["selected_issue"], {})
        self.assertEqual(len(result["detector_results"]), 2)

    def test_selects_rubber_when_rubber_detector_clearly_leads(self):
        result = run_fault_detectors(
            _features(
                energy_x_over_y=0.68,
                corr_xy=-0.54,
                corr_xz=-0.23,
                a_pca_primary_ratio=0.66,
                z_peak_ratio=0.08,
                az_cv=0.82,
                az_jerk_rms=0.84,
                lateral_ratio=1.42,
                lat_dom_freq_hz=3.3,
            ),
            system_abnormality=_system("watch_only", 52.0),
        )

        self.assertEqual(result["selected_issue"]["fault_type"], "rubber_hardening")
        self.assertEqual(result["detector_results"][0]["fault_type"], "rubber_hardening")
        self.assertTrue(result["detector_results"][0]["type_watch_ready"])

    def test_baseline_relative_scoring_can_support_rubber_selection(self):
        baseline = {
            "stats": {
                "corr_xy_abs": {"median": 0.10, "scale": 0.03},
                "corr_xz_abs": {"median": 0.08, "scale": 0.03},
                "a_pca_primary_ratio": {"median": 0.44, "scale": 0.05},
                "energy_x_over_y": {"median": 1.02, "scale": 0.05},
                "lateral_ratio": {"median": 0.82, "scale": 0.08},
                "lat_dom_freq_hz": {"median": 1.10, "scale": 0.30},
                "z_peak_ratio": {"median": 0.13, "scale": 0.02},
            }
        }
        result = run_fault_detectors(
            _features(
                energy_x_over_y=0.88,
                corr_xy=-0.36,
                corr_xz=-0.22,
                a_pca_primary_ratio=0.64,
                z_peak_ratio=0.08,
                az_cv=0.92,
                az_jerk_rms=0.94,
                lateral_ratio=1.28,
                lat_dom_freq_hz=3.4,
            ),
            system_abnormality=_system("watch_only", 52.0),
            baseline=baseline,
        )

        self.assertEqual(result["selected_issue"]["fault_type"], "rubber_hardening")
        self.assertEqual(result["detector_results"][0]["fault_type"], "rubber_hardening")
        self.assertTrue(result["detector_results"][0]["type_watch_ready"])


if __name__ == "__main__":
    unittest.main()
