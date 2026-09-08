"""蓝牙 A2DP Sink（手机免装 App）：封装 bthelper（C#，AudioPlaybackConnection API）。

原理：Windows 10 1903+ 原生支持蓝牙音频接收（AudioPlaybackConnection），
但必须由应用激活。连接建立后，Windows 把手机音频自动路由到系统默认输出。

关键：AudioPlaybackConnection 是进程内对象——bthelper 必须**常驻**持有连接，
断开由本模块终止 helper 进程实现（否则进程退出连接立即断开）。

使用前提：手机与电脑已在 Windows「蓝牙和其他设备」里配对。
"""

import json
import os
import subprocess
import sys
import tempfile

_HELPER_NAME = "bthelper.exe"

# 当前常驻的 bthelper 连接进程
_proc: subprocess.Popen | None = None

# 当前连接设备名（仅本进程持有连接时有效；跨进程用下面的状态文件）
_connected_name: str | None = None

# 供 Windows 之外平台占位（本模块仅 Windows 使用）
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _helper_path() -> str:
    """定位 bthelper.exe：打包后位于 PyInstaller 解包目录（兼容子目录放置）；
    开发时位于 desktop/ 与源码同目录。"""
    if getattr(sys, "_MEIPASS", None):
        root = sys._MEIPASS
        direct = os.path.join(root, _HELPER_NAME)
        if os.path.isfile(direct):
            return direct
        # 兼容旧打包把它放进子目录的情况
        for dirpath, _dirs, files in os.walk(root):
            if _HELPER_NAME in files:
                return os.path.join(dirpath, _HELPER_NAME)
        return direct
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _HELPER_NAME)


def helper_available() -> bool:
    return os.path.isfile(_helper_path())


def _run_once(*args: str) -> str:
    """一次性运行（list），返回 stdout；失败抛 RuntimeError。"""
    exe = _helper_path()
    if not helper_available():
        raise RuntimeError("bthelper.exe 缺失：请运行最新打包版 exe，或从编译产物复制到 desktop/")
    proc = subprocess.run([exe, *args], capture_output=True, text=True,
                          timeout=60, creationflags=_NO_WINDOW)  # CREATE_NO_WINDOW
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"bthelper 退出码 {proc.returncode}")
    return proc.stdout


def _state_path() -> str:
    """连接状态文件：%LOCALAPPDATA%/SoundBeam/bt_state.json（跨进程记住当前设备）。"""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    path = os.path.join(base, "SoundBeam", "bt_state.json")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        pass
    return path


def _save_state(name: str) -> None:
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump({"name": name}, f, ensure_ascii=False)
    except OSError:
        pass


def _clear_state() -> None:
    try:
        os.remove(_state_path())
    except OSError:
        pass


def _tasklist_bthelper() -> bool:
    """tasklist 查询是否有 bthelper.exe 进程在运行（不依赖本进程引用，可发现孤儿进程）。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_HELPER_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=_NO_WINDOW)
        return _HELPER_NAME in (out.stdout or "")
    except Exception:  # noqa: BLE001
        return False


def _kill_all_helpers() -> None:
    """强制结束所有 bthelper 进程（含孤儿进程），释放 A2DP 连接。"""
    try:
        subprocess.run(["taskkill", "/IM", _HELPER_NAME, "/F"],
                       capture_output=True, text=True, timeout=10,
                       creationflags=_NO_WINDOW)
    except Exception:  # noqa: BLE001
        pass


def service_running() -> bool:
    """蓝牙接收服务是否正在运行。

    服务 = 持有 A2DP 连接的 bthelper 常驻进程。只要系统里有该进程就算运行，
    无论它是不是本进程启动的（覆盖上次异常退出遗留的孤儿进程）。
    """
    return _tasklist_bthelper()


def current_device_name() -> str | None:
    """当前已连接设备名：本进程持有连接时用内存值，否则读状态文件。"""
    if _connected_name:
        return _connected_name
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            return json.load(f).get("name")
    except (OSError, ValueError, TypeError):
        return None


def list_devices() -> list:
    """列出可连接的蓝牙音频设备（已配对的手机）。"""
    out = _run_once("list")
    data = json.loads(out or "[]")
    return [(d.get("name") or "(未命名)", d.get("id")) for d in data if d.get("id")]


def connect(device_id: str, name: str | None = None) -> str:
    """与设备建立 A2DP 播放连接；helper 常驻持有连接，返回状态文本。"""
    global _proc, _connected_name
    disconnect()  # 先清理旧连接
    if not helper_available():
        raise RuntimeError("bthelper.exe 缺失：请运行最新打包版 exe")
    proc = subprocess.Popen(
        [_helper_path(), "connect", device_id],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, creationflags=_NO_WINDOW)
    line = proc.stdout.readline() if proc.stdout else ""
    data = json.loads(line or "{}")
    if not data.get("ok", False):
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"连接失败：{data.get('status', 'unknown')}")
    _proc = proc
    _connected_name = name
    if name:
        _save_state(name)
    return data.get("status", "?")


def disconnect() -> None:
    """断开蓝牙接收；优先让 helper 优雅关闭连接（AudioPlaybackConnection.Close()），
    超时再强杀兜底，最后清理所有残留 bthelper 进程。"""
    global _proc, _connected_name
    if _proc is not None:
        if _proc.poll() is None:
            try:
                # 发优雅断开信号：bthelper 收到后 Close() 连接并正常退出，
                # Windows 立即拆除 A2DP 链路（强杀会残留连接，断后仍出声）
                if _proc.stdin:
                    _proc.stdin.write("disconnect\n")
                    _proc.stdin.flush()
                _proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    _proc.terminate()
                    _proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    try:
                        _proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
        _proc = None
    _connected_name = None
    _kill_all_helpers()
    _clear_state()


def is_connected() -> bool:
    """当前是否存在蓝牙 A2DP 连接（含孤儿进程，与 service_running 一致）。"""
    return service_running()
