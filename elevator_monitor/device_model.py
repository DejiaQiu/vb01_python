# coding:UTF-8
import threading
import time

import serial
from serial import SerialException


# 串口配置
class SerialConfig:
    # 串口号
    portName = ""

    # 波特率
    baud = 115200


# 设备实例
class DeviceModel:
    # 设备名称
    deviceName = "我的设备"

    # 设备modbus ID
    ADDR = 0x50

    def __init__(self, deviceName, portName, baud, ADDR, verbose=True):
        self.verbose = verbose
        self._log("初始化设备模型")
        self.deviceName = deviceName
        self.ADDR = ADDR

        # 统一改为实例级状态，避免多实例共享导致串扰
        self.serialConfig = SerialConfig()
        self.serialConfig.portName = portName
        self.serialConfig.baud = baud

        self.deviceData = {}
        self.isOpen = False
        self.loop = False
        self.serialPort = None

        self.TempBytes = []
        self.statReg = None

        self._data_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._parse_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._loop_stop_event = threading.Event()
        self._read_thread = None
        self._loop_thread = None

        self._last_update_ts_ms = None

    def _log(self, message):
        if self.verbose:
            print(message)

    # 获得CRC校验
    def get_crc(self, datas, dlen):
        crc = 0xFFFF
        for i in range(dlen):
            crc ^= datas[i]
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        # 返回格式沿用原逻辑：高字节在前
        hi = (crc >> 8) & 0xFF
        lo = crc & 0xFF
        return (hi << 8) | lo

    # region 获取设备数据

    # 设置设备数据
    def set(self, key, value):
        with self._data_lock:
            self.deviceData[key] = value

    # 获得设备数据
    def get(self, key):
        with self._data_lock:
            return self.deviceData.get(key)

    # 删除设备数据
    def remove(self, key):
        with self._data_lock:
            self.deviceData.pop(key, None)

    def get_snapshot(self, keys=None):
        with self._data_lock:
            if keys is None:
                return dict(self.deviceData)
            return {k: self.deviceData.get(k) for k in keys}

    def get_last_update_ts_ms(self):
        with self._state_lock:
            return self._last_update_ts_ms

    def has_fresh_data(self, max_age_ms=1000):
        last_ts = self.get_last_update_ts_ms()
        if last_ts is None:
            return False
        return int(time.time() * 1000) - last_ts <= max_age_ms

    def wait_for_data(self, timeout_s=2.0, poll_s=0.01):
        deadline = time.perf_counter() + max(0.0, timeout_s)
        while time.perf_counter() < deadline:
            if self.get_last_update_ts_ms() is not None:
                return True
            time.sleep(max(0.001, poll_s))
        return False

    # endregion

    # 打开设备
    def openDevice(self):
        # 先关闭端口（清理旧线程）
        self.closeDevice()

        try:
            self.serialPort = serial.Serial(self.serialConfig.portName, self.serialConfig.baud, timeout=0.5)
        except SerialException as ex:
            self.serialPort = None
            self.isOpen = False
            self._log("打开{}失败: {}".format(self.serialConfig.portName, ex))
            return False

        self.isOpen = True
        self._stop_event.clear()
        self._read_thread = threading.Thread(
            target=self.readDataTh,
            args=("Data-Received-Thread",),
            daemon=True,
        )
        self._read_thread.start()
        self._log("{}已打开".format(self.serialConfig.portName))
        self._log("设备打开成功")
        return True

    # 监听串口数据线程
    def readDataTh(self, threadName):
        self._log("启动" + threadName)

        while not self._stop_event.is_set():
            if not self.isOpen or self.serialPort is None:
                break

            try:
                if hasattr(self.serialPort, "in_waiting"):
                    t_len = self.serialPort.in_waiting
                else:
                    t_len = self.serialPort.inWaiting()
                if t_len <= 0:
                    time.sleep(0.001)
                    continue

                data = self.serialPort.read(t_len)
                if data:
                    self.onDataReceived(data)
            except SerialException as ex:
                self._log("串口读取异常: {}".format(ex))
                break
            except Exception as ex:
                self._log("读取线程异常: {}".format(ex))
                time.sleep(0.05)

        self._log("读取线程退出")

    # 关闭设备
    def closeDevice(self):
        self.stopLoopRead()

        self.isOpen = False
        self._stop_event.set()

        port = self.serialPort
        self.serialPort = None
        if port is not None:
            try:
                port.close()
                self._log("端口关闭了")
            except Exception as ex:
                self._log("端口关闭异常: {}".format(ex))

        t = self._read_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.0)
        self._read_thread = None

        with self._parse_lock:
            self.TempBytes.clear()

        self._log("设备关闭了")

    # region 数据解析

    # 串口数据处理
    def onDataReceived(self, data):
        if not data:
            return

        with self._parse_lock:
            self.TempBytes.extend(data)

            while True:
                # 至少需要地址位
                if len(self.TempBytes) < 1:
                    return

                # 找地址头
                if self.TempBytes[0] != self.ADDR:
                    del self.TempBytes[0]
                    continue

                # 至少需要地址+功能码+长度
                if len(self.TempBytes) < 3:
                    return

                # 只处理读取功能码 0x03
                if self.TempBytes[1] != 0x03:
                    del self.TempBytes[0]
                    continue

                payload_len = self.TempBytes[2]
                frame_len = payload_len + 5
                if payload_len <= 0 or payload_len % 2 != 0:
                    del self.TempBytes[0]
                    continue

                if len(self.TempBytes) < frame_len:
                    return

                frame = self.TempBytes[:frame_len]
                temp_crc = self.get_crc(frame, frame_len - 2)
                if (temp_crc & 0xFF) == frame[frame_len - 2] and ((temp_crc >> 8) & 0xFF) == frame[frame_len - 1]:
                    del self.TempBytes[:frame_len]
                    self.processData(payload_len, frame)
                else:
                    # CRC失败，丢1字节重新同步
                    del self.TempBytes[0]

    # 数据解析
    def processData(self, length, frame=None):
        if frame is None:
            frame = self.TempBytes

        with self._state_lock:
            start_reg = self.statReg

        if start_reg is None:
            return

        reg = start_reg
        for i in range(int(length / 2)):
            idx = 3 + 2 * i
            if idx + 1 >= len(frame):
                break

            # 寄存器数据
            value = (frame[idx] << 8) | frame[idx + 1]
            value = self.change(value)

            # 振动加速度解析
            if 0x34 <= reg <= 0x36:
                value = value / 32768 * 16
                self.set(str(reg), value)
                reg += 1

            # 振动角速度解析
            elif 0x37 <= reg <= 0x39:
                value = value / 32768 * 2000
                self.set(str(reg), value)
                reg += 1

            # 振动角度解析
            elif 0x3D <= reg <= 0x3F:
                value = value / 32768 * 180
                self.set(str(reg), value)
                reg += 1

            # 温度解析
            elif reg == 0x40:
                value = value / 100
                self.set(str(reg), value)
                reg += 1

            # 其他
            else:
                self.set(str(reg), value)
                reg += 1

        with self._state_lock:
            self.statReg = reg
            self._last_update_ts_ms = int(time.time() * 1000)

    # endregion

    # 发送串口数据
    def sendData(self, data):
        if not self.isOpen or self.serialPort is None:
            self._log("发送失败: 串口未打开")
            return False

        payload = data
        if not isinstance(payload, (bytes, bytearray)):
            payload = bytes(payload)

        try:
            self.serialPort.write(payload)
            return True
        except Exception as ex:
            self._log("发送失败: {}".format(ex))
            return False

    # 读取寄存器
    def readReg(self, regAddr, regCount):
        if regCount <= 0:
            return False

        with self._state_lock:
            self.statReg = regAddr

        # 封装读取指令并向串口发送数据
        return self.sendData(self.get_readBytes(self.ADDR, regAddr, regCount))

    # 写入寄存器
    def writeReg(self, regAddr, sValue):
        if not self.isOpen:
            self._log("写寄存器失败: 串口未打开")
            return False

        # 解锁
        if not self.unlock():
            return False

        # 延迟100ms
        time.sleep(0.1)

        # 封装写入指令并向串口发送数据
        if not self.sendData(self.get_writeBytes(self.ADDR, regAddr, sValue)):
            return False

        # 延迟100ms
        time.sleep(0.1)

        # 保存
        return self.save()

    # 发送读取指令封装
    def get_readBytes(self, devid, regAddr, regCount):
        tempBytes = bytearray(8)
        # 设备modbus地址
        tempBytes[0] = devid
        # 读取功能码
        tempBytes[1] = 0x03
        # 寄存器高8位
        tempBytes[2] = (regAddr >> 8) & 0xFF
        # 寄存器低8位
        tempBytes[3] = regAddr & 0xFF
        # 读取寄存器个数高8位
        tempBytes[4] = (regCount >> 8) & 0xFF
        # 读取寄存器个数低8位
        tempBytes[5] = regCount & 0xFF
        # 获得CRC校验
        tempCrc = self.get_crc(tempBytes, len(tempBytes) - 2)
        # Modbus RTU CRC 按低字节在前发送
        tempBytes[6] = tempCrc & 0xFF
        tempBytes[7] = (tempCrc >> 8) & 0xFF
        return bytes(tempBytes)

    # 发送写入指令封装
    def get_writeBytes(self, devid, regAddr, sValue):
        tempBytes = bytearray(8)
        # 设备modbus地址
        tempBytes[0] = devid
        # 写入功能码
        tempBytes[1] = 0x06
        # 寄存器高8位
        tempBytes[2] = (regAddr >> 8) & 0xFF
        # 寄存器低8位
        tempBytes[3] = regAddr & 0xFF
        # 寄存器值高8位
        tempBytes[4] = (sValue >> 8) & 0xFF
        # 寄存器值低8位
        tempBytes[5] = sValue & 0xFF
        # 获得CRC校验
        tempCrc = self.get_crc(tempBytes, len(tempBytes) - 2)
        # Modbus RTU CRC 按低字节在前发送
        tempBytes[6] = tempCrc & 0xFF
        tempBytes[7] = (tempCrc >> 8) & 0xFF
        return bytes(tempBytes)

    # 开始循环读取
    def startLoopRead(self, regAddr=0x34, regCount=13, period_s=0.2):
        if not self.isOpen or self.serialPort is None:
            raise RuntimeError("串口未打开，无法启动循环读取")

        if self._loop_thread is not None and self._loop_thread.is_alive():
            return

        self.loop = True
        self._loop_stop_event.clear()
        self._loop_thread = threading.Thread(
            target=self.loopRead,
            args=(regAddr, regCount, period_s),
            daemon=True,
        )
        self._loop_thread.start()

    # 循环读取线程
    def loopRead(self, regAddr, regCount, period_s):
        self._log("循环读取开始")
        next_t = time.perf_counter()

        while self.loop and not self._loop_stop_event.is_set() and self.isOpen:
            self.readReg(regAddr, regCount)

            if period_s is None or period_s <= 0:
                continue

            next_t += period_s
            sleep_s = next_t - time.perf_counter()
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                # 发生调度延迟时重置节拍，防止累计漂移
                next_t = time.perf_counter()

        self._log("循环读取结束")

    # 关闭循环读取
    def stopLoopRead(self):
        self.loop = False
        self._loop_stop_event.set()

        t = self._loop_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.0)
        self._loop_thread = None

    # 解锁
    def unlock(self):
        cmd = self.get_writeBytes(self.ADDR, 0x69, 0xB588)
        return self.sendData(cmd)

    # 保存
    def save(self):
        cmd = self.get_writeBytes(self.ADDR, 0x00, 0x0000)
        return self.sendData(cmd)

    @staticmethod
    def change(data):
        # 16位有符号转换
        if data >= 0x8000:
            data -= 0x10000
        return data
