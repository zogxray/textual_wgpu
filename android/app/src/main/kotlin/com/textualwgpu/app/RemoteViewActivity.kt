package com.textualwgpu.app

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Color
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.OpenableColumns
import android.text.InputType
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.ViewConfiguration
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import java.nio.ByteBuffer
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.math.abs

class RemoteViewActivity : AppCompatActivity(), TwCallback {

    private var sessionPtr: Long = 0L
    private lateinit var surfaceView: TwSurfaceView
    private lateinit var statusTv: TextView
    private lateinit var barScroll: HorizontalScrollView
    private val mainHandler = Handler(Looper.getMainLooper())
    private var ctrlArmed = false
    private var fullscreenMode = false
    private var showKeysBar = true
    @Volatile
    private var audioThread: Thread? = null
    @Volatile
    private var activeTrack: AudioTrack? = null
    private val videoDecoders = ConcurrentHashMap<Int, VideoDecoderSlot>()

    private val openDocLauncher = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null && sessionPtr != 0L) {
            Thread {
                try {
                    val name = queryFileName(uri) ?: "client_media.bin"
                    val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    if (bytes != null && bytes.isNotEmpty() && sessionPtr != 0L) {
                        TwBridge.nativeSendClientFile(sessionPtr, name, bytes)
                        onStatus("Sent $name (${(bytes.size + 1023) / 1024} KB)")
                    }
                } catch (t: Throwable) {
                    android.util.Log.e("TwFile", "Client file upload failed", t)
                    onStatus("File upload failed: ${t.message}")
                }
            }.start()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        fullscreenMode = intent.getBooleanExtra("fullscreen", false)
        showKeysBar = intent.getBooleanExtra("show_keys", true)

        if (fullscreenMode) {
            applyImmersiveFullscreen()
        }

        val dp = resources.displayMetrics.density
        fun px(v: Int) = (v * dp).toInt()

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#141418"))
        }

        statusTv = TextView(this).apply {
            text = "Connecting…"
            textSize = 11f
            setTextColor(Color.parseColor("#D4AD72"))
            setBackgroundColor(Color.parseColor("#1F1B26"))
            setPadding(px(10), px(4), px(10), px(4))
            visibility = if (fullscreenMode) View.GONE else View.VISIBLE
        }
        root.addView(statusTv)

        surfaceView = TwSurfaceView(this)
        val svLp = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            1f,
        )
        root.addView(surfaceView, svLp)

        barScroll = HorizontalScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#1E1A24"))
            isHorizontalScrollBarEnabled = false
            visibility = if (showKeysBar) View.VISIBLE else View.GONE
        }
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(px(4), px(4), px(4), px(4))
        }
        barScroll.addView(bar)

        fun addChip(label: String, onClick: (Button) -> Unit) {
            val b = Button(this).apply {
                text = label
                textSize = 11f
                isAllCaps = false
                minWidth = px(44)
                minimumWidth = px(44)
                setTextColor(Color.parseColor("#F5F0E8"))
                setBackgroundColor(Color.parseColor("#2E2738"))
                setPadding(px(8), px(4), px(8), px(4))
                setOnClickListener { onClick(this) }
            }
            val lp = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                px(36),
            ).apply { marginEnd = px(4) }
            bar.addView(b, lp)
        }

        addChip("KB") {
            surfaceView.requestFocus()
            val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
            imm.showSoftInput(surfaceView, InputMethodManager.SHOW_IMPLICIT)
        }
        addChip("Esc") { sendKey("escape") }
        addChip("Tab") { sendKey("tab") }
        addChip("⇧Tab") { sendKey("shift+tab") }
        addChip("Ctrl") { b ->
            ctrlArmed = !ctrlArmed
            b.setBackgroundColor(Color.parseColor(if (ctrlArmed) "#D4AD72" else "#2E2738"))
            b.setTextColor(Color.parseColor(if (ctrlArmed) "#141418" else "#F5F0E8"))
        }
        addChip("↑") { sendKey("up") }
        addChip("↓") { sendKey("down") }
        addChip("←") { sendKey("left") }
        addChip("→") { sendKey("right") }
        for (i in 1..10) {
            addChip("F$i") { sendKey("f$i") }
        }
        addChip("Paste") {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val clip = cm.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString().orEmpty()
            if (clip.isNotEmpty() && sessionPtr != 0L) {
                TwBridge.nativePaste(sessionPtr, clip)
                TwBridge.nativeKey(sessionPtr, "ctrl+v")
            }
        }
        addChip("1:1") {
            if (sessionPtr != 0L) {
                TwBridge.nativeResetView(sessionPtr)
                TwBridge.nativeRenderFrame(sessionPtr)
            }
        }
        addChip("Apps") {
            if (sessionPtr != 0L) {
                TwBridge.nativeListApps(sessionPtr)
            }
        }
        root.addView(barScroll)
        setContentView(root)
        surfaceView.requestFocus()

        val mode = intent.getStringExtra("mode") ?: "ssh"
        val vw = intent.getIntExtra("virt_w", 1100)
        val vh = intent.getIntExtra("virt_h", 720)
        val reversePort = intent.getIntExtra("reverse_port", 0)
        if (mode == "preview") {
            sessionPtr = TwBridge.nativeConnectOfflinePreview(vw, vh, this)
        } else if (mode == "tcp") {
            val h = intent.getStringExtra("host") ?: "127.0.0.1"
            val p = intent.getIntExtra("port", 7420)
            sessionPtr = TwBridge.nativeConnectTcp(h, p, vw, vh, this)
        } else if (mode == "local_python") {
            statusTv.text = "Starting local Python Demo…"
            Thread {
                val ok = LocalPythonHost.ensureStarted(applicationContext)
                mainHandler.post {
                    if (isDestroyed) return@post
                    if (!ok) {
                        statusTv.text = "Failed to start local Python server"
                        statusTv.visibility = View.VISIBLE
                    } else {
                        sessionPtr = TwBridge.nativeConnectTcp("127.0.0.1", LocalPythonHost.PORT, vw, vh, this)
                        if (surfaceView.holder.surface.isValid) {
                            TwBridge.nativeAttachSurface(
                                sessionPtr,
                                surfaceView.holder.surface,
                                surfaceView.width.coerceAtLeast(1),
                                surfaceView.height.coerceAtLeast(1),
                            )
                            TwBridge.nativeRenderFrame(sessionPtr)
                        }
                    }
                }
            }.start()
        } else {
            sessionPtr = TwBridge.nativeConnectSsh(
                intent.getStringExtra("host") ?: "",
                intent.getIntExtra("port", 22),
                intent.getStringExtra("user") ?: "",
                intent.getStringExtra("pass") ?: "",
                intent.getStringExtra("key") ?: "",
                intent.getStringExtra("cmd") ?: "",
                vw,
                vh,
                reversePort,
                this,
            )
        }
    }

    private fun applyImmersiveFullscreen() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val controller = WindowInsetsControllerCompat(window, window.decorView)
        controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        controller.hide(WindowInsetsCompat.Type.systemBars())
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus && fullscreenMode) {
            applyImmersiveFullscreen()
        }
    }

    private fun sendKey(key: String) {
        if (sessionPtr == 0L) return
        val effective = if (ctrlArmed && key.length == 1) {
            ctrlArmed = false
            "ctrl+${key.lowercase()}"
        } else {
            key
        }
        TwBridge.nativeKey(sessionPtr, effective)
    }

    private fun mapHardwareKey(event: KeyEvent): String? {
        val base = when (event.keyCode) {
            KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER -> "enter"
            KeyEvent.KEYCODE_ESCAPE -> "escape"
            KeyEvent.KEYCODE_TAB -> "tab"
            KeyEvent.KEYCODE_DEL -> "backspace"
            KeyEvent.KEYCODE_FORWARD_DEL -> "delete"
            KeyEvent.KEYCODE_DPAD_UP -> "up"
            KeyEvent.KEYCODE_DPAD_DOWN -> "down"
            KeyEvent.KEYCODE_DPAD_LEFT -> "left"
            KeyEvent.KEYCODE_DPAD_RIGHT -> "right"
            KeyEvent.KEYCODE_MOVE_HOME -> "home"
            KeyEvent.KEYCODE_MOVE_END -> "end"
            KeyEvent.KEYCODE_PAGE_UP -> "pageup"
            KeyEvent.KEYCODE_PAGE_DOWN -> "pagedown"
            KeyEvent.KEYCODE_SPACE -> "space"
            KeyEvent.KEYCODE_F1 -> "f1"
            KeyEvent.KEYCODE_F2 -> "f2"
            KeyEvent.KEYCODE_F3 -> "f3"
            KeyEvent.KEYCODE_F4 -> "f4"
            KeyEvent.KEYCODE_F5 -> "f5"
            KeyEvent.KEYCODE_F6 -> "f6"
            KeyEvent.KEYCODE_F7 -> "f7"
            KeyEvent.KEYCODE_F8 -> "f8"
            KeyEvent.KEYCODE_F9 -> "f9"
            KeyEvent.KEYCODE_F10 -> "f10"
            else -> null
        }
        val ctrl = event.isCtrlPressed || ctrlArmed
        val shift = event.isShiftPressed
        if (base != null) {
            if (ctrl && base == "enter") return "ctrl+enter"
            if (shift && base in setOf("left", "right", "up", "down", "home", "end", "tab")) {
                return "shift+$base"
            }
            return base
        }
        if (ctrl && event.keyCode in KeyEvent.KEYCODE_A..KeyEvent.KEYCODE_Z) {
            val ch = ('a' + (event.keyCode - KeyEvent.KEYCODE_A))
            ctrlArmed = false
            return "ctrl+$ch"
        }
        return null
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action == KeyEvent.ACTION_DOWN && sessionPtr != 0L) {
            if (event.keyCode == KeyEvent.KEYCODE_BACK && fullscreenMode) {
                return super.dispatchKeyEvent(event)
            }
            val mapped = mapHardwareKey(event)
            if (mapped != null) {
                if (mapped == "ctrl+v") {
                    val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                    val clip = cm.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString().orEmpty()
                    if (clip.isNotEmpty()) {
                        TwBridge.nativePaste(sessionPtr, clip)
                    }
                }
                TwBridge.nativeKey(sessionPtr, mapped)
                if (mapped == "space" && !event.isCtrlPressed) {
                    TwBridge.nativeChar(sessionPtr, " ")
                }
                return true
            }
            val uc = event.getUnicodeChar(event.metaState)
            if (uc > 0 && !event.isCtrlPressed && !event.isAltPressed) {
                val ch = uc.toChar().toString()
                TwBridge.nativeKey(sessionPtr, ch.lowercase())
                TwBridge.nativeChar(sessionPtr, ch)
                return true
            }
        }
        return super.dispatchKeyEvent(event)
    }

    override fun onRedraw() {
        mainHandler.post {
            if (sessionPtr != 0L) {
                TwBridge.nativeRenderFrame(sessionPtr)
            }
        }
    }

    override fun onStatus(msg: String) {
        mainHandler.post {
            statusTv.text = msg
            if (fullscreenMode) {
                // Show errors/connecting status briefly, hide once active.
                val isError = msg.startsWith("Exited") || msg.contains("failed", ignoreCase = true) ||
                    msg.contains("error", ignoreCase = true) || msg.contains("rejected", ignoreCase = true) ||
                    msg.startsWith("Disconnected")
                statusTv.visibility = if (isError) View.VISIBLE else View.GONE
            }
        }
    }

    override fun onClipboard(text: String) {
        mainHandler.post {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("textual-wgpu", text))
        }
    }

    override fun onAudioPcm(sampleRate: Int, channels: Int, pcm: ByteArray) {
        android.util.Log.i("TwAudio", "onAudioPcm sr=$sampleRate ch=$channels bytes=${pcm.size}")
        onAudioStop()
        if (pcm.isEmpty()) return
        val chMask = if (channels <= 1) AudioFormat.CHANNEL_OUT_MONO else AudioFormat.CHANNEL_OUT_STEREO
        val minBuf = AudioTrack.getMinBufferSize(sampleRate, chMask, AudioFormat.ENCODING_PCM_16BIT)
            .coerceAtLeast(4096)
        val useStatic = pcm.size <= 1024 * 1024
        val track = try {
            AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build(),
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setSampleRate(sampleRate)
                        .setChannelMask(chMask)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .build(),
                )
                .setBufferSizeInBytes(if (useStatic) pcm.size else minBuf * 4)
                .setTransferMode(if (useStatic) AudioTrack.MODE_STATIC else AudioTrack.MODE_STREAM)
                .build()
        } catch (t: Throwable) {
            android.util.Log.e("TwAudio", "AudioTrack build failed", t)
            onStatus("Audio error: ${t.message}")
            return
        }
        activeTrack = track
        if (useStatic) {
            try {
                val written = track.write(pcm, 0, pcm.size)
                android.util.Log.i("TwAudio", "STATIC wrote $written / ${pcm.size}")
                track.play()
            } catch (t: Throwable) {
                android.util.Log.e("TwAudio", "STATIC play failed", t)
                onStatus("Audio play error: ${t.message}")
            }
            return
        }
        val t = Thread {
            try {
                track.play()
                var off = 0
                while (off < pcm.size && activeTrack === track) {
                    val chunk = (pcm.size - off).coerceAtMost(minBuf)
                    val written = track.write(pcm, off, chunk)
                    if (written <= 0) break
                    off += written
                }
                Thread.sleep(250)
                if (activeTrack === track) {
                    track.stop()
                }
            } catch (t: Throwable) {
                android.util.Log.e("TwAudio", "STREAM play failed", t)
            } finally {
                try {
                    track.release()
                } catch (_: Throwable) {
                }
                if (activeTrack === track) {
                    activeTrack = null
                }
            }
        }
        audioThread = t
        t.start()
    }

    override fun onAudioStop() {
        val tr = activeTrack
        activeTrack = null
        if (tr != null) {
            try {
                tr.pause()
                tr.flush()
                tr.stop()
                tr.release()
            } catch (_: Throwable) {
            }
        }
    }

    override fun onVideoNalu(
        surfId: Int,
        codecFlag: Int,
        ptsUs: Long,
        isKeyframe: Boolean,
        data: ByteArray,
    ) {
        if (sessionPtr == 0L || data.isEmpty()) return
        val existing = videoDecoders[surfId]
        val slot = if (existing != null && existing.codecFlag == codecFlag) {
            existing
        } else {
            existing?.close()
            val created = VideoDecoderSlot(surfId, codecFlag)
            videoDecoders[surfId] = created
            created
        }
        slot.enqueue(NaluPacket(ptsUs, isKeyframe, data))
    }

    override fun onOpenClientFile(accept: String) {
        mainHandler.post {
            val mimes = accept
                .split(',')
                .map { it.trim() }
                .filter { it.contains('/') }
                .ifEmpty { listOf("*/*") }
                .toTypedArray()
            try {
                openDocLauncher.launch(mimes)
            } catch (t: Throwable) {
                android.util.Log.e("TwFile", "OpenDocument launch failed", t)
                onStatus("File picker error: ${t.message}")
            }
        }
    }

    override fun onAppList(
        ids: Array<String>,
        names: Array<String>,
        descriptions: Array<String>,
        kinds: Array<String>,
    ) {
        mainHandler.post {
            if (isDestroyed || sessionPtr == 0L) return@post
            if (ids.isEmpty()) {
                onStatus("No apps advertised by peer")
                return@post
            }
            val labels = Array(ids.size) { i ->
                val desc = descriptions.getOrNull(i).orEmpty()
                val kind = kinds.getOrNull(i).orEmpty()
                if (desc.isNotEmpty()) "${names[i]} [$kind]\n$desc" else "${names[i]} [$kind]"
            }
            AlertDialog.Builder(this)
                .setTitle("Peer Apps · Launch Without Shell")
                .setItems(labels) { _, which ->
                    val aid = ids.getOrNull(which) ?: return@setItems
                    if (sessionPtr != 0L) {
                        TwBridge.nativeLaunchApp(sessionPtr, aid, "")
                        onStatus("Launching $aid…")
                    }
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
    }

    override fun onLaunchStatus(ok: Boolean, appId: String, message: String) {
        onStatus(if (ok) "Launched $appId: $message" else "Launch $appId failed: $message")
    }

    private fun queryFileName(uri: Uri): String? {
        return try {
            contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0 && c.moveToFirst()) c.getString(idx) else null
            } ?: uri.lastPathSegment?.substringAfterLast('/')
        } catch (_: Throwable) {
            uri.lastPathSegment?.substringAfterLast('/')
        }
    }

    private class NaluPacket(val ptsUs: Long, val isKeyframe: Boolean, val data: ByteArray)

    private inner class VideoDecoderSlot(val surfId: Int, val codecFlag: Int) {
        private val queue = LinkedBlockingQueue<NaluPacket>(32)
        @Volatile
        private var closed = false
        private val thread = Thread({ runLoop() }, "tw-vdec-$surfId").apply {
            isDaemon = true
            start()
        }

        fun enqueue(pkt: NaluPacket) {
            if (closed) return
            if (!queue.offer(pkt)) {
                if (pkt.isKeyframe) {
                    queue.clear()
                    queue.offer(pkt)
                } else {
                    queue.poll()
                    queue.offer(pkt)
                }
            }
        }

        fun close() {
            closed = true
            thread.interrupt()
        }

        private fun copyPlaneWithOffset(buf: ByteBuffer, offset: Int): ByteArray {
            val dup = buf.duplicate()
            val pos = offset.coerceIn(0, dup.limit())
            dup.position(pos)
            val arr = ByteArray(dup.remaining())
            dup.get(arr)
            return arr
        }

        private fun runLoop() {
            val mime = when (codecFlag) {
                TwBridge.VID_CODEC_HEVC -> MediaFormat.MIMETYPE_VIDEO_HEVC
                TwBridge.VID_CODEC_AV1 -> "video/av01"
                else -> MediaFormat.MIMETYPE_VIDEO_AVC
            }
            var codec: MediaCodec? = null
            var seenKf = false
            var outW = 0
            var outH = 0
            val info = MediaCodec.BufferInfo()
            try {
                codec = MediaCodec.createDecoderByType(mime)
                val fmt = MediaFormat.createVideoFormat(mime, 1280, 720).apply {
                    setInteger(
                        MediaFormat.KEY_COLOR_FORMAT,
                        MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible,
                    )
                    setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 1024 * 1024)
                    if (Build.VERSION.SDK_INT >= 30) {
                        setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
                    }
                }
                codec.configure(fmt, null, null, 0)
                codec.start()

                while (!closed && sessionPtr != 0L) {
                    val pkt = queue.poll(15, TimeUnit.MILLISECONDS)
                    if (pkt != null) {
                        if (!seenKf) {
                            if (!pkt.isKeyframe) continue
                            seenKf = true
                        }
                        val inIdx = codec.dequeueInputBuffer(5000)
                        if (inIdx >= 0) {
                            val inBuf = codec.getInputBuffer(inIdx)
                            if (inBuf != null && inBuf.capacity() >= pkt.data.size) {
                                inBuf.clear()
                                inBuf.put(pkt.data)
                                val flags = if (pkt.isKeyframe) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0
                                codec.queueInputBuffer(inIdx, 0, pkt.data.size, pkt.ptsUs, flags)
                            } else {
                                codec.queueInputBuffer(inIdx, 0, 0, 0, 0)
                            }
                        }
                    }

                    while (!closed) {
                        val outIdx = codec.dequeueOutputBuffer(info, 0)
                        if (outIdx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                            val of = codec.outputFormat
                            val hasCrop = of.containsKey("crop-right") && of.containsKey("crop-left") &&
                                of.containsKey("crop-bottom") && of.containsKey("crop-top")
                            outW = if (hasCrop) {
                                of.getInteger("crop-right") - of.getInteger("crop-left") + 1
                            } else {
                                of.getInteger(MediaFormat.KEY_WIDTH)
                            }
                            outH = if (hasCrop) {
                                of.getInteger("crop-bottom") - of.getInteger("crop-top") + 1
                            } else {
                                of.getInteger(MediaFormat.KEY_HEIGHT)
                            }
                        } else if (outIdx >= 0) {
                            try {
                                val img = codec.getOutputImage(outIdx)
                                if (img != null) {
                                    try {
                                        val crop = img.cropRect
                                        val w = if (crop.width() > 0) crop.width() else if (outW > 0) outW else img.width
                                        val h = if (crop.height() > 0) crop.height() else if (outH > 0) outH else img.height
                                        val planes = img.planes
                                        if (planes.size >= 3 && w > 0 && h > 0 && sessionPtr != 0L) {
                                            val yp = planes[0]
                                            val up = planes[1]
                                            val vp = planes[2]
                                            val yOff = crop.top * yp.rowStride + crop.left * yp.pixelStride
                                            val uOff = (crop.top / 2) * up.rowStride + (crop.left / 2) * up.pixelStride
                                            val vOff = (crop.top / 2) * vp.rowStride + (crop.left / 2) * vp.pixelStride
                                            val yBytes = copyPlaneWithOffset(yp.buffer, yOff)
                                            val uBytes = copyPlaneWithOffset(up.buffer, uOff)
                                            val vBytes = copyPlaneWithOffset(vp.buffer, vOff)
                                            TwBridge.nativePushDecodedYuv(
                                                sessionPtr,
                                                surfId,
                                                w,
                                                h,
                                                yBytes,
                                                uBytes,
                                                vBytes,
                                                yp.rowStride,
                                                up.rowStride,
                                                vp.rowStride,
                                                up.pixelStride.coerceAtLeast(1),
                                            )
                                            onRedraw()
                                        }
                                    } finally {
                                        img.close()
                                    }
                                }
                            } finally {
                                codec.releaseOutputBuffer(outIdx, false)
                            }
                        } else {
                            break
                        }
                    }
                }
            } catch (_: InterruptedException) {
            } catch (t: Throwable) {
                android.util.Log.e("TwVideo", "MediaCodec decode failed ($mime)", t)
                onStatus("Video decode err: ${t.message}")
            } finally {
                try {
                    codec?.stop()
                } catch (_: Throwable) {
                }
                try {
                    codec?.release()
                } catch (_: Throwable) {
                }
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        onAudioStop()
        for (slot in videoDecoders.values) {
            slot.close()
        }
        videoDecoders.clear()
        if (sessionPtr != 0L) {
            TwBridge.nativeDisconnect(sessionPtr)
            sessionPtr = 0L
        }
    }

    inner class TwSurfaceView(ctx: Context) : SurfaceView(ctx), SurfaceHolder.Callback {
        private val touchSlop = ViewConfiguration.get(ctx).scaledTouchSlop.toFloat()
        private var downX = 0f
        private var downY = 0f
        private var lastX = 0f
        private var lastY = 0f
        private var moved = false
        private var twoFinger = false

        private val scaleDetector = ScaleGestureDetector(
            ctx,
            object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
                override fun onScale(detector: ScaleGestureDetector): Boolean {
                    if (sessionPtr != 0L) {
                        TwBridge.nativePanZoom(sessionPtr, detector.scaleFactor, 0f, 0f)
                        TwBridge.nativeRenderFrame(sessionPtr)
                    }
                    return true
                }
            },
        )

        init {
            isFocusable = true
            isFocusableInTouchMode = true
            holder.addCallback(this)
        }

        override fun surfaceCreated(holder: SurfaceHolder) {
            if (sessionPtr != 0L) {
                TwBridge.nativeAttachSurface(sessionPtr, holder.surface, width.coerceAtLeast(1), height.coerceAtLeast(1))
                TwBridge.nativeRenderFrame(sessionPtr)
            }
        }

        override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
            if (sessionPtr != 0L) {
                TwBridge.nativeResizeSurface(sessionPtr, width, height)
                TwBridge.nativeRenderFrame(sessionPtr)
            }
        }

        override fun surfaceDestroyed(holder: SurfaceHolder) {
            if (sessionPtr != 0L) {
                TwBridge.nativeDetachSurface(sessionPtr)
            }
        }

        override fun onCheckIsTextEditor(): Boolean = true

        override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection {
            outAttrs.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            outAttrs.imeOptions = EditorInfo.IME_ACTION_NONE or EditorInfo.IME_FLAG_NO_EXTRACT_UI
            return object : BaseInputConnection(this, false) {
                override fun commitText(text: CharSequence?, newCursorPosition: Int): Boolean {
                    val s = text?.toString().orEmpty()
                    if (s.isNotEmpty() && sessionPtr != 0L) {
                        if (ctrlArmed && s.length == 1) {
                            sendKey(s)
                        } else {
                            TwBridge.nativeChar(sessionPtr, s)
                        }
                    }
                    return true
                }

                override fun deleteSurroundingText(beforeLength: Int, afterLength: Int): Boolean {
                    if (sessionPtr != 0L && beforeLength > 0) {
                        repeat(beforeLength) { TwBridge.nativeKey(sessionPtr, "backspace") }
                    }
                    return true
                }

                override fun sendKeyEvent(event: KeyEvent): Boolean {
                    if (event.action == KeyEvent.ACTION_DOWN && sessionPtr != 0L) {
                        return this@RemoteViewActivity.dispatchKeyEvent(event)
                    }
                    return true
                }
            }
        }

        private fun isMouse(event: MotionEvent): Boolean {
            return event.isFromSource(InputDevice.SOURCE_MOUSE) ||
                (event.pointerCount > 0 && event.getToolType(0) == MotionEvent.TOOL_TYPE_MOUSE)
        }

        private fun mapMouseButton(event: MotionEvent): Int {
            return when {
                (event.buttonState and MotionEvent.BUTTON_SECONDARY) != 0 ||
                    event.actionButton == MotionEvent.BUTTON_SECONDARY -> 2
                (event.buttonState and MotionEvent.BUTTON_TERTIARY) != 0 ||
                    event.actionButton == MotionEvent.BUTTON_TERTIARY -> 3
                else -> 1
            }
        }

        override fun onGenericMotionEvent(event: MotionEvent): Boolean {
            if (sessionPtr == 0L) return super.onGenericMotionEvent(event)
            if (isMouse(event)) {
                when (event.actionMasked) {
                    MotionEvent.ACTION_HOVER_MOVE -> {
                        TwBridge.nativeTouch(sessionPtr, TwBridge.POINTER_MOVE, event.x, event.y, 0)
                        return true
                    }
                    MotionEvent.ACTION_SCROLL -> {
                        val vx = -event.getAxisValue(MotionEvent.AXIS_HSCROLL) * 40f
                        val vy = -event.getAxisValue(MotionEvent.AXIS_VSCROLL) * 45f
                        TwBridge.nativeWheel(sessionPtr, event.x, event.y, vx, vy)
                        return true
                    }
                }
            }
            return super.onGenericMotionEvent(event)
        }

        override fun onTouchEvent(event: MotionEvent): Boolean {
            scaleDetector.onTouchEvent(event)
            if (sessionPtr == 0L) return true

            // Real hardware mouse: direct down / move / up + drag-selection + right/middle buttons.
            if (isMouse(event)) {
                when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        requestFocus()
                        TwBridge.nativeTouch(sessionPtr, TwBridge.POINTER_DOWN, event.x, event.y, mapMouseButton(event))
                    }
                    MotionEvent.ACTION_MOVE -> {
                        TwBridge.nativeTouch(sessionPtr, TwBridge.POINTER_MOVE, event.x, event.y, 0)
                    }
                    MotionEvent.ACTION_UP -> {
                        TwBridge.nativeTouch(sessionPtr, TwBridge.POINTER_UP, event.x, event.y, mapMouseButton(event))
                    }
                }
                return true
            }

            if (event.pointerCount >= 2) {
                twoFinger = true
                if (event.actionMasked == MotionEvent.ACTION_MOVE) {
                    val dx = event.x - lastX
                    val dy = event.y - lastY
                    TwBridge.nativePanZoom(sessionPtr, 1f, dx, dy)
                    TwBridge.nativeRenderFrame(sessionPtr)
                }
                lastX = event.x
                lastY = event.y
                return true
            }
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.x
                    downY = event.y
                    lastX = event.x
                    lastY = event.y
                    moved = false
                    twoFinger = false
                }
                MotionEvent.ACTION_MOVE -> {
                    if (!twoFinger) {
                        val dx = event.x - lastX
                        val dy = event.y - lastY
                        if (!moved && (abs(event.x - downX) > touchSlop || abs(event.y - downY) > touchSlop)) {
                            moved = true
                        }
                        if (moved) {
                            TwBridge.nativeWheel(sessionPtr, downX, downY, -dx, -dy)
                        }
                        lastX = event.x
                        lastY = event.y
                    }
                }
                MotionEvent.ACTION_UP -> {
                    if (!twoFinger && !moved) {
                        TwBridge.nativeTouch(sessionPtr, TwBridge.POINTER_DOWN, event.x, event.y, 1)
                        TwBridge.nativeTouch(sessionPtr, TwBridge.POINTER_UP, event.x, event.y, 1)
                    }
                    twoFinger = false
                }
            }
            return true
        }
    }
}
