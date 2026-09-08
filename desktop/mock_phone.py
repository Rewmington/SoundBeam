"""模拟手机端：按 SoundBeam 协议发送正弦波 PCM 流（无手机时联调电脑端用）。

用法:
    python -m desktop.mock_phone --ip 127.0.0.1 --port 50051 --duration 5 --freq 440
"""

import argparse
import math
import struct
import time

from .protocol import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    FLAG_DATA,
    FLAG_HEARTBEAT,
    FLAG_START,
    FLAG_STOP,
    PACKET_MS,
    build_packet,
)


def generate_tone(freq: float, duration_s: float, sample_rate: int = DEFAULT_SAMPLE_RATE,
                  channels: int = DEFAULT_CHANNELS) -> bytes:
    """生成双声道 16bit 正弦波 PCM（小端交错）。"""
    total = int(sample_rate * duration_s)
    out = bytearray()
    for i in range(total):
        v = int(32767 * 0.3 * math.sin(2 * math.pi * freq * i / sample_rate))
        out += struct.pack("<h", v) * channels
    return bytes(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Mock phone sender (SoundBeam protocol)")
    ap.add_argument("--ip", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=50051)
    ap.add_argument("--duration", type=float, default=5.0)
    ap.add_argument("--freq", type=float, default=440.0)
    ap.add_argument("--drop-every", type=int, default=0,
                    help="模拟丢包：每 N 包丢弃 1 包（0=不丢）")
    args = ap.parse_args()

    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (args.ip, args.port)
    sr, ch = DEFAULT_SAMPLE_RATE, DEFAULT_CHANNELS
    frames_per_pkt = sr * PACKET_MS // 1000  # 960

    seq = 0
    ts = 0

    def send(pkt: bytes) -> None:
        nonlocal seq
        sock.sendto(pkt, addr)
        seq = (seq + 1) & 0xFFFF

    # START
    send(build_packet(FLAG_START, seq, ts, ch, sr))
    ts += PACKET_MS

    total_frames = int(sr * args.duration)
    sent = 0
    pkt_idx = 0
    start = time.time()
    while sent < total_frames:
        n = min(frames_per_pkt, total_frames - sent)
        payload = bytearray()
        for i in range(n):
            v = int(32767 * 0.3 * math.sin(2 * math.pi * args.freq * (sent + i) / sr))
            payload += struct.pack("<h", v) * ch
        pkt_idx += 1
        if args.drop_every > 0 and pkt_idx % args.drop_every == 0:
            print(f"  [mock] dropped pkt #{seq} (simulated)")
            seq = (seq + 1) & 0xFFFF  # 模拟端跳过该序号
        else:
            send(build_packet(FLAG_DATA, seq, ts, ch, sr, bytes(payload)))
        sent += n
        ts += PACKET_MS
        # 每 1s 一个心跳
        if pkt_idx % (1000 // PACKET_MS) == 0:
            send(build_packet(FLAG_HEARTBEAT, seq, ts, ch, sr))
        # 尽量贴合实时速率
        elapsed = time.time() - start
        target = sent / sr
        if target > elapsed:
            time.sleep(target - elapsed)

    send(build_packet(FLAG_STOP, seq, ts, ch, sr))
    print(f"[mock] sent {pkt_idx} data packets ({args.duration}s @ {args.freq}Hz) to {addr[0]}:{addr[1]}")


if __name__ == "__main__":
    main()
