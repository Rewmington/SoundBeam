"""sounddevice 播放器：从 AudioReceiver 取 PCM 数据，回调式输出，支持音量增益。

内部维护样本缓冲，按回调实际需要的帧数精确取数（不依赖 20ms 块边界），
兼容 PortAudio 在任何平台上的回调缓冲大小；同时统计音量电平（RMS dB）。

低延迟策略：优先使用 Windows WASAPI（shared 模式，输出缓冲 ~22ms），
避免 MME 驱动（~180ms）。支持播放中切换输出设备。
"""

import logging
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger("soundbeam.player")

# int16 峰值对应 0 dBFS 参考：rms 换算 dBFS 的缩放
_RMS_REF = 32768.0
_SILENCE_DB = -90.0


def preferred_output_device(device: int | None) -> int | None:
    """把 device=None 解析为高质量输出设备：优先 Windows WASAPI 默认输出。

    用户未显式选择设备时，避免落到 MME（延迟~180ms）。
    """
    if device is not None:
        return device
    try:
        for api in sd.query_hostapis():
            if "WASAPI" in api["name"].upper():
                idx = api.get("default_output_device")
                if idx is not None and sd.query_devices(idx)["max_output_channels"] > 0:
                    return idx
    except Exception:  # noqa: BLE001
        pass
    return None  # 非 Windows 或无 WASAPI：让 PortAudio 用默认


def wasapi_extra_settings(device: int | None):
    """设备属于 WASAPI host API 时返回共享模式设置，否则 None。"""
    if device is None:
        return None
    try:
        apis = sd.query_hostapis()
        d = sd.query_devices(device)
        if "WASAPI" in apis[d["hostapi"]]["name"].upper():
            return sd.WasapiSettings(exclusive=False)
    except Exception:  # noqa: BLE001
        pass
    return None


class AudioPlayer:
    def __init__(self, receiver, volume: float = 1.0, device: int | None = None):
        self._receiver = receiver
        self._volume = float(volume)
        self._device = device
        self._sr = 48000
        self._channels = 2
        self._stream = None
        self._pending = bytearray()  # 尚未消费的 PCM int16 样本（小端交错）
        # 最近音量电平（dBFS），供界面显示；-90 表示静音/无数据
        self.level_db = _SILENCE_DB

    @property
    def playing(self) -> bool:
        return self._stream is not None and self._stream.active

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(2.0, float(volume)))

    def set_device(self, device: int | None) -> bool:
        """播放中切换输出设备（保留接收缓冲，切换平滑）。"""
        self._device = device
        if self._stream is None:
            return False
        self._stop_stream()
        ok = self._open_stream()
        if not ok:
            log.error("device switch failed, attempting default")
            self._device = None
            self._open_stream()
        return ok

    def start(self) -> bool:
        params = self._receiver.audio_params()
        if params is None:
            log.warning("start() called before audio params known")
            return False
        self._sr, self._channels = params
        self._pending.clear()
        self.level_db = _SILENCE_DB
        ok = self._open_stream()
        if ok:
            log.info("player started: %d Hz, %d ch, device=%s",
                     self._sr, self._channels, self._device)
        return ok

    def _open_stream(self) -> bool:
        try:
            device = preferred_output_device(self._device)
            self._stream = sd.OutputStream(
                samplerate=self._sr,
                channels=self._channels,
                dtype="float32",
                device=device,
                extra_settings=wasapi_extra_settings(device),
                callback=self._callback,
                blocksize=0,
            )
            self._stream.start()
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("open stream failed: %s", exc)
            self._stream = None
            return False

    def stop(self) -> None:
        self._stream = None  # 先置空，防止回调继续引用
        self.level_db = _SILENCE_DB
        self._stop_stream()

    def _stop_stream(self) -> None:
        st = self._stream
        if st is not None:
            try:
                st.stop()
                st.close()
            except Exception:  # noqa: BLE001
                pass

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        try:
            need = frames * outdata.shape[1] * 2  # 本次所需的 PCM 字节数
            # 从接收器补齐样本缓冲
            while len(self._pending) < need:
                chunk = self._receiver.get_chunk(timeout=0.5)
                if chunk is None:
                    break  # 流未激活/已停止/暂时无数据
                self._pending += chunk

            if len(self._pending) >= need:
                data16 = np.frombuffer(bytes(self._pending[:need]), dtype=np.int16)
                del self._pending[:need]
                audio = data16.astype(np.float32) / _RMS_REF
                rms = float(np.sqrt(np.mean(np.abs(audio.astype(np.float64)) ** 2))) if audio.size else 0.0
                self.level_db = (
                    20.0 * np.log10(rms) if rms > 1e-6 else _SILENCE_DB
                )
                outdata[:, :] = audio.reshape(frames, outdata.shape[1]) * self._volume
            else:
                self.level_db = _SILENCE_DB
                outdata.fill(0.0)
        except Exception:  # noqa: BLE001
            # 回调异常时输出静音，避免播放线程崩溃
            self.level_db = _SILENCE_DB
            try:
                outdata.fill(0.0)
            except Exception:  # noqa: BLE001
                pass
