# SoundBeam

把手机 / 平板正在播放的音频，通过**局域网**实时传输到电脑上播放——音频"投屏"。类比 AirPlay 的音频回传场景，但走自研的局域网协议，Android → Windows。

| 手机端 | 电脑端 | 传输 | 延迟 |
|--------|--------|------|------|
| Android 10+，捕获系统正在播放的任意 App 音频（音乐、视频、游戏） | Windows，Python + tkinter 图形界面，播放到指定音频输出设备 | UDP + PCM 裸流（48kHz / 16bit / 立体声，约 1.5 Mbps） | 约 100–200ms（含 80ms 抖动缓冲） |

## 架构

```
┌─────────────┐  MediaProjection   ┌──────────────┐   UDP/PCM   ┌─────────────────────┐
│ Android App │  AudioPlaybackCapture │ 捕获+发送线程 │ ──────────▶ │ 电脑端 receiver      │
│ (Kotlin)    │   (PCM 48k/16bit/立体声) │              │             │ + 抖动缓冲 (80ms)     │ → 音箱
└─────────────┘   默认端口 50051    └──────────────┘             │ → sounddevice 播放    │
                                                                └─────────────────────┘
```

## 功能

- **自动发现电脑**：手机端一键扫描，自动列出局域网内可连接的电脑。电脑端开启「允许被手机发现」后，通过**组播 + 广播 + QUERY 应答**三条通道周期广播自身（兼容不同路由器 / AP 的组播策略），手机端扫描时即时应答；连接成功后双端自动停止扫描
- **手动连接**：扫描不到（如路由器开启了 AP 隔离）时，可手动输入电脑 IP 和端口
- **关闭手机声音**（Android 12+）：手机端开关，捕获照常进行但手机不再外放
- **输出设备实时切换**：播放中可直接切换扬声器 / 耳机，无需停止接收，音量保持
- **实时状态**：包速率、丢包率、码率（kbps）、音量电平（dB），WASAPI 低延迟输出
- **系统托盘常驻**：关闭窗口后缩到系统托盘，后台继续接收
- **打包发布**：一键打出单文件 `SoundBeam.exe`（免装 Python，约 66MB）

### 手机免装 App 模式

以下方式手机端**无需安装本软件**：

- **蓝牙接收**：Windows 10 1903+ 原生 A2DP Sink（`desktop/bt_sink.py` + `bthelper.exe`）。手机与电脑在系统蓝牙设置中配对后，电脑端一键连接，手机音频直接从电脑扬声器播出

## 目录结构

```
desktop/          电脑端（Python 3.10+）
  ├─ gui.py           主界面（ttkbootstrap + 系统托盘）
  ├─ receiver.py      接收端 + 抖动缓冲（丢包补静音）
  ├─ player.py        播放器（sounddevice）
  ├─ discovery.py     局域网设备发现（组播 / 广播 / 应答）
  ├─ protocol.py      音频流协议封包 / 解析
  ├─ bt_sink.py       蓝牙接收（A2DP Sink）
  ├─ bt/              蓝牙 helper 的 C# 源码（编译出 bthelper.exe）
  ├─ mock_phone.py    模拟手机端（无手机时自测链路）
  ├─ selftest.py      端到端自检
  └─ test_protocol.py 协议单元测试
android/          手机端（Kotlin + Gradle，仅依赖 AndroidX / Material 官方库，无网络 / 三方 SDK）
docs/protocol.md  传输协议规范（音频流协议 v1 + 设备发现 v1）
dist/             打包产物（SoundBeam.exe）
build_exe.bat     电脑端一键打包脚本
```

## 电脑端使用

### 直接下载 exe（推荐）

**无需 Python、无需编译**，去 [Releases 页面](https://github.com/Rewmington/SoundBeam/releases) 下载最新版 `SoundBeam.exe`（约 66MB，已内置蓝牙 `bthelper.exe`），双击即用。每次发新版本，云端会通过 GitHub Actions 自动打好 exe 并挂到 Release 上。

> 想自己动手改源码的，才需要看下面的编译方式。

### 环境要求

- Windows 10 / 11 + Python 3.10+（3.14 已验证）
- 安装依赖：`pip install -r desktop/requirements.txt`（sounddevice、numpy、ttkbootstrap、pystray）

### 启动

```bash
python -m desktop
```

界面说明：

- 顶部显示**本机 IP**——手机端需要填写它
- 默认监听 UDP 50051 端口，可修改
- 选择音频输出设备、调节音量
- 点「开始接收」，等待手机连接
- 「允许被手机发现」默认开启，连接成功后自动关闭广播

### 打包为 exe（免装 Python）

> 普通用户不用做这步，直接去 Releases 下载即可。这里给想改源码的人。

在项目根目录运行 `build_exe.bat`，产物为单文件 `dist\SoundBeam.exe`（约 66MB），拷到任意 Windows 电脑直接运行，无需安装 Python。注意：打包前需先编译出 `desktop/bthelper.exe`（见下方「编译蓝牙 helper」），它会随 exe 内置。

#### 自动发布（GitHub Actions）

本仓库已配置 `.github/workflows/release.yml`：打一个 tag 并推送，云端会自动完成「编译 bthelper + 打包 exe + 构建 apk + 创建 Release」整套流程，无需本地环境：

```bash
git tag v0.1.0 && git push origin v0.1.0
```

#### 编译蓝牙 helper（bthelper.exe，源码版才需要）

```bash
dotnet publish desktop/bt/bthelper.csproj -c Release -r win-x64 --self-contained true
# 把生成的 bthelper.exe 放到 desktop/ 目录
```

### 无手机自测（可选）

```bash
python -m desktop.selftest                                    # 端到端自检（协议单测 + 模拟发送 + 抖动缓冲统计）
python -m desktop.mock_phone --ip 127.0.0.1 --duration 5      # 模拟手机端发送 5s 正弦波
```

## 手机端使用

1. 去 [Releases 页面](https://github.com/Rewmington/SoundBeam/releases) 下载 `app-debug.apk` 安装（或按下方「构建」自己打包）
2. 打开 App
3. **自动发现**：点「扫描局域网内的电脑」（电脑端需已开启「允许被手机发现」并点开始接收），在列表中选择目标电脑，IP / 端口自动填入；扫描不到时手动填写电脑 IP 和端口
4. 可打开「关闭手机声音」（Android 12+，捕获照常、手机不外放）
5. 点「开始传输」→ 系统弹出**屏幕捕获授权**框 → 选「立即开始 / 允许」
6. 在手机播放任意音频，电脑即开始出声；连接成功后双端自动停止扫描

> iOS 说明：本版本仅支持 Android。iOS 不允许第三方 App 捕获全局音频，无法做实时回传。

### 构建

> 普通用户直接去 Releases 下载 `app-debug.apk` 即可，无需 JDK / Android SDK。这里给想改源码的人。

需要 Android SDK（platform 35 / build-tools）+ JDK 17：

```bash
cd android
./gradlew assembleDebug     # 产物: app/build/outputs/apk/debug/app-debug.apk
```

## 注意事项与限制

- **Android 10+ 才能捕获系统音频**；Android 14 起每次点「开始传输」都会要求重新授权（系统限制）
- 显式声明了 `FLAG_SECURE` 或禁止捕获（`ALLOW_CAPTURE_BY_NONE`）的 App，其音频无法被捕获
- 传输的是实时的**未加密 PCM**，仅限**可信局域网**使用；跨网段、公网不可用且不建议
- 丢包时会自动补静音，可能出现轻微爆音 / 卡顿；WiFi 信号差时建议降低播放音量或更换信道
- 蓝牙接收用的 `bthelper.exe` 打包发布版已内置；源码版需先编译 `desktop/bt` 并放置到 `desktop/bthelper.exe`

## 后续计划（v2 候选）

- **Opus 压缩**：弱网场景下降低码率
- 界面与稳定性打磨、正式版本发布

## 许可证

本项目基于 **Apache License 2.0** 许可发布，详见 [LICENSE](LICENSE)。
