package com.textualwgpu.app

import android.content.Context
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.net.InetSocketAddress
import java.net.Socket

object LocalPythonHost {
    const val PORT = 7420
    @Volatile
    private var started = false

    @Synchronized
    fun ensureStarted(ctx: Context): Boolean {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(ctx.applicationContext))
        }
        if (!started) {
            started = true
            val cachePath = ctx.cacheDir.absolutePath
            val t = Thread {
                try {
                    val py = Python.getInstance()
                    val osMod = py.getModule("os")
                    val env = osMod["environ"]
                    env?.callAttr("__setitem__", "TMPDIR", cachePath)
                    env?.callAttr("__setitem__", "HOME", ctx.filesDir.absolutePath)
                    val code = """
import asyncio
from textual_wgpu.demo import build_demo_app
from textual_wgpu.remote.cli import serve_tcp_session

asyncio.run(serve_tcp_session(build_demo_app(), host="127.0.0.1", port=$PORT, advertise=False))
"""
                    py.getBuiltins().callAttr("exec", code, py.getModule("__main__")["__dict__"])
                } catch (e: Throwable) {
                    Log.e("LocalPythonHost", "Python host crashed", e)
                    started = false
                }
            }
            t.isDaemon = true
            t.start()
        }

        // Wait briefly for 127.0.0.1:7420 to accept connections
        val deadline = System.currentTimeMillis() + 6000L
        while (System.currentTimeMillis() < deadline) {
            try {
                Socket().use { s ->
                    s.connect(InetSocketAddress("127.0.0.1", PORT), 150)
                    return true
                }
            } catch (_: Throwable) {
                Thread.sleep(50)
            }
        }
        return false
    }
}
