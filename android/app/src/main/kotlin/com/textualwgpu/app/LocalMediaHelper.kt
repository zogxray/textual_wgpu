package com.textualwgpu.app

import android.Manifest
import android.annotation.SuppressLint
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.provider.Settings
import android.util.Log
import android.view.Surface
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicInteger

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

    @Volatile
    private var appCtx: Context? = null

    @JvmStatic
    fun setAppContext(ctx: Context) {
        appCtx = ctx.applicationContext
    }

    @JvmStatic
    fun deviceId(ctx: Context): String {
        val aid = try {
            Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ANDROID_ID).orEmpty()
        } catch (_: Throwable) {
            ""
        }
        return if (aid.isNotBlank()) "android-$aid" else "android-${Build.MODEL}"
    }

    @JvmStatic
    fun deviceName(): String = Build.MODEL?.takeIf { it.isNotBlank() } ?: "Android Phone"

    @JvmStatic
    fun camerasSpec(ctx: Context): String {
        val out = mutableListOf<String>()
        try {
            val mgr = ctx.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            var hasFront = false
            var hasBack = false
            for (id in mgr.cameraIdList) {
                val facing = mgr.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING)
                if (facing == CameraCharacteristics.LENS_FACING_FRONT) hasFront = true
                if (facing == CameraCharacteristics.LENS_FACING_BACK) hasBack = true
            }
            if (hasFront) out.add("front=Front Camera")
            if (hasBack) out.add("back=Back Camera")
        } catch (_: Throwable) {
        }
        if (out.isEmpty()) {
            out.add("front=Front Camera")
            out.add("back=Back Camera")
        }
        return out.joinToString(";")
    }

    @JvmStatic
    fun deviceProfileJson(): String {
        val ctx = appCtx ?: LocalPythonHost.appContext
        val id = if (ctx != null) deviceId(ctx) else "android-${deviceName()}"
        val name = deviceName()
        val cams = if (ctx != null) camerasSpec(ctx) else "front=Front Camera;back=Back Camera"
        return org.json.JSONObject().apply {
            put("id", id)
            put("name", name)
            put("kind", "android")
            put("cameras", cams)
        }.toString()
    }

    @JvmStatic
    fun syncRustDeviceInfo(ctx: Context) {
        val ac = ctx.applicationContext
        setAppContext(ac)
        try {
            TwBridge.nativeSetDeviceInfo(deviceId(ac), deviceName(), "android", camerasSpec(ac))
        } catch (_: Throwable) {
        }
    }

    @JvmStatic
    fun listMediaJson(reqPath: String): String {
        val ctx = appCtx ?: LocalPythonHost.appContext ?: return "[]"
        val items = listMediaFolder(ctx, reqPath)
        val arr = org.json.JSONArray()
        for (it in items) {
            arr.put(
                org.json.JSONObject().apply {
                    put("d", it.isDir)
                    put("n", it.name)
                    put("p", it.path)
                    put("s", it.size)
                },
            )
        }
        return arr.toString()
    }

    @JvmStatic
    fun readMediaPack(pathOrUri: String): ByteArray? {
        val ctx = appCtx ?: LocalPythonHost.appContext ?: return null
        val res = readMediaFile(ctx, pathOrUri) ?: return null
        val nb = res.first.toByteArray(Charsets.UTF_8)
        val nlen = nb.size.coerceAtMost(4095)
        val out = ByteBuffer.allocate(2 + nlen + res.second.size).order(ByteOrder.LITTLE_ENDIAN)
        out.putShort(nlen.toShort())
        out.put(nb, 0, nlen)
        out.put(res.second)
        return out.array()
    }

    @JvmStatic
    fun listMediaFolder(ctx: Context, reqPath: String): List<FsItem> {
        if (reqPath.isBlank()) {
            return listOf(
                FsItem(true, "Downloads", "Download", 0L),
                FsItem(true, "Pictures", "Pictures", 0L),
                FsItem(true, "Movies", "Movies", 0L),
                FsItem(true, "Music", "Music", 0L),
                FsItem(true, "DCIM", "DCIM", 0L),
            )
        }
        val byName = LinkedHashMap<String, FsItem>()
        // 1) Check public directory directly (full listing when storage/all-files permission granted)
        try {
            val publicType = when (reqPath) {
                "Download" -> Environment.DIRECTORY_DOWNLOADS
                "Pictures" -> Environment.DIRECTORY_PICTURES
                "Movies" -> Environment.DIRECTORY_MOVIES
                "Music" -> Environment.DIRECTORY_MUSIC
                "DCIM" -> Environment.DIRECTORY_DCIM
                else -> reqPath
            }
            val dir = if (reqPath.startsWith("/")) {
                File(reqPath)
            } else {
                Environment.getExternalStoragePublicDirectory(publicType)
            }
            val files = dir?.listFiles()
            if (files != null) {
                for (f in files.sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))) {
                    if (f.name.startsWith(".")) continue
                    byName[f.name] = FsItem(f.isDirectory, f.name, f.absolutePath, if (f.isDirectory) 0L else f.length())
                }
            }
        } catch (_: Throwable) {
        }
        // 2) Query MediaStore (works on Android 10-15 scoped storage even without MANAGE_EXTERNAL_STORAGE)
        val collections = listOf(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
            MediaStore.Audio.Media.EXTERNAL_CONTENT_URI,
            MediaStore.Files.getContentUri("external"),
        )
        val proj = arrayOf(
            MediaStore.MediaColumns._ID,
            MediaStore.MediaColumns.DISPLAY_NAME,
            MediaStore.MediaColumns.SIZE,
            MediaStore.MediaColumns.RELATIVE_PATH,
        )
        val relPrefix = if (reqPath.startsWith("/")) reqPath.substringAfterLast('/') else reqPath
        val sel = "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?"
        val selArgs = arrayOf("$relPrefix%")
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
                        if (name.isBlank() || name.startsWith(".") || byName.containsKey(name)) continue
                        val sz = if (sizeIdx >= 0) cur.getLong(sizeIdx) else 0L
                        val itemUri = ContentUris.withAppendedId(colUri, id).toString()
                        byName[name] = FsItem(false, name, itemUri, sz)
                    }
                }
            } catch (_: Throwable) {
            }
        }
        TwLog.log("TwFs", "listMediaFolder('$reqPath') -> ${byName.size} items")
        return byName.values.toList()
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
                if (bytes.size > 64 * 1024 * 1024) {
                    TwLog.err("TwFs", "File too large (${bytes.size} bytes): $name")
                    return null
                }
                name to bytes
            } else {
                val f = File(pathOrUri)
                if (!f.isFile) return null
                if (f.length() > 64L * 1024L * 1024L) {
                    TwLog.err("TwFs", "File too large (${f.length()} bytes): ${f.name}")
                    return null
                }
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

    // ── Independent per-camera Camera2 + H.264 MediaCodec resource hubs ───
    private class CamSlot(val facing: Int) {
        val subs = ConcurrentHashMap<Int, LinkedBlockingQueue<ByteArray>>()
        @Volatile var active = false
        @Volatile var lastKf: ByteArray? = null
        var device: CameraDevice? = null
        var session: CameraCaptureSession? = null
        var encoder: MediaCodec? = null
        var inputSurface: Surface? = null
        var bgThread: HandlerThread? = null
        var drainThread: Thread? = null
    }

    private val camSlots = ConcurrentHashMap<Int, CamSlot>()
    private val camSubToFacing = ConcurrentHashMap<Int, Int>()
    private val camSubSeq = AtomicInteger(1)
    private val camLock = Any()

    @JvmStatic
    fun subscribeCamera(ctx: Context, facing: Int = 1): Int {
        if (ctx.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            TwLog.err("TwCam", "CAMERA permission not granted")
            return -1
        }
        val normFacing = if (facing >= 2) 2 else 1
        synchronized(camLock) {
            val sid = camSubSeq.getAndIncrement()
            val slot = camSlots.getOrPut(normFacing) { CamSlot(normFacing) }
            val q = LinkedBlockingQueue<ByteArray>(32)
            slot.subs[sid] = q
            camSubToFacing[sid] = normFacing
            if (slot.active) {
                slot.lastKf?.let { q.offer(it) }
                TwLog.log("TwCam", "subscribeCamera #$sid reused facing=$normFacing (subs=${slot.subs.size})")
                return sid
            }
            startCamSlotLocked(ctx, slot)
            TwLog.log("TwCam", "subscribeCamera #$sid started facing=$normFacing (subs=${slot.subs.size})")
            return sid
        }
    }

    @JvmStatic
    fun takeCameraFrame(subId: Int): ByteArray? {
        val facing = camSubToFacing[subId] ?: return null
        val slot = camSlots[facing] ?: return null
        val q = slot.subs[subId] ?: return null
        return try {
            val pkt = q.take()
            if (pkt.isEmpty() || !slot.subs.containsKey(subId)) null else pkt
        } catch (_: InterruptedException) {
            null
        }
    }

    @JvmStatic
    fun unsubscribeCamera(subId: Int) {
        synchronized(camLock) {
            val facing = camSubToFacing.remove(subId) ?: return
            val slot = camSlots[facing] ?: return
            val q = slot.subs.remove(subId)
            q?.clear()
            q?.offer(ByteArray(0))
            if (slot.subs.isEmpty() && slot.active) {
                stopCamSlotLocked(slot)
                camSlots.remove(facing)
                TwLog.log("TwCam", "All facing=$facing subscribers left; Camera2 released")
            }
        }
    }

    @SuppressLint("MissingPermission")
    private fun startCamSlotLocked(ctx: Context, slot: CamSlot) {
        slot.active = true
        slot.lastKf = null
        val bgThread = HandlerThread("tw-cam-bg-${slot.facing}").apply { start() }
        slot.bgThread = bgThread
        val handler = Handler(bgThread.looper)

        try {
            val mgr = ctx.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val wantLens = if (slot.facing >= 2) CameraCharacteristics.LENS_FACING_BACK else CameraCharacteristics.LENS_FACING_FRONT
            val camId = mgr.cameraIdList.firstOrNull { id ->
                mgr.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) == wantLens
            } ?: mgr.cameraIdList.firstOrNull()
            if (camId == null) {
                TwLog.err("TwCam", "No camera device found")
                stopCamSlotLocked(slot)
                return
            }

            val chars = mgr.getCameraCharacteristics(camId)
            val sensorOrient = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
            val rotIdx = (sensorOrient / 90) and 0x03
            val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            val sizes = map?.getOutputSizes(MediaCodec::class.java)
            val chosen = sizes
                ?.filter { it.width in 320..800 && it.height in 240..600 && (it.width % 16 == 0) && (it.height % 16 == 0) }
                ?.minByOrNull { kotlin.math.abs(it.width - 640) + kotlin.math.abs(it.height - 360) }
            val encW = chosen?.width ?: 640
            val encH = chosen?.height ?: 480

            val enc = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
            val fmt = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, encW, encH).apply {
                setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
                setInteger(MediaFormat.KEY_BIT_RATE, 700_000)
                setInteger(MediaFormat.KEY_FRAME_RATE, 24)
                setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
                setInteger(MediaFormat.KEY_PRIORITY, 0)
                setInteger(MediaFormat.KEY_OPERATING_RATE, 30)
            }
            enc.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            val surf = enc.createInputSurface()
            enc.start()
            slot.encoder = enc
            slot.inputSurface = surf

            slot.drainThread = Thread({
                val info = MediaCodec.BufferInfo()
                var spsPps: ByteArray? = null
                var frameCount = 0L
                while (slot.active) {
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
                                    pkt[0] = ((if (isKf) 1 else 0) or (rotIdx shl 1)).toByte()
                                    System.arraycopy(au, 0, pkt, 1, au.size)
                                    if (isKf) slot.lastKf = pkt
                                    for (q in slot.subs.values) {
                                        if (!q.offer(pkt)) {
                                            q.poll()
                                            q.offer(pkt)
                                        }
                                    }
                                    frameCount++
                                    if (frameCount == 1L || frameCount % 120L == 0L) {
                                        TwLog.log("TwCam", "facing=${slot.facing} frame #$frameCount (${au.size}B, kf=$isKf, subs=${slot.subs.size})")
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
            }, "tw-cam-enc-${slot.facing}").apply {
                isDaemon = true
                start()
            }

            mgr.openCamera(
                camId,
                object : CameraDevice.StateCallback() {
                    override fun onOpened(camera: CameraDevice) {
                        if (!slot.active) {
                            camera.close()
                            return
                        }
                        slot.device = camera
                        try {
                            val chars2 = mgr.getCameraCharacteristics(camId)
                            val ranges = chars2.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
                            val bestRange = ranges
                                ?.filter { it.upper in 24..30 }
                                ?.maxWithOrNull(compareBy({ it.lower }, { it.upper }))
                            val req = camera.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                                addTarget(surf)
                                if (bestRange != null) {
                                    set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, bestRange)
                                }
                            }
                            @Suppress("DEPRECATION")
                            camera.createCaptureSession(
                                listOf(surf),
                                object : CameraCaptureSession.StateCallback() {
                                    override fun onConfigured(session: CameraCaptureSession) {
                                        if (!slot.active) {
                                            session.close()
                                            return
                                        }
                                        slot.session = session
                                        try {
                                            session.setRepeatingRequest(req.build(), null, handler)
                                            TwLog.log("TwCam", "Camera2 started id=$camId facing=${slot.facing} (${encW}x${encH} H.264 fps=$bestRange)")
                                        } catch (t: Throwable) {
                                            TwLog.err("TwCam", "setRepeatingRequest failed", t)
                                        }
                                    }

                                    override fun onConfigureFailed(session: CameraCaptureSession) {
                                        TwLog.err("TwCam", "Camera2 configure failed facing=${slot.facing}")
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
                        TwLog.err("TwCam", "Camera2 facing=${slot.facing} error=$error")
                        camera.close()
                    }
                },
                handler,
            )
        } catch (t: Throwable) {
            TwLog.err("TwCam", "startCamSlot failed", t)
            stopCamSlotLocked(slot)
        }
    }

    private fun stopCamSlotLocked(slot: CamSlot) {
        slot.active = false
        slot.drainThread?.interrupt()
        slot.drainThread = null
        try { slot.session?.close() } catch (_: Throwable) {}
        slot.session = null
        try { slot.device?.close() } catch (_: Throwable) {}
        slot.device = null
        try { slot.encoder?.stop() } catch (_: Throwable) {}
        try { slot.encoder?.release() } catch (_: Throwable) {}
        slot.encoder = null
        try { slot.inputSurface?.release() } catch (_: Throwable) {}
        slot.inputSurface = null
        try { slot.bgThread?.quitSafely() } catch (_: Throwable) {}
        slot.bgThread = null
        slot.lastKf = null
    }

    // ── Multi-subscriber Android Microphone (AudioRecord 16kHz PCM16) ──────
    private val micSubs = ConcurrentHashMap<Int, LinkedBlockingQueue<ByteArray>>()
    private val micSubSeq = AtomicInteger(1)
    private val micLock = Any()
    @Volatile
    private var micActive = false
    private var micThread: Thread? = null

    @JvmStatic
    fun subscribeMic(ctx: Context, sampleRate: Int = 16000): Int {
        if (ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            TwLog.err("TwMic", "RECORD_AUDIO permission not granted")
            return -1
        }
        synchronized(micLock) {
            val sid = micSubSeq.getAndIncrement()
            micSubs[sid] = LinkedBlockingQueue(32)
            if (!micActive) {
                startMicHardwareLocked(sampleRate)
            }
            TwLog.log("TwMic", "subscribeMic #$sid (subs=${micSubs.size})")
            return sid
        }
    }

    @JvmStatic
    fun takeMicChunk(subId: Int): ByteArray? {
        val q = micSubs[subId] ?: return null
        return try {
            val pkt = q.take()
            if (pkt.isEmpty() || !micSubs.containsKey(subId)) null else pkt
        } catch (_: InterruptedException) {
            null
        }
    }

    @JvmStatic
    fun unsubscribeMic(subId: Int) {
        synchronized(micLock) {
            val q = micSubs.remove(subId)
            q?.clear()
            q?.offer(ByteArray(0))
            if (micSubs.isEmpty() && micActive) {
                micActive = false
                micThread?.interrupt()
                micThread = null
                TwLog.log("TwMic", "All mic subscribers left; AudioRecord released")
            }
        }
    }

    @SuppressLint("MissingPermission")
    private fun startMicHardwareLocked(sampleRate: Int) {
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
                        for (q in micSubs.values) {
                            if (!q.offer(copy)) {
                                q.poll()
                                q.offer(copy)
                            }
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

    @Volatile
    private var clipCache: String = ""

    @JvmStatic
    fun getClipboardText(ctx: Context): String {
        val latch = java.util.concurrent.CountDownLatch(1)
        var result = clipCache
        Handler(Looper.getMainLooper()).post {
            try {
                val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                val txt = cm.primaryClip?.getItemAt(0)?.coerceToText(ctx)?.toString()
                if (txt != null) {
                    clipCache = txt
                    result = txt
                }
            } catch (_: Throwable) {
            } finally {
                latch.countDown()
            }
        }
        try {
            latch.await(600, java.util.concurrent.TimeUnit.MILLISECONDS)
        } catch (_: InterruptedException) {
        }
        return result
    }

    @JvmStatic
    fun setClipboardText(ctx: Context, text: String) {
        clipCache = text
        Handler(Looper.getMainLooper()).post {
            try {
                val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("textual-wgpu", text))
            } catch (_: Throwable) {
            }
        }
    }

    @JvmStatic
    fun createSpeakerTrack(sampleRate: Int = 16000, channels: Int = 1): AudioTrack? {
        val sr = sampleRate.coerceIn(8000, 48000)
        val chMask = if (channels >= 2) AudioFormat.CHANNEL_OUT_STEREO else AudioFormat.CHANNEL_OUT_MONO
        val minBuf = AudioTrack.getMinBufferSize(sr, chMask, AudioFormat.ENCODING_PCM_16BIT).coerceAtLeast(4096)
        return try {
            AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(sr)
                        .setChannelMask(chMask)
                        .build(),
                )
                .setBufferSizeInBytes(minBuf * 2)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
                .apply { play() }
        } catch (t: Throwable) {
            TwLog.err("TwSpk", "AudioTrack init failed", t)
            null
        }
    }
}
