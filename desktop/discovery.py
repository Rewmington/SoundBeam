"""SoundBeam Discovery v1 —— 局域网设备发现（组播 + 广播 + 应答）。

方向：电脑接收端周期发送 ANNOUNCE，手机端监听并收集；手机端扫描时先发
QUERY，电脑端收到后立即单播应答 ANNOUNCE（不依赖周期定时，响应更快）。

传输通道（3 路，容错不同路由器/AP 的组播策略）：
  - 组播  239.255.77.77:50052
  - 有限广播 255.255.255.255:50052
  - QUERY 应答：单播回发到请求方

包格式（大端序）:
    [0:4]   Magic    "ASDC"
    [4]     Version  0x01
    [5]     Type     0x01=ANNOUNCE  0x02=QUERY
    [6:8]   AudioPort uint16  （QUERY 可忽略）
    [8:10]  NameLen  uint16   （QUERY 可为 0）
    [10:]   Name     UTF-8
"""

import ipaddress
import socket
import struct
import threading

DISCOVERY_GROUP = "239.255.77.77"
DISCOVERY_PORT = 50052
MAGIC = b"ASDC"
VERSION = 0x01
TYPE_ANNOUNCE = 0x01
TYPE_QUERY = 0x02
TTL = 2


def build_announce(name: str, audio_port: int) -> bytes:
    name_bytes = name.encode("utf-8")
    assert len(name_bytes) <= 0xFFFF
    return (
        MAGIC
        + bytes([VERSION, TYPE_ANNOUNCE])
        + struct.pack(">H", audio_port)
        + struct.pack(">H", len(name_bytes))
        + name_bytes
    )


def build_query() -> bytes:
    """QUERY 包：固定最短长度（magic+ver+type+port+namelen=0）。"""
    return MAGIC + bytes([VERSION, TYPE_QUERY]) + struct.pack(">HH", 0, 0)


def parse_announce(data: bytes):
    """解析 ANNOUNCE 包，返回 (name, audio_port)；非 ANNOUNCE 或非法返回 None。"""
    if len(data) < 10 or data[0:4] != MAGIC:
        return None
    if data[4] != VERSION or data[5] != TYPE_ANNOUNCE:
        return None
    audio_port = struct.unpack(">H", data[6:8])[0]
    name_len = struct.unpack(">H", data[8:10])[0]
    if len(data) != 10 + name_len:
        return None
    try:
        name = data[10:10 + name_len].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return name, audio_port


def parse_query(data: bytes) -> bool:
    return (len(data) >= 6 and data[0:4] == MAGIC
            and data[4] == VERSION and data[5] == TYPE_QUERY)


def _local_ipv4_addresses() -> list:
    out = []
    try:
        import socket as _s
        hostname = _s.gethostname()
        for info in _s.getaddrinfo(hostname, None, _s.AF_INET):
            ip = info[4][0]
            if ip not in out and not ipaddress.ip_address(ip).is_loopback:
                out.append(ip)
    except OSError:
        pass
    return out


class Announcer:
    """电脑端：周期多路广播 ANNOUNCE；监听 QUERY 并立即单播应答。"""

    def __init__(self, name: str, audio_port: int,
                 group: str = DISCOVERY_GROUP, port: int = DISCOVERY_PORT,
                 interval: float = 2.0):
        self.name = name
        self.audio_port = audio_port
        self.group = group
        self.port = port
        self.interval = interval
        self.active = False

        # 发送 socket：组播 + 广播
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, TTL)
        # 发送广播前先构建一次包
        self._packet = build_announce(self.name, self.audio_port)

        # 监听 socket：收 QUERY（组播），单播应答
        self._listen = None
        try:
            ls = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ls.bind(("", self.port))
            ls.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                          struct.pack("4sl", socket.inet_aton(self.group), socket.INADDR_ANY))
            ls.settimeout(0.2)
            self._listen = ls
        except OSError:
            self._listen = None  # 端口被占用：只发不收（仍能响应不了 QUERY）

    @property
    def listening(self) -> bool:
        return self._listen is not None

    def start(self) -> None:
        if self.active:
            return
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="disco-announce")
        self._thread.start()

    def stop(self) -> None:
        self.active = False
        try:
            self._sock.close()
        except OSError:
            pass
        if self._listen is not None:
            try:
                self._listen.close()
            except OSError:
                pass
            self._listen = None

    # ---------- 发送 ----------
    def _send_to(self, addr) -> None:
        try:
            self._sock.sendto(self._packet, addr)
        except OSError:
            pass

    def _announce_all(self) -> None:
        targets = [(self.group, self.port), ("255.255.255.255", self.port)]
        for ip in _local_ipv4_addresses():
            # 定向广播（近似 /24；真实子网掩码难以跨平台获取）
            parts = ip.split(".")
            target = ".".join(parts[:3]) + ".255"
            if target != ip:
                targets.append((target, self.port))
        for t in targets:
            self._send_to(t)

    def _reply_query(self, peer_ip: str) -> None:
        try:
            self._sock.sendto(self._packet, (peer_ip, self.port))
        except OSError:
            pass

    # ---------- 主循环 ----------
    def _run(self) -> None:
        poll_interval = min(0.2, self.interval / 4)
        last_send = 0.0
        import time
        while self.active:
            now = time.time()
            if now - last_send >= self.interval:
                self._announce_all()
                last_send = now
            if self._listen is not None:
                try:
                    data, addr = self._listen.recvfrom(1024)
                    if parse_query(data):
                        self._reply_query(addr[0])
                except socket.timeout:
                    pass
                except OSError:
                    return
            time.sleep(poll_interval)


def main() -> None:  # 供手动测试: python -m desktop.discovery
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=socket.gethostname())
    ap.add_argument("--port", type=int, default=50051)
    args = ap.parse_args()
    a = Announcer(args.name, args.port)
    a.start()
    print(f"Announcing '{a.name}' 组播+广播 {a.group}:{a.port}（监听 QUERY: {a.listening}）... Ctrl+C 停止")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        a.stop()


if __name__ == "__main__":
    main()
