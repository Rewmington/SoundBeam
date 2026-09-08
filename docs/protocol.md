# SoundBeam Stream Protocol v1

手机端 → 电脑端，UDP 单播，默认端口 **50051**。

## 设备发现（Discovery v1）

组播组 **239.255.77.77:50052**（与音频通道分离），用于手机端自动列出局域网内的电脑接收端：

- 电脑端（接收端）每隔 2s 向组播组发送 **ANNOUNCE** 包，直到被连接或广播被关闭
- 手机端加入组播组监听 ~2.5s，解析收到的 ANNOUNCE 去重展示，选中即停止扫描
- 连接成功后双端停止：电脑端收到音频包后自动停止广播（或手动关闭开关）

ANNOUNCE 包格式（大端序）：

| 偏移 | 大小 | 字段 | 说明 |
|------|------|------|------|
| 0 | 4 | Magic | 固定 `"ASDC"` |
| 4 | 1 | Version | `0x01` |
| 5 | 1 | Type | `0x01` = ANNOUNCE |
| 6 | 2 | AudioPort | uint16，音频接收端口 |
| 8 | 2 | NameLen | uint16 |
| 10 | N | Name | UTF-8 设备名 |

依赖：WiFi 组播需路由器支持（多数家用路由器默认开启；开启「AP 隔离」时会失效，此时用手动输入 IP）。

实现：电脑端 `desktop/discovery.py`；手机端 `Discovery.kt`。

## 音频包（SoundBeam Stream Protocol v1）

手机端 → 电脑端，UDP 单播，默认端口 **50051**。

## 包格式（大端序）

| 偏移 | 大小 | 字段 | 说明 |
|------|------|------|------|
| 0 | 2 | Magic | 固定 `0x4153`（"AS"） |
| 2 | 1 | Version | 固定 `0x01` |
| 3 | 1 | Flags | 见下 |
| 4 | 2 | Seq | uint16 包序号（循环计数，丢包检测依据） |
| 6 | 4 | Timestamp | uint32，流启动后的相对毫秒 |
| 10 | 1 | Channels | `1`=mono，`2`=stereo |
| 11 | 2 | SampleRate | uint16，Hz（当前实现固定 48000） |
| 13 | 2 | PayloadLen | uint16，载荷字节数 |
| 15 | N | Payload | PCM 16-bit 小端（LE），交错声道 |

### Flags

| bit | 值 | 含义 |
|-----|-----|------|
| 0 | 0x01 | START：流起始（空载荷），发送第一包 DATA 前发出 |
| 1 | 0x02 | STOP：流结束（空载荷） |
| 2 | 0x04 | DATA：载荷为 20ms PCM |
| 3 | 0x08 | HEARTBEAT：每 1s 一个（载荷可为空） |

## 时序

```
START → DATA × N → STOP
         │
         └─ 期间每 1s 一个 HEARTBEAT
```

## 参数

- 每 DATA 包 **20ms** 音频，48kHz 立体声时载荷 = 960 × 2ch × 2B = **3840 B**
- 码率 ≈ 1.536 Mbps

## 接收端行为（参考实现 desktop/receiver.py）

1. 收到 START 时重置流状态，记录采样率/声道
2. DATA 包按 seq 存入抖动缓冲，**目标延迟 80ms（4 包）**
3. 播放时若 seq 缺失且其后已有包在缓冲 → 判定丢包，补 20ms 静音并推进
4. 收到 STOP 停止本次流
5. 统计丢包率 = 补静音次数 / (实收 + 补静音)

## 实现参考

- 电脑端：`desktop/protocol.py`（Python）
- 手机端：`android/app/src/main/java/com/soundbeam/Protocol.kt`（Kotlin）
