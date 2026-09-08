package com.soundbeam

import android.content.Context
import android.net.wifi.WifiManager
import java.net.DatagramPacket
import java.net.InetAddress
import java.net.MulticastSocket
import java.net.SocketTimeoutException

/** 局域网内发现的接收端（电脑） */
data class DiscoveredHost(
    val name: String,
    val ip: String,
    val audioPort: Int,
)

/**
 * SoundBeam Discovery v1 —— 组播扫描局域网内的电脑接收端。
 * 与桌面端 desktop/discovery.py 对应：组播组 239.255.77.77:50052，解析 ANNOUNCE 包。
 */
object Discovery {
    const val GROUP = "239.255.77.77"
    const val PORT = 50052

    /** 扫描局域网内的 SoundBeam 接收端，[scanMs] 毫秒后自动停止并返回去重结果。 */
    fun scan(context: Context, scanMs: Long = 2500): List<DiscoveredHost> {
        val wifi =
            context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val lock = wifi.createMulticastLock("soundbeam-disco")
        lock.setReferenceCounted(false)
        val socket: MulticastSocket
        val group: InetAddress
        try {
            lock.acquire()
            socket = MulticastSocket(PORT)
            socket.reuseAddress = true
            socket.soTimeout = 300
            group = InetAddress.getByName(GROUP)
            socket.joinGroup(group)
        } catch (e: Exception) {
            // WiFi 未连接 / 组播不可用：安全返回空列表（不再向上抛出导致崩溃）
            try {
                lock.release()
            } catch (_: Exception) {
            }
            return emptyList()
        }
        val found = LinkedHashMap<String, DiscoveredHost>() // ip -> host（去重）
        val deadline = System.currentTimeMillis() + scanMs
        val buf = ByteArray(2048)
        try {
            // 先发 QUERY，触发电脑端立即应答（不等其 2s 周期广播，响应更快）
            try {
                socket.send(DatagramPacket(buildQuery(), buildQuery().size, group, PORT))
            } catch (_: Exception) {
            }
            while (System.currentTimeMillis() < deadline) {
                try {
                    val pkt = DatagramPacket(buf, buf.size)
                    socket.receive(pkt)
                    parseAnnounce(pkt.data, pkt.length)?.let { (name, port) ->
                        val ip = pkt.address.hostAddress ?: return@let
                        found[ip] = DiscoveredHost(name, ip, port)
                    }
                } catch (_: SocketTimeoutException) {
                    // 单个超时，继续等直到整体截止
                }
            }
        } catch (_: Exception) {
            // 网络异常：返回已发现的结果
        } finally {
            try {
                socket.leaveGroup(group)
            } catch (_: Exception) {
            }
            socket.close()
            lock.release()
        }
        return found.values.toList()
    }

    /** 构造 QUERY 包（magic+ver+type=0x02+port=0+namelen=0）。 */
    fun buildQuery(): ByteArray = byteArrayOf(
        'A'.code.toByte(), 'S'.code.toByte(), 'D'.code.toByte(), 'C'.code.toByte(),
        0x01, 0x02, 0x00, 0x00, 0x00, 0x00,
    )

    /** 解析 ANNOUNCE 包：返回 (name, audioPort)；非法返回 null。 */
    fun parseAnnounce(data: ByteArray, len: Int): Pair<String, Int>? {
        if (len < 10) return null
        if (data[0] != 'A'.code.toByte() || data[1] != 'S'.code.toByte() ||
            data[2] != 'D'.code.toByte() || data[3] != 'C'.code.toByte()
        ) return null
        if (data[4] != 0x01.toByte() || data[5] != 0x01.toByte()) return null // version=1, type=ANNOUNCE
        val port = ((data[6].toInt() and 0xFF) shl 8) or (data[7].toInt() and 0xFF)
        val nameLen = ((data[8].toInt() and 0xFF) shl 8) or (data[9].toInt() and 0xFF)
        if (len != 10 + nameLen) return null
        return String(data, 10, nameLen, Charsets.UTF_8) to port
    }
}
