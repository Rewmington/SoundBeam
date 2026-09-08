"""SoundBeam Stream Protocol v1 — 封包 / 解析（纯函数，无外部依赖）。

包格式（大端序，固定头 15 字节）:
    [0:2]   Magic      0x4153 ("AS")
    [2]     Version    0x01
    [3]     Flags      START=0x01 / STOP=0x02 / DATA=0x04 / HEARTBEAT=0x08
    [4:6]   Seq        uint16 包序号（循环计数）
    [6:10]  Timestamp  uint32 流启动后的相对毫秒
    [10]    Channels   1=mono, 2=stereo
    [11:13] SampleRate uint16 Hz
    [13:15] PayloadLen uint16 载荷字节数
    [15:]   Payload    PCM 16-bit 小端，交错声道
"""

import struct
from dataclasses import dataclass
from typing import Optional

MAGIC = b"AS"
VERSION = 0x01

FLAG_START = 0x01
FLAG_STOP = 0x02
FLAG_DATA = 0x04
FLAG_HEARTBEAT = 0x08

HEADER_SIZE = 15

DEFAULT_PORT = 50051
# 每 DATA 包 20ms（48kHz 立体声 16bit = 3840B）
PACKET_MS = 20
DEFAULT_SAMPLE_RATE = 48000
DEFAULT_CHANNELS = 2


@dataclass
class Packet:
    flags: int
    seq: int
    timestamp_ms: int
    channels: int
    sample_rate: int
    payload: bytes

    @property
    def is_start(self) -> bool:
        return bool(self.flags & FLAG_START)

    @property
    def is_stop(self) -> bool:
        return bool(self.flags & FLAG_STOP)

    @property
    def is_data(self) -> bool:
        return bool(self.flags & FLAG_DATA)

    @property
    def is_heartbeat(self) -> bool:
        return bool(self.flags & FLAG_HEARTBEAT)

    @property
    def samples_per_channel(self) -> int:
        """由载荷长度反推本包覆盖的每声道采样数。"""
        if self.channels <= 0:
            return 0
        return len(self.payload) // (2 * self.channels)


def build_packet(flags: int, seq: int, timestamp_ms: int, channels: int,
                 sample_rate: int, payload: bytes = b"") -> bytes:
    assert flags & 0xFF == flags
    assert 1 <= channels <= 2
    assert 0 < sample_rate <= 0xFFFF, sample_rate
    assert len(payload) <= 0xFFFF
    header = (
        MAGIC
        + bytes([VERSION, flags])
        + struct.pack(">H", seq & 0xFFFF)
        + struct.pack(">I", timestamp_ms & 0xFFFFFFFF)
        + bytes([channels])
        + struct.pack(">H", sample_rate)
        + struct.pack(">H", len(payload))
    )
    return header + payload


def parse_packet(data: bytes) -> Optional[Packet]:
    if len(data) < HEADER_SIZE:
        return None
    if data[0:2] != MAGIC:
        return None
    if data[2] != VERSION:
        return None
    flags = data[3]
    seq = struct.unpack(">H", data[4:6])[0]
    ts = struct.unpack(">I", data[6:10])[0]
    channels = data[10]
    sample_rate = struct.unpack(">H", data[11:13])[0]
    payload_len = struct.unpack(">H", data[13:15])[0]
    if len(data) != HEADER_SIZE + payload_len:
        return None
    return Packet(
        flags=flags,
        seq=seq,
        timestamp_ms=ts,
        channels=channels,
        sample_rate=sample_rate,
        payload=data[HEADER_SIZE:],
    )
