package com.soundbeam

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.UnknownHostException
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * 后台 UDP 发送线程：从有界队列取协议包发往目标电脑。
 *
 * 发送失败（网络不可达等）时线程退出，调用方通过 [onError] 感知。
 * 地址在构造时解析（失败抛 [IllegalArgumentException]），包入队不依赖线程是否已启动，
 * 保证 START 等首个包不会因线程启动竞态而丢失。
 */
class UdpSender(
    private val ip: String,
    private val port: Int,
    private val onError: ((String) -> Unit)? = null,
) : Thread("audio-udp-sender") {

    private val queue = LinkedBlockingQueue<ByteArray>(512)
    private val socket = DatagramSocket()
    @Volatile
    private var running = true

    private val address: InetAddress = try {
        InetAddress.getByName(ip)
    } catch (e: UnknownHostException) {
        socket.close()
        throw IllegalArgumentException("无法解析电脑 IP：$ip", e)
    }

    init {
        socket.soTimeout = 2000
    }

    override fun run() {
        try {
            while (running) {
                val packet = try {
                    queue.poll(200, TimeUnit.MILLISECONDS)
                } catch (e: InterruptedException) {
                    break
                } ?: continue
                try {
                    socket.send(DatagramPacket(packet, packet.size, address, port))
                } catch (e: Exception) {
                    if (running) {
                        onError?.invoke("发送失败（网络错误）：${e.message}")
                        running = false
                    }
                }
            }
        } finally {
            socket.close()
        }
    }

    fun send(packet: ByteArray) {
        if (running) queue.offer(packet)
    }

    fun shutdown() {
        running = false
        queue.clear()
        interrupt()
    }
}
