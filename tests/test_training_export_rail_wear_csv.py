import math
import unittest

from elevator_monitor.monitor.constants import RAIL_WEAR_FIELDS
from elevator_monitor.training.export_rail_wear_csv import convert_rows


def _row(ts_ms: int, ax: float, gx: float) -> dict[str, float | int]:
    return {
        "elevator_id": "elevator-1",
        "ts_ms": ts_ms,
        "Ax": ax,
        "Ay": 0.0,
        "Az": -1.0,
        "Gx": gx,
        "Gy": 0.0,
        "Gz": 0.0,
        "t": 26.0,
    }


class TestExportRailWearCsv(unittest.TestCase):
    def test_convert_rows_output_schema(self):
        rows = []
        # 正常段：用于构建基线。
        for i in range(320):
            rows.append(_row(i * 10, ax=0.01 * math.sin(i * 0.2), gx=0.05 * math.sin(i * 0.1)))
        # 异常段：持续抖动提升，应该产生 warning/critical。
        for j in range(160):
            k = 320 + j
            rows.append(_row(k * 10, ax=0.9 * math.sin(j * 0.2), gx=2.0 * math.sin(j * 0.1)))

        out = convert_rows(rows, sample_hz=100.0)
        self.assertTrue(out)

        keys = list(out[0].keys())
        self.assertEqual(RAIL_WEAR_FIELDS, keys)
        self.assertTrue(any(int(r.get("alarm_flag", 0)) == 1 for r in out))


if __name__ == "__main__":
    unittest.main()
