import unittest

from report.fault_algorithms._base import axis_mapping_signature
from report.fault_algorithms.detect_rubber_hardening import detect


def _baseline(*, mapping_signature: str | None = None) -> dict:
    stats = {
        "az_std": (0.0093, 0.0012),
        "az_rms_ac": (0.0093, 0.0012),
        "az_p2p": (0.0375, 0.0040),
        "az_cv": (0.77, 0.08),
        "az_jerk_rms": (0.0138, 0.0025),
        "corr_xy": (-0.70, 0.16),
        "corr_xz": (0.00, 0.28),
        "corr_yz": (0.00, 0.28),
        "energy_z_over_xy": (0.90, 0.20),
        "a_crest": (1.65, 0.25),
        "a_kurt": (0.80, 0.45),
        "peak_rate_hz": (0.20, 0.08),
        "a_rms_ac": (0.0120, 0.0020),
        "a_p2p": (0.0550, 0.0080),
        "g_std": (0.0500, 0.0100),
    }
    return {
        "count": 24,
        "stats": {key: {"median": median, "scale": scale} for key, (median, scale) in stats.items()},
        "axis_mapping_signature": mapping_signature or axis_mapping_signature(None),
        "axis_mapping_mode": "default",
    }


def _feature_overrides(**kwargs):
    base = {
        "n": 463,
        "fs_hz": 40.0,
        "duration_s": 11.55,
        "sampling_ok": True,
        "sampling_ok_40hz": True,
        "sampling_condition": "sampling_ok",
        "axis_mapping_mode": "default",
        "axis_mapping_signature": axis_mapping_signature(None),
        "used_new_only": True,
        "a_mean": 1.0,
        "g_mean": 0.90,
        "a_rms_ac": 0.012,
        "a_p2p": 0.055,
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
        self.assertIn("baseline_match=true", result["reasons"])

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
                corr_xy=0.28,
                corr_xz=0.24,
                corr_yz=0.22,
                energy_z_over_xy=1.95,
                a_crest=1.72,
                a_kurt=1.05,
                peak_rate_hz=0.14,
            )
        )

        self.assertGreaterEqual(result["score"], 60.0, msg=result)
        self.assertTrue(result["triggered"], msg=result)
        self.assertIn("confirm=candidate_hits_pass", result["reasons"])

    def test_fallback_without_baseline_is_capped_to_watch(self):
        result = detect(
            _feature_overrides(
                a_rms_ac=0.017,
                g_std=0.075,
                az_std=0.0172,
                az_rms_ac=0.0170,
                az_p2p=0.061,
                az_cv=1.58,
                az_jerk_rms=0.030,
                corr_xy=0.82,
                corr_xz=0.54,
                corr_yz=0.49,
                energy_z_over_xy=2.10,
                a_crest=1.68,
                a_kurt=0.95,
                peak_rate_hz=0.12,
            )
        )

        self.assertGreaterEqual(result["score"], 45.0, msg=result)
        self.assertLess(result["score"], 60.0, msg=result)
        self.assertFalse(result["triggered"], msg=result)
        self.assertIn("baseline_match=na", result["reasons"])

    def test_mapping_mismatch_blocks_candidate(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(mapping_signature="vertical=Ax|lateral_x=Ay|lateral_y=Az"),
                a_rms_ac=0.016,
                g_std=0.070,
                az_std=0.0165,
                az_rms_ac=0.0165,
                az_p2p=0.058,
                az_cv=1.42,
                az_jerk_rms=0.027,
                corr_xy=0.28,
                corr_xz=0.24,
                corr_yz=0.22,
                energy_z_over_xy=1.95,
            )
        )

        self.assertGreaterEqual(result["score"], 45.0, msg=result)
        self.assertLess(result["score"], 60.0, msg=result)
        self.assertFalse(result["triggered"], msg=result)
        self.assertIn("baseline_match=false", result["reasons"])

    def test_low_sampling_rate_does_not_trigger_hardening(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(),
                n=120,
                fs_hz=2.0,
                duration_s=60.0,
                sampling_ok=False,
                sampling_ok_40hz=False,
                sampling_condition="low_sampling_rate",
                a_rms_ac=0.016,
                g_std=0.070,
                az_std=0.0165,
                az_rms_ac=0.0165,
                az_p2p=0.058,
                az_cv=1.42,
                az_jerk_rms=0.027,
                energy_z_over_xy=1.95,
            )
        )

        self.assertLess(result["score"], 45.0, msg=result)
        self.assertFalse(result["triggered"], msg=result)
        self.assertIn("sampling_condition=low_sampling_rate", result["reasons"])

    def test_spiky_impact_signature_does_not_trigger_hardening(self):
        result = detect(
            _feature_overrides(
                baseline=_baseline(),
                a_rms_ac=0.020,
                g_std=0.085,
                az_std=0.018,
                az_rms_ac=0.018,
                az_p2p=0.065,
                az_cv=1.45,
                az_jerk_rms=0.035,
                corr_xy=0.55,
                corr_xz=0.42,
                corr_yz=0.36,
                energy_z_over_xy=2.00,
                a_crest=4.9,
                a_kurt=8.2,
                peak_rate_hz=4.6,
            )
        )

        self.assertLess(result["score"], 60.0, msg=result)
        self.assertFalse(result["triggered"], msg=result)


if __name__ == "__main__":
    unittest.main()
