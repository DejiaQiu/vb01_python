import unittest

from elevator_monitor.device_model import DeviceModel


class TestDeviceModel(unittest.TestCase):
    def test_change_int16(self):
        self.assertEqual(DeviceModel.change(0x7FFF), 32767)
        self.assertEqual(DeviceModel.change(0x8000), -32768)
        self.assertEqual(DeviceModel.change(0xFFFF), -1)

    def test_build_read_bytes(self):
        m = DeviceModel("d", "/dev/null", 115200, 0x50, verbose=False)
        packet = m.get_readBytes(0x50, 0x34, 19)
        self.assertIsInstance(packet, bytes)
        self.assertEqual(len(packet), 8)
        self.assertEqual(packet[0], 0x50)
        self.assertEqual(packet[1], 0x03)

    def test_parse_frame_updates_data(self):
        m = DeviceModel("d", "/dev/null", 115200, 0x50, verbose=False)
        m.statReg = 0x34

        # 3 registers: 0x0001, 0x7FFF, 0x8000
        frame = bytearray([0x50, 0x03, 0x06, 0x00, 0x01, 0x7F, 0xFF, 0x80, 0x00])
        crc = m.get_crc(frame, len(frame))
        frame.extend([(crc >> 8) & 0xFF, crc & 0xFF])

        m.onDataReceived(bytes(frame))

        self.assertIsNotNone(m.get("52"))
        self.assertIsNotNone(m.get("53"))
        self.assertIsNotNone(m.get("54"))
        self.assertIsNotNone(m.get_last_update_ts_ms())

    def test_send_data_when_closed(self):
        m = DeviceModel("d", "/dev/null", 115200, 0x50, verbose=False)
        ok = m.sendData(b"abc")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
