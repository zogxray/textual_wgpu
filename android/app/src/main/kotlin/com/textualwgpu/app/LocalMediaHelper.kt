package com.textualwgpu.app

import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.util.Log
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

object LocalMediaHelper {

    class VideoSession(path: String) {
        private val extractor = MediaExtractor()
        private var trackIdx = -1
        private var codecFlagValue = 0x10
        private var csdHeader: ByteArray = ByteArray(0)
        private var startPtsUs = -1L
        private var startMonoNs = 0L
        @Volatile
        private var closed = false

        init {
            extractor.setDataSource(path)
            for (i in 0 until extractor.trackCount) {
                val fmt = extractor.getTrackFormat(i)
                val mime = fmt.getString(MediaFormat.KEY_MIME).orEmpty()
                if (mime == MediaFormat.MIMETYPE_VIDEO_HEVC || mime == MediaFormat.MIMETYPE_VIDEO_AVC) {
                    trackIdx = i
                    codecFlagValue = if (mime == MediaFormat.MIMETYPE_VIDEO_HEVC) 0x20 else 0x10
                    extractor.selectTrack(i)
                    val baos = ByteArrayOutputStream()
                    for (k in listOf("csd-0", "csd-1", "csd-2")) {
                        val bb = fmt.getByteBuffer(k) ?: continue
                        val dup = bb.duplicate()
                        val arr = ByteArray(dup.remaining())
                        dup.get(arr)
                        baos.write(ensureAnnexB(arr))
                    }
                    csdHeader = baos.toByteArray()
                    break
                }
            }
            if (trackIdx < 0) {
                extractor.release()
                throw IllegalArgumentException("No H.264/HEVC video track found")
            }
        }

        fun codecFlag(): Int = codecFlagValue

        fun nextFrame(): ByteArray? {
            if (closed) return null
            val sampleSize = extractor.sampleSize
            if (sampleSize < 0) {
                // Loop cleanly from start of video
                extractor.seekTo(0, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                startPtsUs = -1L
                return null
            }
            val buf = ByteBuffer.allocate(sampleSize.coerceIn(1024L, 16L * 1024L * 1024L).toInt())
            val n = extractor.readSampleData(buf, 0)
            if (n <= 0) {
                extractor.advance()
                return null
            }
            val ptsUs = extractor.sampleTime.coerceAtLeast(0L)
            val isKf = (extractor.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC) != 0
            extractor.advance()

            if (startPtsUs < 0) {
                startPtsUs = ptsUs
                startMonoNs = System.nanoTime()
            } else {
                val targetNs = startMonoNs + (ptsUs - startPtsUs) * 1000L
                val waitNs = targetNs - System.nanoTime()
                if (waitNs > 1_000_000L && waitNs < 2_000_000_000L) {
                    try {
                        Thread.sleep(waitNs / 1_000_000L)
                    } catch (_: InterruptedException) {
                        return null
                    }
                }
            }

            val raw = ByteArray(n)
            buf.rewind()
            buf.get(raw)
            val annexB = ensureAnnexB(raw)
            val payload = if (isKf && csdHeader.isNotEmpty() && !startsWithHeader(annexB)) {
                csdHeader + annexB
            } else {
                annexB
            }
            val out = ByteBuffer.allocate(9 + payload.size).order(ByteOrder.LITTLE_ENDIAN)
            out.put(if (isKf) 1.toByte() else 0.toByte())
            out.putLong(ptsUs)
            out.put(payload)
            return out.array()
        }

        fun close() {
            closed = true
            try {
                extractor.release()
            } catch (_: Throwable) {
            }
        }
    }

    private fun startsWithHeader(data: ByteArray): Boolean {
        if (data.size < 5) return false
        if (data[0] != 0.toByte() || data[1] != 0.toByte() || data[2] != 0.toByte() || data[3] != 1.toByte()) {
            return false
        }
        val b4 = data[4].toInt() and 0xFF
        val h264Type = b4 and 0x1F
        val hevcType = (b4 shr 1) and 0x3F
        return h264Type == 7 || h264Type == 8 || hevcType in 32..34
    }

    private fun ensureAnnexB(data: ByteArray): ByteArray {
        if (data.size >= 4 && data[0] == 0.toByte() && data[1] == 0.toByte() &&
            (data[2] == 1.toByte() || (data[2] == 0.toByte() && data[3] == 1.toByte()))
        ) {
            return data
        }
        // Convert 4-byte length-prefixed AVCC/HVCC NALUs to Annex-B
        val out = ByteArrayOutputStream(data.size + 16)
        var off = 0
        while (off + 4 <= data.size) {
            val len = ((data[off].toInt() and 0xFF) shl 24) or
                ((data[off + 1].toInt() and 0xFF) shl 16) or
                ((data[off + 2].toInt() and 0xFF) shl 8) or
                (data[off + 3].toInt() and 0xFF)
            off += 4
            if (len <= 0 || off + len > data.size) break
            out.write(0); out.write(0); out.write(0); out.write(1)
            out.write(data, off, len)
            off += len
        }
        return if (out.size() > 0) out.toByteArray() else data
    }

    @JvmStatic
    fun openVideo(path: String): VideoSession = VideoSession(path)

    @JvmStatic
    fun decodeAudioPcm(path: String): ByteArray? {
        val extractor = MediaExtractor()
        var codec: MediaCodec? = null
        try {
            extractor.setDataSource(path)
            var trackIdx = -1
            var fmt: MediaFormat? = null
            var mime = ""
            for (i in 0 until extractor.trackCount) {
                val f = extractor.getTrackFormat(i)
                val m = f.getString(MediaFormat.KEY_MIME).orEmpty()
                if (m.startsWith("audio/")) {
                    trackIdx = i
                    fmt = f
                    mime = m
                    break
                }
            }
            if (trackIdx < 0 || fmt == null) return null
            extractor.selectTrack(trackIdx)
            var sr = if (fmt.containsKey(MediaFormat.KEY_SAMPLE_RATE)) fmt.getInteger(MediaFormat.KEY_SAMPLE_RATE) else 44100
            var ch = if (fmt.containsKey(MediaFormat.KEY_CHANNEL_COUNT)) fmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT) else 2

            codec = MediaCodec.createDecoderByType(mime)
            codec.configure(fmt, null, null, 0)
            codec.start()

            val pcmOut = ByteArrayOutputStream()
            val maxBytes = 10 * 1024 * 1024 // cap ~10MB PCM for responsive playback
            val info = MediaCodec.BufferInfo()
            var sawEos = false
            var outEos = false

            while (!outEos && pcmOut.size() < maxBytes) {
                if (!sawEos) {
                    val inIdx = codec.dequeueInputBuffer(5000)
                    if (inIdx >= 0) {
                        val inBuf = codec.getInputBuffer(inIdx)
                        if (inBuf != null) {
                            val n = extractor.readSampleData(inBuf, 0)
                            if (n < 0) {
                                codec.queueInputBuffer(inIdx, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                                sawEos = true
                            } else {
                                codec.queueInputBuffer(inIdx, 0, n, extractor.sampleTime.coerceAtLeast(0L), 0)
                                extractor.advance()
                            }
                        }
                    }
                }
                val outIdx = codec.dequeueOutputBuffer(info, 5000)
                if (outIdx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    val of = codec.outputFormat
                    if (of.containsKey(MediaFormat.KEY_SAMPLE_RATE)) sr = of.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                    if (of.containsKey(MediaFormat.KEY_CHANNEL_COUNT)) ch = of.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                } else if (outIdx >= 0) {
                    val outBuf = codec.getOutputBuffer(outIdx)
                    if (outBuf != null && info.size > 0) {
                        outBuf.position(info.offset)
                        val chunk = ByteArray(info.size)
                        outBuf.get(chunk)
                        pcmOut.write(chunk)
                    }
                    codec.releaseOutputBuffer(outIdx, false)
                    if ((info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        outEos = true
                    }
                }
            }
            val pcm = pcmOut.toByteArray()
            if (pcm.isEmpty()) return null
            val out = ByteBuffer.allocate(8 + pcm.size).order(ByteOrder.LITTLE_ENDIAN)
            out.putInt(sr)
            out.putInt(ch)
            out.put(pcm)
            return out.array()
        } catch (t: Throwable) {
            Log.e("LocalMediaHelper", "decodeAudioPcm failed", t)
            return null
        } finally {
            try {
                codec?.stop()
            } catch (_: Throwable) {
            }
            try {
                codec?.release()
            } catch (_: Throwable) {
            }
            try {
                extractor.release()
            } catch (_: Throwable) {
            }
        }
    }
}
