package com.textualwgpu.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder

object LocalResourceServer {
    const val PORT = 7423
    @Volatile
    private var started = false
    private var serverSocket: ServerSocket? = null

    @Synchronized
    fun ensureStarted(ctx: Context) {
        val ac = ctx.applicationContext
        LocalMediaHelper.setAppContext(ac)
        if (started) return
        started = true
        Thread({
            try {
                val srv = ServerSocket(PORT, 32, InetAddress.getByName("127.0.0.1")).apply {
                    reuseAddress = true
                }
                serverSocket = srv
                TwLog.log("TwRes", "ResourceServer listening on 127.0.0.1:$PORT")
                while (!srv.isClosed) {
                    val conn = try {
                        srv.accept()
                    } catch (_: Throwable) {
                        break
                    }
                    Thread({ handleConn(ac, conn) }, "tw-res-conn").apply {
                        isDaemon = true
                        start()
                    }
                }
            } catch (t: Throwable) {
                TwLog.err("TwRes", "ResourceServer failed", t)
                started = false
            }
        }, "tw-res-srv").apply {
            isDaemon = true
            start()
        }
    }

    private fun readLine(inp: InputStream, maxBytes: Int = 65536): String? {
        val buf = ByteArrayOutputStream()
        while (buf.size() < maxBytes) {
            val b = inp.read()
            if (b < 0) {
                return if (buf.size() > 0) buf.toString(Charsets.UTF_8.name()) else null
            }
            if (b == '\n'.code) {
                return buf.toString(Charsets.UTF_8.name())
            }
            buf.write(b)
        }
        return buf.toString(Charsets.UTF_8.name())
    }

    private fun readExact(inp: InputStream, dst: ByteArray, n: Int): Boolean {
        var off = 0
        while (off < n) {
            val r = inp.read(dst, off, n - off)
            if (r <= 0) return false
            off += r
        }
        return true
    }

    private fun writeJsonLine(out: BufferedOutputStream, obj: JSONObject) {
        out.write((obj.toString() + "\n").toByteArray(Charsets.UTF_8))
        out.flush()
    }

    private fun handleConn(ctx: Context, conn: Socket) {
        try {
            conn.soTimeout = 5000
            val inp = conn.getInputStream()
            val out = BufferedOutputStream(conn.getOutputStream(), 65536)
            val line = readLine(inp) ?: return
            val req = JSONObject(line)
            when (req.optString("op")) {
                "list" -> {
                    val prof = JSONObject(LocalMediaHelper.deviceProfileJson())
                    val cams = prof.optString("cameras", "front=Front Camera;back=Back Camera")
                    val resArr = JSONArray()
                    for (part in cams.split(";")) {
                        val p = part.trim()
                        if (p.isEmpty()) continue
                        val eq = p.indexOf('=')
                        val cid = if (eq >= 0) p.substring(0, eq).trim() else p
                        val lbl = if (eq >= 0) p.substring(eq + 1).trim().ifEmpty { cid } else p
                        resArr.put(JSONObject().apply {
                            put("kind", "camera")
                            put("id", cid)
                            put("label", lbl)
                        })
                    }
                    resArr.put(JSONObject().apply {
                        put("kind", "mic")
                        put("id", "default")
                        put("label", "Microphone")
                    })
                    resArr.put(JSONObject().apply {
                        put("kind", "speaker")
                        put("id", "default")
                        put("label", "Speaker")
                    })
                    resArr.put(JSONObject().apply {
                        put("kind", "clipboard")
                        put("id", "default")
                        put("label", "Clipboard")
                    })
                    resArr.put(JSONObject().apply {
                        put("kind", "files")
                        put("id", "fs")
                        put("label", "Files & Media")
                    })
                    resArr.put(JSONObject().apply {
                        put("kind", "gpu")
                        put("id", "vulkan")
                        put("label", "Vulkan Mobile GPU (${android.os.Build.HARDWARE ?: "SoC"})")
                    })
                    val dev = JSONObject().apply {
                        put("device_id", prof.optString("id"))
                        put("name", prof.optString("name"))
                        put("kind", "android")
                        put("icon", "📱")
                    }
                    writeJsonLine(out, JSONObject().apply {
                        put("ok", true)
                        put("device", dev)
                        put("resources", resArr)
                    })
                }
                "fs_list" -> {
                    val reqPath = req.optString("path", "")
                    val items = LocalMediaHelper.listMediaFolder(ctx, reqPath)
                    val arr = JSONArray()
                    for (it in items) {
                        arr.put(JSONObject().apply {
                            put("d", it.isDir)
                            put("n", it.name)
                            put("p", it.path)
                            put("s", it.size)
                        })
                    }
                    writeJsonLine(out, JSONObject().apply {
                        put("ok", true)
                        put("path", reqPath)
                        put("entries", arr)
                    })
                }
                "fs_get" -> {
                    val reqPath = req.optString("path", "")
                    val res = LocalMediaHelper.readMediaFile(ctx, reqPath)
                    if (res == null) {
                        writeJsonLine(out, JSONObject().apply {
                            put("ok", false)
                            put("error", "not found")
                        })
                        return
                    }
                    writeJsonLine(out, JSONObject().apply {
                        put("ok", true)
                        put("name", res.first)
                        put("size", res.second.size)
                    })
                    out.write(res.second)
                    out.flush()
                }
                "clip_get" -> {
                    val txt = LocalMediaHelper.getClipboardText(ctx)
                    writeJsonLine(out, JSONObject().apply {
                        put("ok", true)
                        put("text", txt)
                    })
                }
                "clip_set" -> {
                    val txt = req.optString("text", "")
                    LocalMediaHelper.setClipboardText(ctx, txt)
                    writeJsonLine(out, JSONObject().apply { put("ok", true) })
                }
                "play" -> {
                    val sr = req.optInt("sr", 16000)
                    val ch = req.optInt("ch", 1)
                    val track = LocalMediaHelper.createSpeakerTrack(sr, ch)
                    if (track == null) {
                        writeJsonLine(out, JSONObject().apply {
                            put("ok", false)
                            put("error", "speaker unavailable")
                        })
                        return
                    }
                    conn.soTimeout = 0
                    writeJsonLine(out, JSONObject().apply { put("ok", true) })
                    val hdr = ByteArray(13)
                    try {
                        while (!conn.isClosed) {
                            if (!readExact(inp, hdr, 13)) break
                            val bb = ByteBuffer.wrap(hdr).order(ByteOrder.LITTLE_ENDIAN)
                            bb.getLong() // pts
                            bb.get() // flags
                            val plen = bb.getInt()
                            if (plen <= 0 || plen > 4 * 1024 * 1024) {
                                if (plen == 0) continue else break
                            }
                            val pcm = ByteArray(plen)
                            if (!readExact(inp, pcm, plen)) break
                            track.write(pcm, 0, pcm.size)
                        }
                    } finally {
                        try { track.stop() } catch (_: Throwable) {}
                        try { track.release() } catch (_: Throwable) {}
                    }
                }
                "tunnel_tcp" -> {
                    val tport = req.optInt("port", 0)
                    if (tport <= 1024 || tport > 65535) {
                        writeJsonLine(out, JSONObject().apply {
                            put("ok", false)
                            put("error", "invalid port")
                        })
                        return
                    }
                    val up = Socket("127.0.0.1", tport)
                    conn.soTimeout = 0
                    writeJsonLine(out, JSONObject().apply { put("ok", true) })
                    val upIn = up.getInputStream()
                    val upOut = up.getOutputStream()
                    val t1 = Thread({
                        try {
                            val buf = ByteArray(65536)
                            while (true) {
                                val n = inp.read(buf)
                                if (n <= 0) break
                                upOut.write(buf, 0, n)
                                upOut.flush()
                            }
                        } catch (_: Throwable) {
                        } finally {
                            try { up.close() } catch (_: Throwable) {}
                        }
                    }, "tw-tun-tx").apply { isDaemon = true; start() }
                    try {
                        val buf = ByteArray(65536)
                        while (true) {
                            val n = upIn.read(buf)
                            if (n <= 0) break
                            out.write(buf, 0, n)
                            out.flush()
                        }
                    } finally {
                        try { up.close() } catch (_: Throwable) {}
                        t1.interrupt()
                    }
                }
                "sub" -> {
                    val kind = req.optString("kind")
                    val id = req.optString("id")
                    conn.soTimeout = 0
                    if (kind == "camera") {
                        if (ctx.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
                            writeJsonLine(out, JSONObject().apply {
                                put("ok", false)
                                put("error", "CAMERA permission not granted")
                            })
                            return
                        }
                        val facing = if (id == "back" || id == "2") 2 else 1
                        val sid = LocalMediaHelper.subscribeCamera(ctx, facing)
                        if (sid <= 0) {
                            writeJsonLine(out, JSONObject().apply {
                                put("ok", false)
                                put("error", "camera unavailable")
                            })
                            return
                        }
                        writeJsonLine(out, JSONObject().apply { put("ok", true) })
                        val eofWatch = Thread({
                            try {
                                while (inp.read() >= 0) {
                                    // wait for client disconnect
                                }
                            } catch (_: Throwable) {
                            } finally {
                                LocalMediaHelper.unsubscribeCamera(sid)
                            }
                        }, "tw-res-cam-eof").apply {
                            isDaemon = true
                            start()
                        }
                        val hdrBuf = ByteBuffer.allocate(13).order(ByteOrder.LITTLE_ENDIAN)
                        val t0 = System.nanoTime()
                        try {
                            while (!conn.isClosed) {
                                val pkt = LocalMediaHelper.takeCameraFrame(sid) ?: break
                                if (pkt.size <= 1) continue
                                val h = pkt[0].toInt() and 0xFF
                                val isKf = (h and 0x01) != 0
                                val rotIdx = (h shr 1) and 0x03
                                val flags = TwBridge.VID_CODEC_H264 or (rotIdx shl 2) or (if (isKf) 0x01 else 0x00)
                                val ptsUs = (System.nanoTime() - t0) / 1000L
                                val plen = pkt.size - 1
                                hdrBuf.clear()
                                hdrBuf.putLong(ptsUs)
                                hdrBuf.put(flags.toByte())
                                hdrBuf.putInt(plen)
                                out.write(hdrBuf.array(), 0, 13)
                                out.write(pkt, 1, plen)
                                out.flush()
                            }
                        } finally {
                            LocalMediaHelper.unsubscribeCamera(sid)
                            eofWatch.interrupt()
                        }
                    } else if (kind == "mic") {
                        if (ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                            writeJsonLine(out, JSONObject().apply {
                                put("ok", false)
                                put("error", "RECORD_AUDIO permission not granted")
                            })
                            return
                        }
                        val sid = LocalMediaHelper.subscribeMic(ctx, 16000)
                        if (sid <= 0) {
                            writeJsonLine(out, JSONObject().apply {
                                put("ok", false)
                                put("error", "mic unavailable")
                            })
                            return
                        }
                        writeJsonLine(out, JSONObject().apply { put("ok", true) })
                        val eofWatch = Thread({
                            try {
                                while (inp.read() >= 0) {
                                    // wait for client disconnect
                                }
                            } catch (_: Throwable) {
                            } finally {
                                LocalMediaHelper.unsubscribeMic(sid)
                            }
                        }, "tw-res-mic-eof").apply {
                            isDaemon = true
                            start()
                        }
                        val hdrBuf = ByteBuffer.allocate(13).order(ByteOrder.LITTLE_ENDIAN)
                        val t0 = System.nanoTime()
                        try {
                            while (!conn.isClosed) {
                                val pcm = LocalMediaHelper.takeMicChunk(sid) ?: break
                                if (pcm.isEmpty()) continue
                                val ptsUs = (System.nanoTime() - t0) / 1000L
                                hdrBuf.clear()
                                hdrBuf.putLong(ptsUs)
                                hdrBuf.put(1.toByte())
                                hdrBuf.putInt(pcm.size)
                                out.write(hdrBuf.array(), 0, 13)
                                out.write(pcm)
                                out.flush()
                            }
                        } finally {
                            LocalMediaHelper.unsubscribeMic(sid)
                            eofWatch.interrupt()
                        }
                    } else {
                        writeJsonLine(out, JSONObject().apply {
                            put("ok", false)
                            put("error", "unknown resource kind")
                        })
                    }
                }
                else -> {
                    writeJsonLine(out, JSONObject().apply {
                        put("ok", false)
                        put("error", "unknown op")
                    })
                }
            }
        } catch (_: Throwable) {
        } finally {
            try {
                conn.close()
            } catch (_: Throwable) {
            }
        }
    }
}
