import unittest

from report.fault_algorithms.detect_rubber_hardening import detect


def _baseline() -> dict:
    stats = {
        "az_std": (0.0093, 0.0012),
        "az_rms_ac": (0.0093, 0.0012),
        "az_p2p": (0.0375, 0.0040),
        "az_cv": (0.77, 0.08),
        "az_jerk_rms": (0.0138, 0.0025),
        "az_qspread": (0.0260, 0.0045),
        "corr_xy": (-0.70, 0.16),
        "corr_xz": (0.00, 0.28),
        "corr_yz": (0.00, 0.28),
        "energy_z_over_xy": (0.90, 0.20),
        "mag_cv": (0.014, 0.003),
        "mag_std": (0.014, 0.003),
        "a_crest": (1.65, 0.25),
        "a_kurt": (0.80, 0.45),
        "peak_rate_hz": (0.20, 0.08),
    }
    return {
        "count": 24,
        "stats": {key: {"median": median, "scale": scale} for key, (median, scale) in stats.items()},
    }


def _feature_overrides(**kwargs):
    base = {
        "n": 24,
        "fs_hz": 0.65,
        "duration_s": 30.0,
        "used_new_only": True,
        "a_mean": 1.0,
        "g_mean": 0.90,
        "a_rms_ac": 0.012,
        "a_crest": 1.55,
        "a_kurt": 0.70,
        "g_std": 0.050,
        "peak_rate_hz": 0.18,
        "ax_p2p": 0.050,
        "ay_p2p": 0.044,
        "az_p2p": 0.038,
        "ax_rms_ac": 0.012,
        "ay_rms_ac": 0.010,
        "az_rms_ac": 0.0095,
        "az_std": 0.0095,
        "az_cv": 0.78,
        "az_jerk_rms": 0.014,
        "az_qspread": 0.026,
        "mag_std": 0.014,
        "mag_cv": 0.014,
        "corr_xy": -0.68,
        "corr_xz": 0.02,
        "corr_yz": -0.04,
        "energy_z_over_xy": 0.92,
        "energy_x_over_y": 1.15,
    }
    base.update(kwargs)
    return base


class TestRubberHardeningDetector(unittest.TestCase):
    def test_baseline_normal_like_sample_stays_suppressed(self):
        result = detect(_feature_overrides(baseline=_baseline()))

        self.assertLess(result["score"], 35.0, msg=result)
        self.assertFalse(result["triggered"], msg=result)
        self.assertIn("baseline_mode=robust_baseline", result["reasons"])

    def test_baseline_hardening_signature_can_be_promoted(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(),
                a_rms_ac=0.016,
                g_std=0.070,
                az_std=0.0165,
                az_rms_ac=0.0165,
                az_p2p=0.058,
                az_cv=1.42,
                az_jerk_rms=0.027,
                az_qspread=0.043,
                corr_xy=0.28,
                corr_xz=0.24,
                corr_yz=0.22,
                energy_z_over_xy=1.95,
                mag_cv=0.0085,
                mag_std=0.011,
                a_crest=1.72,
                a_kurt=1.05,
                peak_rate_hz=0.14,
            )
        )

        self.assertGreaterEqual(result["score"], 60.0, msg=result)
        self.assertTrue(result["triggered"], msg=result)
        self.assertIn("confirm=baseline_relative_pass", result["reasons"])

    def test_fallback_mode_requires_clear_directional_and_damping_evidence(self):
        result = detect(
            _feature_overrides(
                a_rms_ac=0.017,
                g_std=0.075,
                ax_p2p=0.036,
                ay_p2p=0.032,
                az_std=0.0172,
                az_rms_ac=0.0170,
                az_p2p=0.061,
                az_cv=1.58,
                az_jerk_rms=0.030,
                az_qspread=0.048,
                corr_xy=0.82,
                corr_xz=0.54,
                corr_yz=0.49,
                energy_z_over_xy=2.10,
                energy_x_over_y=1.95,
                mag_cv=0.007,
                mag_std=0.010,
                a_crest=1.68,
                a_kurt=0.95,
                peak_rate_hz=0.12,
            )
        )

        self.assertGreaterEqual(result["score"], 60.0, msg=result)
        self.assertTrue(result["triggered"], msg=result)
        self.assertIn("baseline_mode=self_normalized_fallback", result["reasons"])

    def test_spiky_impact_signature_does_not_trigger_hardening(self):
        result = detect(
            _feature_overrides(
                a_rms_ac=0.020,
                g_std=0.085,
                az_std=0.018,
                az_rms_ac=0.018,
                az_p2p=0.065,
                az_cv=1.45,
                az_jerk_rms=0.035,
                az_qspread=0.050,
                corr_xy=0.55,
                corr_xz=0.42,
                corr_yz=0.36,
                energy_z_over_xy=2.00,
                mag_cv=0.012,
                mag_std=0.014,
                a_crest=4.9,
                a_kurt=8.2,
                peak_rate_hz=4.6,
            )
        )

        self.assertLess(result["score"], 60.0, msg=result)
        self.assertFalse(result["triggered"], msg=result)


if __name__ == "__main__":
    unittest.main()
