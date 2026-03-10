import math
import unittest

from elevator_monitor.legacy_dtu_vibration import (
    convert_legacy_dtu_rows,
    legacy_dtu_row_to_vibration_row,
)


class TestLegacyDtuVibration(unittest.TestCase):
    def test_legacy_row_maps_to_vibration_fields(self):
        row = {
            "dtu_id": "6",
            "ts": "1770086303206",
            "dtu_data": "{}",
            "dtu_vib": (
                '{"AX": 3, "AY": 4, "AZ": 0, "GX": 1, "GY": 2, "GZ": 2, '
                '"VX": 0, "VY": 1, "VZ": 2, "DX": 7, "DY": 8, "DZ": 9, '
                '"HZX": 0.4, "HZY": 0.5, "HZZ": 0.6, '
                '"YAW": 10, "ROLL": 20, "PITCH": 30, "TEMPB": 26.5}'
            ),
        }

        converted = legacy_dtu_row_to_vibration_row(row)

        self.assertEqual(converted["elevator_id"], "elevator-001")
        self.assertEqual(converted["ts_ms"], "1770086303206")
        self.assertEqual(converted["data_ts_ms"], "1770086303206")
        self.assertEqual(converted["data_age_ms"], "0")
        self.assertEqual(converted["is_new_frame"], "1")
        self.assertEqual(converted["Ax"], "3.0")
        self.assertEqual(converted["Ay"], "4.0")
        self.assertEqual(converted["Az"], "0.0")
        self.assertEqual(converted["Gx"], "1.0")
        self.assertEqual(converted["Gy"], "2.0")
        self.assertEqual(converted["Gz"], "2.0")
        self.assertEqual(converted["vx"], "0.0")
        self.assertEqual(converted["vy"], "1.0")
        self.assertEqual(converted["vz"], "2.0")
        self.assertEqual(converted["ax"], "10.0")
        self.assertEqual(converted["ay"], "20.0")
        self.assertEqual(converted["az"], "30.0")
        self.assertEqual(converted["t"], "26.5")
        self.assertEqual(converted["sx"], "7")
        self.assertEqual(converted["sy"], "8")
        self.assertEqual(converted["sz"], "9")
        self.assertEqual(converted["fx"], "4")
        self.assertEqual(converted["fy"], "5")
        self.assertEqual(converted["fz"], "6")
        self.assertAlmostEqual(float(converted["A_mag"]), 5.0, places=6)
        self.assertAlmostEqual(float(converted["G_mag"]), 3.0, places=6)

    def test_convert_rows_sorts_by_ts(self):
        rows = [
            {
                "ts": "2000",
                "dtu_vib": '{"AX": 0, "AY": 1, "AZ": 0, "GX": 0, "GY": 0, "GZ": 1}',
            },
            {
                "ts": "1000",
                "dtu_vib": '{"AX": 1, "AY": 0, "AZ": 0, "GX": 1, "GY": 0, "GZ": 0}',
            },
        ]

        converted = convert_legacy_dtu_rows(rows, elevator_id="elevator-xyz")

        self.assertEqual([row["ts_ms"] for row in converted], ["1000", "2000"])
        self.assertTrue(all(row["elevator_id"] == "elevator-xyz" for row in converted))
        self.assertTrue(math.isclose(float(converted[0]["A_mag"]), 1.0))
        self.assertTrue(math.isclose(float(converted[1]["G_mag"]), 1.0))


if __name__ == "__main__":
    unittest.main()
