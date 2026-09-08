package com.soundbeam

import android.app.Application
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import java.io.File

/**
 * 进程级崩溃守卫：在 Application 层最早注册未捕获异常处理器。
 *
 * 无论崩溃发生在布局加载还是任意后台线程，都会把堆栈：
 *   1. 写入内部文件 crash.log（下次启动 MainActivity 会显示到状态栏）
 *   2. 复制到系统剪贴板（用户可粘贴给开发者，无需任何权限）
 */
class CrashGuard : Application() {

    companion object {
        const val CRASH_LOG = "crash.log"
    }

    override fun onCreate() {
        super.onCreate()
        val prev = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                val info = buildString {
                    appendLine("SoundBeam 崩溃信息（请直接粘贴发送给开发者）")
                    appendLine("设备: ${Build.MANUFACTURER} ${Build.MODEL}")
                    appendLine("Android: ${Build.VERSION.SDK_INT} (${Build.VERSION.RELEASE})")
                    appendLine("线程: ${thread.name}")
                    appendLine(throwable.stackTraceToString())
                }.take(4000)
                try {
                    File(filesDir, CRASH_LOG).writeText(info)
                } catch (_: Exception) {
                }
                try {
                    val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                    cm.setPrimaryClip(ClipData.newPlainText("SoundBeamCrash", info))
                } catch (_: Exception) {
                }
            } catch (_: Exception) {
            }
            prev?.uncaughtException(thread, throwable)
        }
    }
}
