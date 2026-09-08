package com.soundbeam

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper

/**
 * 前台服务：MediaProjection 授权下用 AudioPlaybackCapture 捕获系统音频
 * （正在播放的任意 App 声音），组包后经 UdpSender 实时发送到电脑。
 */
class AudioCaptureService : Service() {

    companion object {
        const val ACTION_START = "com.soundbeam.action.START"
        const val ACTION_STOP = "com.soundbeam.action.STOP"
        const val ACTION_SET_MUTE = "com.soundbeam.action.SET_MUTE"
        const val EXTRA_IP = "ip"
        const val EXTRA_PORT = "port"
        const val EXTRA_RESULT_CODE = "resultCode"
        const val EXTRA_RESULT_DATA = "resultData"
        const val EXTRA_MUTE_PHONE = "mutePhone"
        private const val CHANNEL_ID = "soundbeam_capture"
        private const val NOTIFICATION_ID = 1

        @Volatile
        var isRunning: Boolean = false
            private set

        /** 最近一次发送/捕获错误，供界面轮询展示 */
        @Volatile
        var lastError: String? = null
            private set
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private val audioManager by lazy { getSystemService(AudioManager::class.java) }
    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: android.hardware.display.VirtualDisplay? = null
    private var imageReader: android.media.ImageReader? = null
    private var audioRecord: AudioRecord? = null
    private var savedMediaVolume = -1
    private var sender: UdpSender? = null
    private var captureThread: Thread? = null
    private var targetIp: String = ""
    private var targetPort: Int = 50051
    private var stopping = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopSelf()
            ACTION_START -> startCapture(intent)
            ACTION_SET_MUTE -> setMutePhone(intent.getBooleanExtra(EXTRA_MUTE_PHONE, false))
        }
        return START_NOT_STICKY
    }

    /** 播放中实时开关「关闭手机声音」：静音/恢复媒体流（服务存活期间可随时调用）。 */
    private fun setMutePhone(mute: Boolean) {
        if (mute && savedMediaVolume < 0) {
            savedMediaVolume = audioManager.getStreamVolume(AudioManager.STREAM_MUSIC)
            audioManager.setStreamVolume(AudioManager.STREAM_MUSIC, 0, 0)
        } else if (!mute && savedMediaVolume >= 0) {
            audioManager.setStreamVolume(AudioManager.STREAM_MUSIC, savedMediaVolume, 0)
            savedMediaVolume = -1
        }
    }

    // ---------------- 捕获启动 ----------------
    private fun startCapture(intent: Intent) {
        val ip = intent.getStringExtra(EXTRA_IP)
        val port = intent.getIntExtra(EXTRA_PORT, 50051)
        val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
        @Suppress("DEPRECATION")
        val resultData: Intent? = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
        } else {
            intent.getParcelableExtra(EXTRA_RESULT_DATA)
        }
        if (ip.isNullOrBlank() || resultData == null) {
            stopSelf()
            return
        }
        targetIp = ip
        targetPort = port
        stopping = false
        lastError = null

        ensureChannel()
        startForeground(NOTIFICATION_ID, buildNotification(),
            if (Build.VERSION.SDK_INT >= 29)
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION else 0)

        isRunning = true
        val mpm = getSystemService(MediaProjectionManager::class.java)
        val proj = mpm.getMediaProjection(resultCode, resultData)
        if (proj == null) {
            stopSelf()
            return
        }
        mediaProjection = proj

        // 创建 1×1 虚拟投影屏使 MediaProjection 真实激活：部分设备（尤其国内 OEM）
        // 的 AudioPlaybackCapture 依赖投影激活，否则捕获到的 PCM 全是静音。
        try {
            val ir = android.media.ImageReader.newInstance(
                1, 1, android.graphics.PixelFormat.RGBA_8888, 2)
            virtualDisplay = proj.createVirtualDisplay(
                "SoundBeamCapture",
                1, 1, resources.displayMetrics.densityDpi,
                android.hardware.display.DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                ir.surface, null, null,
            )
            imageReader = ir
        } catch (e: Exception) {
            // 个别设备可能不支持虚拟屏，不影响继续尝试捕获
            virtualDisplay = null
        }

        // Android 14+ 要求注册 MediaProjection.Callback 处理投影生命周期
        if (Build.VERSION.SDK_INT >= 34) {
            proj.registerCallback(object : MediaProjection.Callback() {
                override fun onCapturedContentResize(width: Int, height: Int) {
                    // audio-only 虚拟屏无需调整尺寸
                }

                override fun onCapturedContentVisibilityChanged(isVisible: Boolean) {
                    // 音频捕获不受内容可见性影响，保持发送
                }

                override fun onStop() {
                    // 投影被系统终止（如用户从状态栏关闭）→ 停止服务
                    mainHandler.post { stopSelf() }
                }
            }, mainHandler)
        }

        val config = AudioPlaybackCaptureConfiguration.Builder(proj)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .build()

        // 「关闭手机声音」：静音媒体流，手机不再外放。
        // 注意：不同机型的捕获可能跟随媒体音量（系统混音层面共享）——
        // 若开启后电脑声音也变小/消失，即该机型限制，无法在非 root 下绕过。
        val mutePhone = intent.getBooleanExtra(EXTRA_MUTE_PHONE, false)
        setMutePhone(mutePhone)

        val format = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(Protocol.SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_IN_STEREO)
            .build()

        val record = try {
            // 缓冲取"系统最小可用缓冲"与 3 包(60ms)中的较大者：足够覆盖突发的
            // 同时尽量浅，减小底层 FIFO 造成的捕获延迟
            val minBuf = AudioRecord.getMinBufferSize(
                Protocol.SAMPLE_RATE, AudioFormat.CHANNEL_IN_STEREO,
                AudioFormat.ENCODING_PCM_16BIT)
            AudioRecord.Builder()
                .setAudioFormat(format)
                .setBufferSizeInBytes(maxOf(minBuf, Protocol.PACKET_BYTES * 3))
                .setAudioPlaybackCaptureConfig(config)
                .build()
        } catch (e: Exception) {
            stopSelf()
            return
        }
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            stopSelf()
            return
        }
        audioRecord = record
        record.startRecording()

        val sender = try {
            UdpSender(ip, port) { err ->
                lastError = err
            }
        } catch (e: IllegalArgumentException) {
            lastError = e.message
            stopSelf()
            return
        }
        this.sender = sender
        sender.start()
        // 流起始包
        sender.send(Protocol.buildPacket(Protocol.FLAG_START, 0, 0L))

        captureThread = Thread({
            val buf = ByteArray(Protocol.PACKET_BYTES)
            var seq = 0
            var ts = 0L
            var packets = 0
            try {
                while (!Thread.currentThread().isInterrupted) {
                    val n = record.read(buf, 0, buf.size, AudioRecord.READ_BLOCKING)
                    if (n <= 0) break
                    if (n != Protocol.PACKET_BYTES) continue // 非整包，丢弃（保持 20ms 边界）
                    sender.send(Protocol.buildPacket(Protocol.FLAG_DATA, seq, ts, payload = buf))
                    seq = (seq + 1) and 0xFFFF
                    ts += Protocol.PACKET_MS
                    packets++
                    if (packets % (1000 / Protocol.PACKET_MS) == 0) {
                        sender.send(Protocol.buildPacket(Protocol.FLAG_HEARTBEAT, seq, ts))
                    }
                }
            } catch (e: Exception) {
                // 录音被停止等，忽略
            }
            mainHandler.post { stopSelf() }
        }, "audio-capture-reader")
        captureThread?.start()
    }

    // ---------------- 停止与清理 ----------------
    override fun onDestroy() {
        cleanup()
        super.onDestroy()
    }

    private fun cleanup() {
        if (stopping) return
        stopping = true
        isRunning = false
        // 恢复「关闭手机声音」前的媒体音量
        setMutePhone(false)
        captureThread?.interrupt()
        captureThread = null
        sender?.let {
            it.send(Protocol.buildPacket(Protocol.FLAG_STOP, 0, 0L))
            it.shutdown()
        }
        sender = null
        try {
            audioRecord?.stop()
        } catch (_: Exception) {
        }
        audioRecord?.release()
        audioRecord = null
        virtualDisplay?.release()
        virtualDisplay = null
        imageReader?.close()
        imageReader = null
        mediaProjection?.stop()
        mediaProjection = null
    }

    // ---------------- 通知 ----------------
    private fun ensureChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID, "音频传输", NotificationManager.IMPORTANCE_LOW)
        nm.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        val text = "正在发送音频到 $targetIp:$targetPort"
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("SoundBeam")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setOngoing(true)
            .build()
    }
}
