"""电脑端主界面（ttkbootstrap 风格）：接收控制、设备发现、状态显示、音量调节。

运行: python -m desktop  （或 python -m desktop.gui）
"""

import ipaddress
import logging
import socket
import threading
import time
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import PRIMARY, SUCCESS

import pystray
import sounddevice as sd
from PIL import Image, ImageDraw

from .bt_sink import (connect as bt_connect, disconnect as bt_disconnect,
                      helper_available as bt_helper_available,
                      list_devices as bt_list_devices,
                      current_device_name as bt_current_device_name,
                      service_running as bt_service_running)
from .audio_tools import dedupe_devices, play_test_tone
from .discovery import Announcer
from .player import AudioPlayer
from .protocol import DEFAULT_PORT
from .receiver import AudioReceiver

log = logging.getLogger("soundbeam.gui")


def make_tray_image() -> Image.Image:
    """用 Pillow 绘制托盘图标：蓝底圆角方块 + 白色均衡器竖条。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(31, 111, 235, 255))
    bars = [
        (16, 26, 22, 46),
        (29, 18, 35, 54),
        (42, 26, 48, 46),
    ]
    for x0, y0, x1, y1 in bars:
        d.rounded_rectangle((x0, y0, x1, y1), radius=3, fill=(255, 255, 255, 255))
    return img


def local_ipv4_addresses() -> list:
    """列出本机所有非回环 IPv4 地址。"""
    out = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ipaddress.ip_address(ip).is_loopback:
                out.append(ip)
    except OSError:
        pass
    return out


def output_devices() -> list:
    """列出可播放的输出设备，标注类型与驱动；去重（同名物理设备优先保留 WASAPI）。"""
    try:
        devices = sd.query_devices()
        apis = sd.query_hostapis()
        candidates = []
        for i, d in enumerate(devices):
            if d["max_output_channels"] <= 0:
                continue
            if d["max_input_channels"] > 0:
                tag = "输入+输出"
            else:
                tag = "输出"
            host = apis[d["hostapi"]]["name"] if 0 <= d["hostapi"] < len(apis) else "?"
            candidates.append((f"[{tag} · {host}] {d['name']}", i, host))
        # 默认列表首项是「系统默认」；去重后插入到最前，保持可选
        deduped = dedupe_devices(candidates)
        return [("系统默认输出设备（优先 WASAPI）", None)] + deduped
    except Exception:  # noqa: BLE001
        return [("系统默认输出设备（优先 WASAPI）", None)]


class SoundBeamApp(ttk.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("SoundBeam - 局域网音频投屏接收端")
        self.geometry("680x600")
        self.minsize(620, 480)

        self.receiver: AudioReceiver | None = None
        self.player: AudioPlayer | None = None
        self.announcer: Announcer | None = None
        self._tray: pystray.Icon | None = None
        self._state_epoch = 0
        self._last_remote = None
        self._connected_announced = False

        self._build_ui()
        # 启动即创建系统托盘图标（右键菜单可关闭/恢复窗口），无需先点窗口关闭
        self.after(200, self._start_tray)
        self.after(100, self._tick)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        pad = {"padx": 14, "pady": 6}
        font_h = ("Microsoft YaHei UI", 18, "bold")
        font_sub = ("Microsoft YaHei UI", 10)

        # 可滚动容器：小屏幕也能滚动访问所有面板
        self.scroll = ttk.ScrolledFrame(self, autohide=True)
        self.scroll.pack(fill="both", expand=True)

        # 标题区
        head = ttk.Frame(self.scroll)
        head.pack(fill="x", **pad)
        ttk.Label(head, text="🎧 SoundBeam", font=font_h).pack(anchor="w")
        ttk.Label(head, text="手机音频 → 局域网 → 电脑音箱",
                  font=font_sub, bootstyle="secondary").pack(anchor="w")

        # 顶部：端口 + 启停按钮
        top = ttk.Labelframe(self.scroll, text="接收", padding=10)
        top.pack(fill="x", **pad)
        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Label(row, text="监听端口:").pack(side="left")
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(row, textvariable=self.port_var, width=8).pack(side="left", padx=6)
        self.btn_toggle = ttk.Button(row, text="开始接收", bootstyle=PRIMARY,
                                     command=self._toggle)
        self.btn_toggle.pack(side="left", padx=10)
        ips = local_ipv4_addresses()
        ip_text = ", ".join(ips) if ips else "未检测到局域网 IP"
        ttk.Label(row, text=f"本机 IP：{ip_text}", bootstyle="info",
                  font=("", 10, "bold")).pack(side="right")

        # 广播开关（开关式，选中状态一目了然）
        self.bcast_var = tk.BooleanVar(value=True)
        bc = ttk.Checkbutton(
            self.scroll, text="允许被手机发现（广播本机，连接后自动关闭）",
            variable=self.bcast_var, command=self._on_bcast_toggle,
            bootstyle="success-round-toggle")
        bc.pack(fill="x", padx=14, pady=4)

        # 输出设备 + 音量
        devf = ttk.Labelframe(self.scroll, text="输出", padding=10)
        devf.pack(fill="x", **pad)
        dev_row = ttk.Frame(devf)
        dev_row.pack(fill="x", pady=2)
        ttk.Label(dev_row, text="设备:").pack(side="left")
        self.dev_combo = ttk.Combobox(dev_row, state="readonly", width=42)
        self._devices = output_devices()
        if self._devices:
            self.dev_combo["values"] = [label for label, _ in self._devices]
            self.dev_combo.current(0)
        else:
            self.dev_combo["values"] = ["（未找到输出设备）"]
        self.dev_combo.pack(side="left", padx=8)
        self.dev_combo.bind("<<ComboboxSelected>>", self._on_device_change)
        ttk.Button(dev_row, text="试听", width=6,
                   command=self._test_output).pack(side="left", padx=4)

        vol_row = ttk.Frame(devf)
        vol_row.pack(fill="x", pady=(6, 2))
        ttk.Label(vol_row, text="音量:").pack(side="left")
        self.vol_var = tk.DoubleVar(value=100)
        vol_scale = ttk.Scale(vol_row, from_=0, to=200, variable=self.vol_var,
                              command=self._on_volume, length=280)
        vol_scale.pack(side="left", padx=8)
        self.vol_label = ttk.Label(vol_row, text="100%", width=6)
        self.vol_label.pack(side="left")

        # 蓝牙接收（手机免装 App，Windows 原生 A2DP Sink）
        btf = ttk.Labelframe(self.scroll, text="蓝牙接收（手机免装 App）", padding=10)
        btf.pack(fill="x", **pad)
        if bt_helper_available():
            ttk.Label(btf, text="先让手机与电脑在 Windows 蓝牙设置中配对，再扫描并连接；"
                                "连接后手机音频会直接从电脑默认扬声器播出。",
                      bootstyle="secondary", wraplength=560).pack(anchor="w")
            bt_row = ttk.Frame(btf)
            bt_row.pack(fill="x", pady=(8, 2))
            self.btn_bt_scan = ttk.Button(bt_row, text="扫描手机", command=self._bt_scan)
            self.btn_bt_scan.pack(side="left")
            self.bt_combo = ttk.Combobox(bt_row, state="readonly", width=20)
            self._bt_devices = []
            self.bt_combo.set("（点击扫描）")
            self.bt_combo.pack(side="left", padx=8)
            # 连接/断开合并为一个切换按钮，避免一行放不下被挤出版面
            self.btn_bt_conn = ttk.Button(bt_row, text="连接", bootstyle=PRIMARY,
                                          command=self._bt_toggle_connect)
            self.btn_bt_conn.pack(side="left", padx=4)
            self.bt_state_var = tk.StringVar(value="")
            ttk.Label(btf, textvariable=self.bt_state_var,
                      bootstyle="secondary").pack(anchor="w", pady=(4, 0))
            # 启动时检测蓝牙接收服务：若已在运行（上次未正常退出），直接进入
            # 「已连接」状态并显示断开按钮，不再重复启动
            self.after(200, self._bt_refresh_from_service)
        else:
            ttk.Label(btf, text="未找到 bthelper.exe（打包发布版已内置；源码版需先编译 "
                                "desktop/bt 并放置到 desktop/bthelper.exe）。",
                      bootstyle="secondary", wraplength=560).pack(anchor="w")

        # 状态
        status = ttk.Labelframe(self.scroll, text="状态", padding=10)
        status.pack(fill="x", **pad)
        self.state_var = tk.StringVar(value="● 空闲")
        self.stats_var = tk.StringVar(value="等待开始接收…")
        self.state_label = ttk.Label(status, textvariable=self.state_var,
                                     font=("Microsoft YaHei UI", 13, "bold"))
        self.state_label.pack(anchor="w", pady=(0, 4))
        ttk.Label(status, textvariable=self.stats_var,
                  bootstyle="secondary", wraplength=540).pack(anchor="w")

        # 日志
        logf = ttk.Labelframe(self.scroll, text="日志", padding=8)
        logf.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(logf, height=8, state="disabled", wrap="word",
                                bg="#ffffff", font=("Consolas", 9), relief="flat")
        self.log_text.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {line}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ---------------- 广播（被发现） ----------------
    def _on_bcast_toggle(self) -> None:
        if self.bcast_var.get():
            self._start_announcer()
        else:
            self._stop_announcer()

    def _start_announcer(self) -> None:
        if self.announcer is not None:
            return
        port = DEFAULT_PORT
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            pass
        self.announcer = Announcer(socket.gethostname(), port)
        self.announcer.start()
        self._log(f"广播已开启：手机端可自动发现本机（音频端口 {port}）"
                  + ("" if self.announcer.listening else "（发现端口被占用，仅周期广播）"))

    def _stop_announcer_logged(self) -> None:
        self._stop_announcer()
        self._log("广播已关闭")

    def _stop_announcer(self) -> None:
        if self.announcer is not None:
            self.announcer.stop()
            self.announcer = None

    # ---------------- 控制 ----------------
    def _toggle(self) -> None:
        if self.receiver is None:
            self._start_receiving()
        else:
            self._stop_receiving()

    def _start_receiving(self) -> None:
        try:
            port = int(self.port_var.get().strip())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            self._log("端口无效，请输入 1-65535 的整数")
            return
        try:
            self.receiver = AudioReceiver(port)
            self.receiver.start()
        except OSError as exc:
            self._log(f"端口 {port} 绑定失败：{exc}（可能已被占用）")
            self.receiver = None
            return
        self._state_epoch += 1
        epoch = self._state_epoch
        self._connected_announced = False
        self.btn_toggle.configure(text="停止接收")
        self.state_var.set("● 等待手机连接…")
        self.stats_var.set(f"UDP 端口 {port} 已监听，手机端可扫描自动发现或手动输入 IP")
        self._log(f"开始接收：UDP {port}")
        if self.bcast_var.get():
            self._stop_announcer()
            self._start_announcer()

        threading.Thread(target=self._auto_start_player, args=(epoch,),
                         daemon=True, name="ensure-player").start()

    def _stop_receiving(self) -> None:
        self._state_epoch += 1
        if self.player is not None:
            self.player.stop()
            self.player = None
        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None
        self._last_remote = None
        self.btn_toggle.configure(text="开始接收")
        self.state_var.set("● 空闲")
        self.stats_var.set("已停止")
        self._log("已停止接收")
        if self.bcast_var.get() and self.announcer is None:
            self._start_announcer()

    def _auto_start_player(self, epoch: int) -> None:
        """等待 START 包拿到音频参数后自动启动播放器。"""
        while epoch == self._state_epoch and self.receiver is not None:
            params = self.receiver.audio_params()
            if params is not None and self.player is None:
                sr, ch = params
                self.after(0, lambda: self._start_player(sr, ch))
                return
            time.sleep(0.05)

    def _selected_device(self) -> int | None:
        selection = self.dev_combo.get()
        for label, idx in self._devices or []:
            if label == selection:
                return idx
        return None

    def _on_device_change(self, _event=None) -> None:
        dev_idx = self._selected_device()
        if self.player is not None and self.player.playing:
            if self.player.set_device(dev_idx):
                self._apply_volume()
                self._log(f"输出设备已切换：{self.dev_combo.get()}（音量 {int(self.vol_var.get())}%）")
            else:
                self._log("设备切换失败，已回退默认输出")
        else:
            self._log(f"输出设备将用于下次播放：{self.dev_combo.get()}")

    def _apply_volume(self) -> None:
        v = float(self.vol_var.get())
        self.vol_label.configure(text=f"{int(v)}%")
        if self.player is not None:
            self.player.set_volume(v / 100.0)

    # ---------------- 试听（测试音效） ----------------
    def _test_output(self) -> None:
        """对当前选中的输出设备播放一段测试音，确认设备发声。"""
        dev = self._selected_device()
        label = self.dev_combo.get()
        self._log(f"试听输出设备：{label}…")
        threading.Thread(
            target=self._play_test_on, args=(dev,), daemon=True,
            name="test-output").start()

    def _play_test_on(self, dev: int | None) -> None:
        ok, desc = play_test_tone(dev, volume=float(self.vol_var.get()) / 100.0)
        self.after(0, lambda: self._log(
            f"输出试听完成（{desc}）" if ok else f"输出试听失败：{desc}"))

    def _start_player(self, sr: int, ch: int) -> None:
        if self.receiver is None:
            return
        self.player = AudioPlayer(self.receiver, device=self._selected_device())
        self.player.set_volume(self.vol_var.get() / 100.0)
        ok = self.player.start()
        addr = self.receiver.remote_addr
        self.state_var.set(f"● 正在播放来自 {addr[0] if addr else '手机'} 的音频")
        self._log(f"播放器启动：{sr} Hz / {ch} 声道" + (f"（设备 {self.dev_combo.get()}）" if ok else ""))

    def _on_volume(self, _val: str) -> None:
        v = int(float(_val))
        self.vol_label.configure(text=f"{v}%")
        if self.player is not None:
            self.player.set_volume(v / 100.0)

    # ---------------- 蓝牙接收（手机免装 App） ----------------
    def _bt_scan(self) -> None:
        self.btn_bt_scan.configure(state="disabled")
        self.bt_state_var.set("正在扫描已配对的蓝牙设备…")
        threading.Thread(target=self._bt_scan_worker, daemon=True).start()

    def _bt_scan_worker(self) -> None:
        try:
            found = bt_list_devices()
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._bt_scan_done([], str(exc)))
            return
        self.after(0, lambda: self._bt_scan_done(found, None))

    def _bt_scan_done(self, found: list, err: str | None) -> None:
        self.btn_bt_scan.configure(state="normal")
        if err:
            self._log(f"蓝牙扫描失败：{err}")
            self.bt_state_var.set(f"扫描失败：{err}")
            return
        self._bt_devices = found
        if found:
            self.bt_combo["values"] = [f"{name}" for name, _ in found]
            self.bt_combo.current(0)
            self.bt_state_var.set(f"发现 {len(found)} 个可连接设备，选择后点连接")
        else:
            self.bt_combo["values"] = []
            self.bt_combo.set("未发现设备")
            self.bt_state_var.set("未发现可连接设备：请先让手机与电脑配对，并确保手机音频已输出到蓝牙")
            self._log("蓝牙扫描完成：没有发现可连接的音频设备")

    def _bt_refresh_from_service(self) -> None:
        """启动时检测蓝牙接收服务：已运行则不再启动，直接显示已连接设备 + 断开按钮。"""
        if bt_service_running():
            name = bt_current_device_name()
            label = f"已连接：{name}" if name else "已连接（设备名不可见）"
            if name and self._bt_devices:
                names = [n for n, _ in self._bt_devices]
                if name in names:
                    self.bt_combo.set(name)
            self.btn_bt_conn.configure(text="断开")
            self.bt_state_var.set(
                f"蓝牙接收服务运行中，{label}；点「断开」可停止并在电脑上关闭手机声音")
            self._log(f"检测到蓝牙接收服务已在运行（{label}），未重复启动")
        else:
            self.btn_bt_conn.configure(text="连接")
            self.bt_state_var.set("蓝牙接收服务未启动：扫描并连接手机后即可播放")

    def _bt_toggle_connect(self) -> None:
        """连接/断开切换：服务在运行（含孤儿进程）则断开，否则连接所选手机。"""
        if bt_service_running():
            self._bt_disconnect()
            return
        if not self._bt_devices or not self.bt_combo.get():
            self._bt_scan()
            return
        idx = self.bt_combo.current()
        if idx < 0 or idx >= len(self._bt_devices):
            return
        name, dev_id = self._bt_devices[idx]
        self.bt_state_var.set(f"正在连接 {name}（约 2 秒）…")
        self.btn_bt_conn.configure(state="disabled")
        threading.Thread(target=self._bt_connect_worker, args=(dev_id, name),
                         daemon=True).start()

    def _bt_connect_worker(self, dev_id: str, name: str) -> None:
        try:
            status = bt_connect(dev_id, name)
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._bt_connect_done(
                f"连接失败：{exc}", ok=False))
            return
        self.after(0, lambda: self._bt_connect_done(status, ok=True))

    def _bt_connect_done(self, status: str, ok: bool) -> None:
        self.btn_bt_conn.configure(state="normal")
        if ok:
            self.btn_bt_conn.configure(text="断开")
            self.bt_state_var.set("已连接：手机音频正从电脑扬声器播放，点「断开」可停止")
            self._log(f"蓝牙接收已连接（状态 {status}）")
        else:
            self.bt_state_var.set(status)
            self._log(status)

    def _bt_disconnect(self) -> None:
        """断开蓝牙接收。断开动作放后台线程执行，UI 立即反馈；
        否则等待 bthelper 退出/链路拆除会让界面卡住几秒。"""
        self.btn_bt_conn.configure(text="连接")
        self.bt_state_var.set("已断开：正在停止蓝牙接收服务…")
        self._log("正在断开蓝牙接收…")

        def worker() -> None:
            try:
                bt_disconnect()
            except Exception as exc:  # noqa: BLE001
                self._log(f"断开失败：{exc}")
            else:
                self._log("蓝牙接收已断开，服务已关闭")
            self.after(0, lambda: self.bt_state_var.set(
                "已断开：蓝牙接收服务已停止，手机声音不再从电脑播出"))

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- 状态刷新 ----------------
    def _tick(self) -> None:
        r = self.receiver
        if r is not None:
            if r.remote_addr != self._last_remote:
                if r.remote_addr is not None:
                    self._log(f"已收到来自 {r.remote_addr[0]}:{r.remote_addr[1]} 的 SoundBeam 数据包")
                self._last_remote = r.remote_addr
            if r.remote_addr is not None and r.audio_params() is not None and not self._connected_announced:
                self._connected_announced = True
                self.state_var.set(f"● 已连接 {r.remote_addr[0]}")
                self.state_label.configure(bootstyle=SUCCESS)
                if self.bcast_var.get() and self.announcer is not None:
                    self.bcast_var.set(False)
                    self._stop_announcer_logged()
            if r.packets_per_sec:
                parts = [
                    f"包速率 {r.packets_per_sec}/s | 丢包率 {r.loss_percent:.1f}%",
                    f"码率 {r.rate_kbps:.0f} kbps",
                ]
                if self.player is not None:
                    lvl = self.player.level_db
                    parts.append(
                        f"音量电平 {lvl:.0f} dB" if lvl > -80 else "音量电平 静音"
                    )
                self.stats_var.set(" | ".join(parts))
            if r.remote_addr is None:
                self.state_var.set("● 等待手机连接…")
        self.after(100, self._tick)

    # ---------------- 退出与系统托盘 ----------------
    def _on_close(self) -> None:
        """点击窗口关闭按钮：隐藏窗口、缩到系统托盘，后台继续接收。"""
        self.withdraw()
        self._start_tray()

    def _start_tray(self) -> None:
        """启动系统托盘图标（右键菜单：打开窗口 / 关闭）。"""
        if self._tray is not None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("打开窗口", lambda icon, item: self.after(0, self._show_window),
                             default=True),
            pystray.MenuItem("关闭", lambda icon, item: self.after(0, self._quit_app)),
        )
        self._tray = pystray.Icon("soundbeam", make_tray_image(),
                                  "SoundBeam - 局域网音频投屏接收端", menu=menu)
        self._tray.run_detached()

    def _show_window(self) -> None:
        """从托盘恢复主窗口。"""
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_app(self) -> None:
        """托盘菜单「关闭」：清理所有资源后真正退出。"""
        self._stop_receiving()
        self._stop_announcer()
        # 先终止常驻的蓝牙 helper 进程，否则它占用 exe 解包目录，退出时会弹
        # 「Failed to remove temporary directory」警告
        bt_disconnect()
        self._log("已退出")
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
        self.destroy()


def main() -> None:
    app = SoundBeamApp()
    app.mainloop()


if __name__ == "__main__":
    main()
