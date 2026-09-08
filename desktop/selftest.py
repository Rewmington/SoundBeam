"""端到端自检：本机启动 AudioReceiver，用 mock 发送正弦波，验证链路与抖动缓冲。

用法: python -m desktop.selftest [--duration 5]
退出码 0 = 通过；1 = 失败。
"""

import argparse
import threading
import time

from .mock_phone import main as mock_main
from .receiver import AudioReceiver
from .protocol import DEFAULT_PORT, PACKET_MS


def _run_mock(ip: str, port: int, duration: float) -> None:
    import sys
    sys.argv = ["mock_phone", "--ip", ip, "--port", str(port), "--duration", str(duration)]
    try:
        mock_main()
    except SystemExit:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=4.0)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    recv = AudioReceiver(args.port)
    recv.start()

    # 等待流启动（mock 线程并发发送）
    t = threading.Thread(target=_run_mock, args=("127.0.0.1", args.port, args.duration), daemon=True)
    t.start()

    deadline = time.monotonic() + 3.0
    while recv.audio_params() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    params = recv.audio_params()
    if params is None:
        print("FAIL: stream never started (mock sender did not reach receiver)")
        recv.stop()
        return 1
    sr, ch = params
    print(f"stream started: {sr} Hz, {ch} ch")

    expected = int(1000 / PACKET_MS * args.duration)  # 包/秒 × 时长
    got, silence = 0, 0
    t0 = time.time()
    while time.time() - t0 < args.duration + 2.5:
        chunk = recv.get_chunk(timeout=1.0)
        if chunk is None:
            break
        if chunk == bytes(len(chunk)):
            silence += 1
        else:
            got += 1
    elapsed = time.time() - t0
    print(f"received {got} real packets, {silence} silence-filled gaps, "
          f"pps={recv.packets_per_sec}, loss={recv.loss_percent:.1f}%")

    recv.stop()
    min_expected = int(expected * 0.9)
    if got < min_expected:
        print(f"FAIL: only {got} real packets (expected >= {min_expected})")
        return 1
    if recv.loss_percent > 5.0:
        print(f"FAIL: loss {recv.loss_percent:.1f}% too high on loopback")
        return 1
    print("selftest PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())