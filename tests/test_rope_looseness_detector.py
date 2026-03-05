import unittest

from report.fault_algorithms.detect_rope_looseness import detect


def _feature_overrides(**kwargs):
    base = {
        "n": 24,
        "fs_hz": 5.0,
        "duration_s": 24.0,
        "used_new_only": True,
        "a_rms_ac": 0.002,
        "a_p2p": 0.015,
        "a_std": 0.003,
        "a_crest": 1.3,
        "a_kurt": 0.5,
        "g_std": 0.010,
        "peak_rate_hz": 0.1,
        "zc_rate_hz": 4.0,
        "lateral_ratio": 1.0,
        "ag_corr": 0.10,
        "gx_ax_corr": 0.10,
        "gy_ay_corr": 0.10,
        "jerk_rms": 0.002,
    }
    base.update(kwargs)
    return base


class TestRopeLoosenessDetector(unittest.TestCase):
    def test_running_low_frequency_sway_triggers(self):
        result = detect(
            _feature_overrides(
                a_rms_ac=0.017,
                a_p2p=0.125,
                g_std=0.095,
                zc_rate_hz=0.25,
                peak_rate_hz=0.0,
                lateral_ratio=2.45,
                ag_corr=0.58,
                gx_ax_corr=0.62,
                gy_ay_corr=0.54,
                a_crest=1.4,
                a_kurt=0.8,
            )
        )

        self.assertGreaterEqual(result["score"], 60.0)
        self.assertTrue(result["triggered"])

    def test_non_running_window_is_suppressed(self):
        result = detect(
            _feature_overrides(
                a_rms_ac=0.001,
                a_p2p=0.010,
                g_std=0.004,
                zc_rate_hz=0.02,
                lateral_ratio=1.05,
                ag_corr=0.08,
                gx_ax_corr=0.06,
                gy_ay_corr=0.07,
            )
        )

        self.assertLess(result["score"], 35.0)
        self.assertFalse(result["triggered"])

    def test_spiky_impact_signature_does_not_trigger_rope_looseness(self):
        result = detect(
            _feature_overrides(
                a_rms_ac=0.018,
                a_p2p=0.140,
                g_std=0.080,
                zc_rate_hz=8.0,
                peak_rate_hz=5.5,
                lateral_ratio=0.85,
                ag_corr=0.12,
                gx_ax_corr=0.10,
                gy_ay_corr=0.09,
                a_crest=4.8,
                a_kurt=8.0,
            )
        )

        self.assertLess(result["score"], 60.0)
        self.assertFalse(result["triggered"])


if __name__ == "__main__":
    unittest.main()
