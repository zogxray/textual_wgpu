package com.textualwgpu.app

import android.Manifest
import android.annotation.SuppressLint
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaRecorder
import android.net.Uri
import android.os.Environment
import android.os.Handler
import android.os.HandlerThread
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.util.Log
import android.view.Surface
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.LinkedBlockingQueue

object TwLog {
    private val lines = ArrayDeque<String>(160)
    private val fmt = SimpleDateFormat("HH:mm:ss.SSS", Locale.US)

    @Synchronized
    fun log(tag: String, msg: String) {
        Log.i(tag, msg)
        val entry = "[${fmt.format(Date())}] [$tag] $msg"
        if (lines.size >= 160) lines.removeFirst()
        lines.addLast(entry)
    }

    @Synchronized
    fun err(tag: String, msg: String, t: Throwable? = null) {
        if (t != null) Log.e(tag, msg, t) else Log.e(tag, msg)
        val entry = "[${fmt.format(Date())}] [ERR/$tag] $msg${if (t != null) ": ${t.message}" else ""}"
        if (lines.size >= 160) lines.removeFirst()
        lines.addLast(entry)
    }

    @Synchronized
    fun dump(): String = if (lines.isEmpty()) "(no logs yet)" else lines.joinToString("\n")
}

object LocalMediaHelper {

    data class FsItem(val isDir: Boolean, val name: String, val path: String, val size: Long)

    @JvmStatic
    fun listMediaFolder(ctx: Context, reqPath: String): List<FsItem> {
        if (reqPath.isBlank()) {
            return listOf(
                FsItem(true, "Downloads", "Download", 0L),
                FsItem(true, "Pictures", "Pictures", 0L),
                FsItem(true, "Movies", "Movies", 0L),
                FsItem(true, "Music", "Music", 0L),
                FsItem(true, "DCIM", "DCIM", 0L),
                FsItem(false, "[System File Picker…]", "__picker__", 0L),
            )
        }
        val byName = LinkedHashMap<String, FsItem>()
        // 1) Query MediaStore for matching relative path (works cleanly on Android 10-15 scoped storage)
        val collections = listOf(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
            MediaStore.Audio.Media.EXTERNAL_CONTENT_URI,
            MediaStore.Downloads.EXTERNAL_CONTENT_URI,
        )
        val proj = arrayOf(
            MediaStore.MediaColumns._ID,
            MediaStore.MediaColumns.DISPLAY_NAME,
            MediaStore.MediaColumns.SIZE,
            MediaStore.MediaColumns.RELATIVE_PATH,
        )
        val sel = "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?"
        val selArgs = arrayOf("$reqPath%")
        for (colUri in collections) {
            try {
                ctx.contentResolver.query(
                    colUri,
                    proj,
                    sel,
                    selArgs,
                    "${MediaStore.MediaColumns.DATE_MODIFIED} DESC",
                )?.use { cur ->
                    val idIdx = cur.getColumnIndex(MediaStore.MediaColumns._ID)
                    val nameIdx = cur.getColumnIndex(MediaStore.MediaColumns.DISPLAY_NAME)
                    val sizeIdx = cur.getColumnIndex(MediaStore.MediaColumns.SIZE)
                    while (cur.moveToNext()) {
                        val id = if (idIdx >= 0) cur.getLong(idIdx) else continue
                        val name = if (nameIdx >= 0) cur.getString(nameIdx).orEmpty() else continue
                        if (name.isBlank() || byName.containsKey(name)) continue
                        val sz = if (sizeIdx >= 0) cur.getLong(sizeIdx) else 0L
                        val itemUri = ContentUris.withAppendedId(colUri, id).toString()
                        byName[name] = FsItem(false, name, itemUri, sz)
                    }
                }
            } catch (_: Throwable) {
            }
        }
        // 2) Also check public external directory directly if accessible
        try {
            val dir = File(Environment.getExternalStorageDirectory(), reqPath)
            val files = dir.listFiles()
            if (files != null) {
                for (f in files.sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))) {
                    if (f.name.startsWith(".")) continue
                    if (!byName.containsKey(f.name)) {
                        byName[f.name] = FsItem(f.isDirectory, f.name, f.absolutePath, if (f.isDirectory) 0L else f.length())
                    }
                }
            }
        } catch (_: Throwable) {
        }
        val out = byName.values.toMutableList()
        out.add(FsItem(false, "[System File Picker…]", "__picker__", 0L))
        return out
    }

    @JvmStatic
    fun readMediaFile(ctx: Context, pathOrUri: String): Pair<String, ByteArray>? {
        return try {
            if (pathOrUri.startsWith("content://")) {
                val uri = Uri.parse(pathOrUri)
                var name = "media.bin"
                ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                    val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if (idx >= 0 && c.moveToFirst()) name = c.getString(idx) ?: name
                }
                val bytes = ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: return null
                name to bytes
            } else {
                val f = File(pathOrUri)
                if (!f.isFile) return null
                f.name to f.readBytes()
            }
        } catch (t: Throwable) {
            Log.e("LocalMediaHelper", "readMediaFile failed ($pathOrUri)", t)
            null
        }
    }

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

    // ── Camera2 + H.264 MediaCodec encoder (surface-to-NALU) ───────────────
    private val camQueue = LinkedBlockingQueue<ByteArray>(32)
    @Volatile
    private var camActive = false
    private var camDevice: CameraDevice? = null
    private var camSession: CameraCaptureSession? = null
    private var camEncoder: MediaCodec? = null
    private var camInputSurface: Surface? = null
    private var camBgThread: HandlerThread? = null
    private var camDrainThread: Thread? = null

    @JvmStatic
    @SuppressLint("MissingPermission")
    fun startCamera(ctx: Context, facing: Int = 1) {
        stopCamera()
        if (ctx.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            TwLog.err("TwCam", "CAMERA permission not granted")
            return
        }
        camActive = true
        camQueue.clear()
        val bgThread = HandlerThread("tw-cam-bg").apply { start() }
        camBgThread = bgThread
        val handler = Handler(bgThread.looper)

        try {
            val mgr = ctx.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val wantLens = if (facing >= 2) CameraCharacteristics.LENS_FACING_BACK else CameraCharacteristics.LENS_FACING_FRONT
            var camId = mgr.cameraIdList.firstOrNull { id ->
                mgr.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) == wantLens
            } ?: mgr.cameraIdList.firstOrNull()
            if (camId == null) {
                TwLog.err("TwCam", "No camera device found")
                stopCamera()
                return
            }

            val enc = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
            val fmt = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, 640, 480).apply {
                setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
                setInteger(MediaFormat.KEY_BIT_RATE, 1_000_000)
                setInteger(MediaFormat.KEY_FRAME_RATE, 24)
                setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
            }
            enc.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            val surf = enc.createInputSurface()
            enc.start()
            camEncoder = enc
            camInputSurface = surf

            camDrainThread = Thread({
                val info = MediaCodec.BufferInfo()
                var spsPps: ByteArray? = null
                var frameCount = 0L
                while (camActive) {
                    val idx = try {
                        enc.dequeueOutputBuffer(info, 20_000)
                    } catch (_: Throwable) {
                        break
                    }
                    if (idx >= 0) {
                        try {
                            val buf = enc.getOutputBuffer(idx)
                            if (buf != null && info.size > 0) {
                                buf.position(info.offset)
                                val arr = ByteArray(info.size)
                                buf.get(arr)
                                if ((info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) {
                                    spsPps = arr
                                } else {
                                    val isKf = (info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0
                                    val au = if (isKf && spsPps != null) spsPps!! + arr else arr
                                    val pkt = ByteArray(1 + au.size)
                                    pkt[0] = if (isKf) 1 else 0
                                    System.arraycopy(au, 0, pkt, 1, au.size)
                                    if (!camQueue.offer(pkt)) {
                                        camQueue.poll()
                                        camQueue.offer(pkt)
                                    }
                                    frameCount++
                                    if (frameCount == 1L || frameCount % 120L == 0L) {
                                        TwLog.log("TwCam", "encoded frame #$frameCount (${au.size}B, kf=$isKf)")
                                    }
                                }
                            }
                        } finally {
                            try {
                                enc.releaseOutputBuffer(idx, false)
                            } catch (_: Throwable) {
                            }
                        }
                    }
                }
            }, "tw-cam-enc").apply {
                isDaemon = true
                start()
            }

            mgr.openCamera(
                camId,
                object : CameraDevice.StateCallback() {
                    override fun onOpened(camera: CameraDevice) {
                        if (!camActive) {
                            camera.close()
                            return
                        }
                        camDevice = camera
                        try {
                            val req = camera.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                                addTarget(surf)
                            }
                            @Suppress("DEPRECATION")
                            camera.createCaptureSession(
                                listOf(surf),
                                object : CameraCaptureSession.StateCallback() {
                                    override fun onConfigured(session: CameraCaptureSession) {
                                        if (!camActive) {
                                            session.close()
                                            return
                                        }
                                        camSession = session
                                        try {
                                            session.setRepeatingRequest(req.build(), null, handler)
                                            TwLog.log("TwCam", "Camera2 started id=$camId (640x480 H.264)")
                                        } catch (t: Throwable) {
                                            TwLog.err("TwCam", "setRepeatingRequest failed", t)
                                        }
                                    }

                                    override fun onConfigureFailed(session: CameraCaptureSession) {
                                        TwLog.err("TwCam", "Camera2 configure failed")
                                    }
                                },
                                handler,
                            )
                        } catch (t: Throwable) {
                            TwLog.err("TwCam", "createCaptureSession failed", t)
                        }
                    }

                    override fun onDisconnected(camera: CameraDevice) {
                        camera.close()
                    }

                    override fun onError(camera: CameraDevice, error: Int) {
                        TwLog.err("TwCam", "Camera2 error=$error")
                        camera.close()
                    }
                },
                handler,
            )
        } catch (t: Throwable) {
            TwLog.err("TwCam", "startCamera failed", t)
            stopCamera()
        }
    }

    @JvmStatic
    fun popCameraFrame(): ByteArray? = camQueue.poll()

    @JvmStatic
    fun stopCamera() {
        camActive = false
        camDrainThread?.interrupt()
        camDrainThread = null
        try { camSession?.close() } catch (_: Throwable) {}
        camSession = null
        try { camDevice?.close() } catch (_: Throwable) {}
        camDevice = null
        try { camEncoder?.stop() } catch (_: Throwable) {}
        try { camEncoder?.release() } catch (_: Throwable) {}
        camEncoder = null
        try { camInputSurface?.release() } catch (_: Throwable) {}
        camInputSurface = null
        try { camBgThread?.quitSafely() } catch (_: Throwable) {}
        camBgThread = null
        camQueue.clear()
    }

    // ── Android Microphone (AudioRecord 16kHz PCM16) ───────────────────────
    private val micQueue = LinkedBlockingQueue<ByteArray>(32)
    @Volatile
    private var micActive = false
    private var micThread: Thread? = null

    @JvmStatic
    @SuppressLint("MissingPermission")
    fun startMic(ctx: Context, sampleRate: Int = 16000) {
        stopMic()
        if (ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            TwLog.err("TwMic", "RECORD_AUDIO permission not granted")
            return
        }
        val minBuf = AudioRecord.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        ).coerceAtLeast(4096)
        val rec = try {
            AudioRecord(
                MediaRecorder.AudioSource.MIC,
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                minBuf * 2,
            )
        } catch (t: Throwable) {
            TwLog.err("TwMic", "AudioRecord init failed", t)
            return
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            TwLog.err("TwMic", "AudioRecord not initialized")
            rec.release()
            return
        }
        micActive = true
        micQueue.clear()
        micThread = Thread({
            val chunk = ByteArray(2560) // 80ms @ 16kHz mono s16le
            try {
                rec.startRecording()
                TwLog.log("TwMic", "AudioRecord started sr=$sampleRate")
                while (micActive) {
                    var off = 0
                    while (off < chunk.size && micActive) {
                        val n = rec.read(chunk, off, chunk.size - off)
                        if (n <= 0) break
                        off += n
                    }
                    if (off == chunk.size) {
                        val copy = chunk.copyOf()
                        if (!micQueue.offer(copy)) {
                            micQueue.poll()
                            micQueue.offer(copy)
                        }
                    }
                }
            } catch (t: Throwable) {
                TwLog.err("TwMic", "mic read loop failed", t)
            } finally {
                try { rec.stop() } catch (_: Throwable) {}
                try { rec.release() } catch (_: Throwable) {}
            }
        }, "tw-mic").apply {
            isDaemon = true
            start()
        }
    }

    @JvmStatic
    fun popMicChunk(): ByteArray? = micQueue.poll()

    @JvmStatic
    fun stopMic() {
        micActive = false
        micThread?.interrupt()
        micThread = null
        micQueue.clear()
    }
}
