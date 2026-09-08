"""协议封包/解析单元测试。运行: python -m desktop.test_protocol"""

import struct

from .protocol import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    FLAG_DATA,
    FLAG_START,
    FLAG_STOP,
    HEADER_SIZE,
    build_packet,
    parse_packet,
)


def test_roundtrip():
    payload = bytes(range(128))
    raw = build_packet(FLAG_DATA, seq=1234, timestamp_ms=60000,
                       channels=2, sample_rate=48000, payload=payload)
    assert len(raw) == HEADER_SIZE + 128
    pkt = parse_packet(raw)
    assert pkt is not None
    assert pkt.flags == FLAG_DATA
    assert pkt.seq == 1234
    assert pkt.timestamp_ms == 60000
    assert pkt.channels == 2
    assert pkt.sample_rate == 48000
    assert pkt.payload == payload
    assert pkt.samples_per_channel == 128 // (2 * 2)
    print("test_roundtrip OK")


def test_bigendian_layout():
    """逐字节核对头布局（大端 + Magic/版本/标志）。"""
    raw = build_packet(FLAG_START | FLAG_DATA, seq=0x0203, timestamp_ms=0x04050607,
                       channels=1, sample_rate=0x0809, payload=b"AB")
    assert raw[0:2] == b"AS"
    assert raw[2] == 0x01
    assert raw[3] == FLAG_START | FLAG_DATA
    assert raw[4:6] == b"\x02\x03"
    assert raw[6:10] == b"\x04\x05\x06\x07"
    assert raw[10] == 1
    assert raw[11:13] == b"\x08\x09"
    assert raw[13:15] == struct.pack(">H", 2)
    assert raw[15:] == b"AB"
    print("test_bigendian_layout OK")


def test_bad_packets():
    # 太短
    assert parse_packet(b"AS\x01") is None
    # Magic 错
    good = build_packet(FLAG_DATA, 1, 0, 2, 48000, b"\x00\x00")
    bad_magic = b"XX" + good[2:]
    assert parse_packet(bad_magic) is None
    # 长度不匹配
    bad_len = good[:-1] + b"\x00\x00\x00"
    assert parse_packet(bad_len) is None
    # 空包
    assert parse_packet(b"") is None
    print("test_bad_packets OK")


def test_empty_payload_start():
    raw = build_packet(FLAG_START, seq=0, timestamp_ms=0,
                       channels=DEFAULT_CHANNELS, sample_rate=DEFAULT_SAMPLE_RATE)
    pkt = parse_packet(raw)
    assert pkt is not None
    assert pkt.is_start and not pkt.is_data
    assert pkt.payload == b""
    print("test_empty_payload_start OK")


def test_stop_flag():
    raw = build_packet(FLAG_STOP, seq=999, timestamp_ms=1, channels=2, sample_rate=44100)
    pkt = parse_packet(raw)
    assert pkt is not None and pkt.is_stop
    print("test_stop_flag OK")


if __name__ == "__main__":
    test_roundtrip()
    test_bigendian_layout()
    test_bad_packets()
    test_empty_payload_start()
    test_stop_flag()
    print("\nAll protocol tests passed")