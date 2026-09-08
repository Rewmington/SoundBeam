"""UDP 接收线程 + 抖动缓冲（jitter buffer）。

职责：
- 绑定 UDP 端口接收 SoundBeam 协议包
- 按 seq 排序缓冲，目标延迟约 4 包（80ms），播放缺包补静音
- 统计丢包率 / 接收速率，供界面展示
"""

import socket
import struct
import threading
import time
from typing import Dict, Optional, Tuple

from .protocol import (
    FLAG_DATA,
    FLAG_HEARTBEAT,
    FLAG_START,
    FLAG_STOP,
    HEADER_SIZE,
    PACKET_MS,
    Packet,
    parse_packet,
)

# jitter buffer 目标延迟（包数）。0xFFFF 的约 1/8，避免 seq 绕回歧义
TARGET_BUFFER_PACKETS = 3
MAX_BUFFER_PACKETS = 40
# seq 循环窗口：用于判断先后
SEQ_WINDOW = 0x8000


class AudioReceiver:
    def __init__(self, port: int, host: str = "0.0.0.0"):
        self.port = port
        self.host = host
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        self._sock.bind((host, port))
        self._sock.settimeout(0.2)

        self._lock = threading.Lock()
        self._buf: Dict[int, bytes] = {}  # seq -> payload(PCM16LE)
        self._next_seq: Optional[int] = None
        self._sample_rate = 0
        self._channels = 0
        self._stream_active = False  # 已收到 START
        self._stopped = False        # 收到 STOP

        # 统计（最近 1s 滑动窗口）
        self._recv_total = 0         # 总收包数（DATA）
        self._loss_total = 0         # 估算丢包总数
        self._window_ts = 0.0
        self._window_pkts = 0
        self.packets_per_sec = 0     # 每秒收包数
        self.loss_percent = 0.0      # 丢包率 %
        self.rate_kbps = 0.0         # 折算音频码率
        self.last_heartbeat_ts = 0.0
        self.remote_addr: Optional[Tuple[str, int]] = None

        self._thread = threading.Thread(target=self._run, daemon=True, name="udp-recv")

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._sock.close()

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def audio_params(self) -> Optional[Tuple[int, int]]:
        """返回 (sample_rate, channels)；流未初始化返回 None。"""
        with self._lock:
            if self._sample_rate and self._channels:
                return self._sample_rate, self._channels
            return None

    def _reset_stream(self) -> None:
        self._buf.clear()
        self._next_seq = None
        self._stream_active = False
        self._stopped = False
        self._recv_total = 0
        self._loss_total = 0

    # ---------- 播放侧取数 ----------
    def get_chunk(self, timeout: float = 0.5) -> Optional[bytes]:
        """取一个 20ms PCM 块（int16 小端交错声道）。

        返回 bytes（可能为补静音）；流未开始或已停止返回 None。
        """
        deadline = time.monotonic() + timeout
        frames = self._channels * self._sample_rate * PACKET_MS // 1000
        silence = bytes(max(frames, 0) * 2)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready: Optional[bytes] = None
            with self._lock:
                if not self._stream_active or self._stopped:
                    return None
                expected = self._next_seq
                if expected is None:
                    if self._buf:
                        expected = min(self._buf.keys())
                        self._next_seq = expected
                    # 无数据：等待
                if expected is not None and expected in self._buf:
                    payload = self._buf.pop(expected)
                    self._next_seq = (expected + 1) & 0xFFFF
                    ready = payload
                elif expected is not None and self._has_future(expected):
                    # 已有后续包在等：当前序缺失，判为丢包，补静音推进
                    self._next_seq = (expected + 1) & 0xFFFF
                    self._loss_total += 1
                    ready = silence
                else:
                    # 数据未到齐，短暂等待后重试
                    ready = None
            if ready is not None:
                return ready
            time.sleep(min(0.005, remaining))

    def _has_future(self, seq: int) -> bool:
        """buffer 中是否存在位于 seq 之后（按循环序）的包。"""
        for s in self._buf:
            if self._seq_after(s, seq):
                return True
        return False

    @staticmethod
    def _seq_after(a: int, b: int) -> bool:
        """a 是否在 b 之后（seq 是 uint16 循环，窗口取 SEQ_WINDOW）。"""
        return ((a - b) % 0x10000) < SEQ_WINDOW

    # ---------- 接收线程 ----------
    def _run(self) -> None:
        while True:
            try:
                data, addr = self._sock.recvfrom(65536)
            except socket.timeout:
                self._update_stats()
                if not self._sock.fileno() or self._sock.fileno() == -1:
                    return
                continue
            except OSError:
                return  # socket 已关闭
            pkt = parse_packet(data)
            if pkt is None:
                continue
            with self._lock:
                self.remote_addr = addr
                if pkt.is_start:
                    self._reset_stream()
                    self._sample_rate = pkt.sample_rate or 48000
                    self._channels = pkt.channels or 2
                    self._stream_active = True
                    self.last_heartbeat_ts = time.time()
                    continue
                if pkt.is_stop:
                    self._stopped = True
                    continue
                if pkt.is_heartbeat:
                    self.last_heartbeat_ts = time.time()
                    continue
                if pkt.is_data:
                    if not self._stream_active and pkt.sample_rate and pkt.channels:
                        # 容错：START 包丢失时，从 DATA 包头恢复流参数（协议头每包自带）
                        self._reset_stream()
                        self._sample_rate = pkt.sample_rate
                        self._channels = pkt.channels
                        self._stream_active = True
                        self.last_heartbeat_ts = time.time()
                    if self._stream_active:
                        self._buf[pkt.seq] = pkt.payload
                        self._recv_total += 1
                        self._window_pkts += 1
                        if len(self._buf) > MAX_BUFFER_PACKETS:
                            # 丢弃最旧的（按循环序最小）
                            oldest = min(self._buf, key=lambda s: ((s - self._next_seq) % 0x10000) if self._next_seq is not None else s)
                            self._buf.pop(oldest, None)
            self._update_stats()

    def _update_stats(self) -> None:
        now = time.time()
        with self._lock:
            if now - self._window_ts >= 1.0:
                self.packets_per_sec = self._window_pkts
                window_bytes = self._window_pkts * 3840 if self._stream_active else 0
                self.rate_kbps = window_bytes * 8 / 1000.0
                self._window_ts = now
                self._window_pkts = 0
                total = self._recv_total + self._loss_total
                self.loss_percent = (self._loss_total / total * 100.0) if total else 0.0
