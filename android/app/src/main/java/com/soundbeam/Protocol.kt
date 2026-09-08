package com.soundbeam

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * SoundBeam Stream Protocol v1 —— 与桌面端 desktop/protocol.py 保持一致。
 *
 * 包格式（大端序，固定头 15 字节）:
 *   [0:2]   Magic      0x4153 ("AS")
 *   [2]     Version    0x01
 *   [3]     Flags      START=0x01 / STOP=0x02 / DATA=0x04 / HEARTBEAT=0x08
 *   [4:6]   Seq        uint16 包序号（循环计数）
 *   [6:10]  Timestamp  uint32 流启动后的相对毫秒
 *   [10]    Channels   1=mono, 2=stereo
 *   [11:13] SampleRate uint16 Hz
 *   [13:15] PayloadLen uint16 载荷字节数
 *   [15:]   Payload    PCM 16-bit 小端，交错声道
 */
object Protocol {

    const val FLAG_START = 0x01
    const val FLAG_STOP = 0x02
    const val FLAG_DATA = 0x04
    const val FLAG_HEARTBEAT = 0x08

    const val PACKET_MS = 20
    const val SAMPLE_RATE = 48000
    const val CHANNELS = 2
    // 每包字节数 = 20ms × 48k × 2ch × 2B
    const val PACKET_BYTES = PACKET_MS * SAMPLE_RATE / 1000 * CHANNELS * 2 // 3840

    fun buildPacket(
        flags: Int,
        seq: Int,
        timestampMs: Long,
        channels: Int = CHANNELS,
        sampleRate: Int = SAMPLE_RATE,
        payload: ByteArray = ByteArray(0),
    ): ByteArray {
        val buf = ByteBuffer.allocate(15 + payload.size).order(ByteOrder.BIG_ENDIAN)
        buf.put(0x41)                       // 'A'
        buf.put(0x53)                       // 'S'
        buf.put(0x01)                       // version
        buf.put(flags.toByte())
        buf.putShort((seq and 0xFFFF).toShort())
        buf.putInt((timestampMs and 0xFFFFFFFFL).toInt())
        buf.put(channels.toByte())
        buf.putShort(sampleRate.toShort())
        buf.putShort(payload.size.toShort())
        buf.put(payload)
        return buf.array()
    }
}
