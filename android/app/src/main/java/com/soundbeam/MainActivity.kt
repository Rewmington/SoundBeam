package com.soundbeam

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.EditText
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import java.io.File

class MainActivity : AppCompatActivity() {

    companion object {
        private const val REQ_MEDIA_PROJECTION = 1001
        private const val REQ_NOTIFICATIONS = 1002
    }

    private lateinit var editIp: EditText
    private lateinit var editPort: EditText
    private lateinit var btnStart: Button
    private lateinit var btnStop: Button
    private lateinit var btnScan: Button
    private lateinit var switchMute: SwitchCompat
    private lateinit var tvStatus: TextView
    private lateinit var tvScanResult: TextView
    private lateinit var groupDevices: RadioGroup

    private val uiHandler = Handler(Looper.getMainLooper())
    private val statusPoll = object : Runnable {
        override fun run() {
            updateUiState()
            uiHandler.postDelayed(this, 500)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        editIp = findViewById(R.id.editIp)
        editPort = findViewById(R.id.editPort)
        btnStart = findViewById(R.id.btnStart)
        btnStop = findViewById(R.id.btnStop)
        btnScan = findViewById(R.id.btnScan)
        switchMute = findViewById(R.id.switchMute)
        tvStatus = findViewById(R.id.tvStatus)
        tvScanResult = findViewById(R.id.tvScanResult)
        groupDevices = findViewById(R.id.groupDevices)

        showPreviousCrash()

        btnScan.setOnClickListener { scanDevices() }
        // 播放中实时生效「关闭手机声音」：传输过程中拨动开关立即静音/恢复
        switchMute.setOnCheckedChangeListener { _, isChecked ->
            if (AudioCaptureService.isRunning) {
                startService(
                    Intent(this, AudioCaptureService::class.java)
                        .setAction(AudioCaptureService.ACTION_SET_MUTE)
                        .putExtra(AudioCaptureService.EXTRA_MUTE_PHONE, isChecked))
            }
        }
        groupDevices.setOnCheckedChangeListener { _, checkedId ->
            if (checkedId == -1) return@setOnCheckedChangeListener
            val rb = findViewById<RadioButton>(checkedId)
            // 选中即填入 IP/端口并停止扫描，连接后双端不再扫描
            val host = rb.tag as? DiscoveredHost ?: return@setOnCheckedChangeListener
            editIp.setText(host.ip)
            editPort.setText(host.audioPort.toString())
            tvScanResult.text = "已选择 ${host.name}（${host.ip}:${host.audioPort}），可直接开始传输"
        }
        btnStart.setOnClickListener { requestMediaProjection() }
        btnStop.setOnClickListener {
            stopService(Intent(this, AudioCaptureService::class.java)
                .setAction(AudioCaptureService.ACTION_STOP))
            tvStatus.text = "已停止"
            updateUiState()
        }
        updateUiState()
    }

    override fun onResume() {
        super.onResume()
        updateUiState()
        uiHandler.post(statusPoll)
    }

    /** 控件就绪后调用：把上次崩溃堆栈显示到状态栏并清理，便于远程定位。 */
    private fun showPreviousCrash() {
        val crashFile = File(filesDir, CrashGuard.CRASH_LOG)
        if (!crashFile.exists()) return
        try {
            val text = crashFile.readText().trim().take(400)
            if (text.isNotEmpty()) {
                tvStatus.text = "上次运行可能异常，请把剪贴板内容粘给我：$text"
            }
            crashFile.delete()
        } catch (_: Exception) {
        }
    }

    override fun onPause() {
        super.onPause()
        uiHandler.removeCallbacks(statusPoll)
    }

    // ---------------- 自动发现 ----------------
    private fun scanDevices() {
        btnScan.isEnabled = false
        tvScanResult.text = "正在扫描局域网（2.5 秒）…"
        groupDevices.removeAllViews()
        groupDevices.visibility = RadioGroup.GONE
        Thread {
            val found = try {
                Discovery.scan(this)
            } catch (e: Exception) {
                emptyList()
            }
            uiHandler.post {
                btnScan.isEnabled = true
                if (found.isEmpty()) {
                    tvScanResult.text = "未发现电脑。请确认电脑端已开启「允许被手机发现」、连接同一 WiFi，或使用手动连接。"
                } else {
                    groupDevices.removeAllViews()
                    found.forEach { host ->
                        val rb = RadioButton(this)
                        rb.text = "${host.name}  ·  ${host.ip}:${host.audioPort}"
                        rb.tag = host
                        groupDevices.addView(rb)
                    }
                    groupDevices.visibility = RadioGroup.VISIBLE
                    tvScanResult.text = "发现 ${found.size} 台电脑，点击选择即可自动填入："
                }
            }
        }.start()
    }

    // ---------------- 传输控制 ----------------
    private fun updateUiState() {
        val running = AudioCaptureService.isRunning
        btnStart.isEnabled = !running
        btnStop.isEnabled = running
        AudioCaptureService.lastError?.let { err ->
            tvStatus.text = "出错：$err"
            return
        }
        if (running) tvStatus.text = "正在发送音频到电脑…（保持前台运行）"
    }

    private fun requestMediaProjection() {
        val ip = editIp.text.toString().trim()
        if (ip.isEmpty() || !ip.matches(Regex("^[0-9.]+$")) || ip.split(".").size != 4) {
            tvStatus.text = "请填写有效的电脑 IP（可先用自动发现选择）"
            return
        }
        val port = editPort.text.toString().trim().toIntOrNull()
        if (port == null || port !in 1..65535) {
            tvStatus.text = "端口无效（1-65535）"
            return
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(
                arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFICATIONS)
        }
        val mpm = getSystemService(MediaProjectionManager::class.java)
        startActivityForResult(mpm.createScreenCaptureIntent(), REQ_MEDIA_PROJECTION)
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_MEDIA_PROJECTION) return
        if (resultCode != Activity.RESULT_OK || data == null) {
            tvStatus.text = "已取消屏幕捕获授权，未开始传输"
            return
        }
        val ip = editIp.text.toString().trim()
        val port = editPort.text.toString().trim().toIntOrNull() ?: 50051
        val mutePhone = switchMute.isChecked
        val serviceIntent = Intent(this, AudioCaptureService::class.java)
            .setAction(AudioCaptureService.ACTION_START)
            .putExtra(AudioCaptureService.EXTRA_IP, ip)
            .putExtra(AudioCaptureService.EXTRA_PORT, port)
            .putExtra(AudioCaptureService.EXTRA_RESULT_CODE, resultCode)
            .putExtra(AudioCaptureService.EXTRA_RESULT_DATA, data)
            .putExtra(AudioCaptureService.EXTRA_MUTE_PHONE, mutePhone)
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(serviceIntent)
        } else {
            startService(serviceIntent)
        }
        tvStatus.text = "已开始：$ip:$port" + if (mutePhone) "（手机已静音）" else ""
        updateUiState()
    }
}
