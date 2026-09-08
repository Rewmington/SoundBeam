# -*- coding: utf-8 -*-
"""测试 audio_tools.dedupe_devices 的去重逻辑（纯函数，不依赖硬件）。"""
import unittest

from .audio_tools import dedupe_devices


class TestDedupeDevices(unittest.TestCase):
    def test_same_output_device_multi_hostapi_keeps_wasapi(self):
        """同一输出物理设备以 MME + WASAPI 两种 hostapi 出现，应只保留 WASAPI。"""
        devices = [
            ("[输出 · MME] Speakers (Realtek High Definition Audio)", 3, "MME"),
            ("[输出 · WASAPI] Speakers (Realtek High Definition Audio)", 8, "WASAPI"),
        ]
        out = dedupe_devices(devices)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][1], 8)

    def test_same_input_device_keeps_wasapi_when_after_mme(self):
        """USB 输入设备同名但 hostapi 不同，WASAPI 排后面也应胜出。"""
        devices = [
            ("[MME] Microphone (Realtek HD Audio) (44100 Hz)", 2, "MME"),
            ("[WASAPI] Microphone (Realtek HD Audio) (44100 Hz)", 9, "WASAPI"),
        ]
        out = dedupe_devices(devices)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][1], 9)

    def test_distinct_devices_kept(self):
        """不同物理设备不应被合并。"""
        devices = [
            ("[输出 · WASAPI] Speakers (Realtek HD Audio)", 8, "WASAPI"),
            ("[输出 · WASAPI] Headphones (USB Audio)", 9, "WASAPI"),
        ]
        out = dedupe_devices(devices)
        self.assertEqual(len(out), 2)

    def test_empty(self):
        self.assertEqual(dedupe_devices([]), [])


if __name__ == "__main__":
    unittest.main()
