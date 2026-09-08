"""音频工具：设备枚举去重 + 试听音。

问题 3 的两个改动落在这里，做成纯函数便于测试：
- dedupe_devices: 同一物理设备在多个 hostapi（MME/WASAPI/DirectSound/WDM-KS）
  下会各出现一次，按设备名归一化去重，优先保留 WASAPI（低延迟）。
- play_test_tone: 播放一段短测试音到指定设备，用于「试听」确认设备发声。
"""

import math

import numpy as np
import sounddevice as sd

# hostapi 优先级：数值越小越优先
_API_PRIORITY = {
    "WASAPI": 0,
    "WDM-KS": 1,
    "DIRECTSOUND": 2,
    "MME": 3,
}
_SILENCE_DB = -90.0


def _normalize_name(name: str) -> str:
    """设备名归一化：剥离开头 hostapi/类型前缀，留核心设备名作为去重 key。

    例：
      "[输出 · MME] Speakers (Realtek HD Audio)" 与
      "[输出 · WASAPI] Speakers (Realtek HD Audio)"
        → 都归为 "speakers (realtek hd audio)"，从而合并同一个物理设备。
    """
    s = name.lower().strip()
    # 去掉开头的 [...] 前缀（hostapi + 类型标注，形如 "[输出 · WASAPI]"）
    if s.startswith("[") and "]" in s:
        s = s[s.index("]") + 1:].strip()
    # 统一空白
    return " ".join(s.split())


def dedupe_devices(device_list: list, prefer_api: str = "WASAPI") -> list:
    """对 [(label, index, api_name), ...] 按设备名去重，优先保留 prefer_api。

    返回与输入相同的 `(label, index)` 二元组列表（顺序保持），
    但同一物理设备只保留一个（选 hostapi 优先级最高的那次）。
    """
    prefer = prefer_api.upper()
    best_by_key: dict[str, tuple] = {}  # key -> (api_rank, original_item)
    kept: dict[str, tuple] = {}         # key -> original_item
    order: list[str] = []               # 保持首次出现的顺序

    for item in device_list:
        label, index, api = item[0], item[1], (item[2] or "").upper()
        key = _normalize_name(label)
        if not key:
            continue
        rank = _API_PRIORITY.get(api, 99)
        if prefer in api:
            rank = -1  # 明确期望的 hostapi 绝对优先
        if key not in best_by_key or rank < best_by_key[key][0]:
            best_by_key[key] = (rank, (label, index))
            if key not in kept:
                order.append(key)
            kept[key] = (label, index)

    return [kept[k] for k in order]


def play_test_tone(device: int | None, volume: float = 1.0,
                   samplerate: int = 48000, freq: float = 440.0,
                   duration: float = 0.35, channels: int = 2) -> tuple:
    """向指定输出设备播放一段测试音（440Hz beep），用于确认设备发声。

    返回 (ok, device_name)：ok 是否成功，device_name 实际播放到的设备名；
    失败时 device_name 为错误说明。GUI 里放后台线程调用（sd.play 阻塞）。
    """
    try:
        dev = _preferred(device)
        name = _device_name(dev)
        # 声道数用设备实际支持的输出通道数，避免多声道设备（如 7.1 耳机 8 通道）
        # 与固定 2 声道不匹配而报 Invalid number of channels / 静音。
        ch = _output_channels(dev) or channels or 2
        frames = int(samplerate * duration)
        t = np.arange(frames, dtype=np.float64) / samplerate
        mono = (0.4 * float(volume) * np.sin(2 * math.pi * freq * t)).astype(np.float32)
        data = np.repeat(mono[:, np.newaxis], ch, axis=1)
        sd.play(data, samplerate=samplerate, device=dev)
        sd.wait()
        return True, name
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _output_channels(device: int | None) -> int:
    """返回设备支持的输出通道数；查不到返回 0。"""
    try:
        d = sd.query_devices(device if device is not None else _preferred(None))
        return int(d.get("max_output_channels") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _preferred(device: int | None) -> int | None:
    """device=None 时优先用户当前的真实默认输出设备（sd.default.device[1]）。
    _InputOutputPair 支持下标索引取输出；取不到再回退默认。"""
    if device is not None:
        return device
    try:
        dd = sd.default.device
        # _InputOutputPair([in,out]) 可按下标取出设备；也兼容单 int 情形
        out = dd[1] if not isinstance(dd, int) else dd
        if out is not None:
            d = sd.query_devices(int(out))
            if d["max_output_channels"] > 0:
                return int(out)
    except Exception:  # noqa: BLE001
        pass
    return None


def _device_name(device: int | None) -> str:
    """取设备名；device=None 时取当前真实默认输出设备名。"""
    dev = device if device is not None else _preferred(None)
    try:
        d = sd.query_devices(dev)
        return d["name"]
    except Exception:  # noqa: BLE001
        return "默认输出"
