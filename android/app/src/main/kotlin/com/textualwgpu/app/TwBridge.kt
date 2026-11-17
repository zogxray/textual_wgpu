package com.textualwgpu.app

import android.view.Surface

interface TwCallback {
    fun onRedraw()
    fun onStatus(msg: String)
    fun onClipboard(text: String)
    fun onAudioPcm(sampleRate: Int, channels: Int, pcm: ByteArray)
    fun onAudioStop()
    fun onVideoNalu(surfId: Int, codecFlag: Int, ptsUs: Long, isKeyframe: Boolean, data: ByteArray)
    fun onOpenClientFile(accept: String)
    fun onAppList(ids: Array<String>, names: Array<String>, descriptions: Array<String>, kinds: Array<String>)
    fun onLaunchStatus(ok: Boolean, appId: String, message: String)
    fun onFsList(reqPath: String)
    fun onFsGet(pathOrUri: String)
}

object TwBridge {
    init {
        System.loadLibrary("tw_ffi")
    }

    const val POINTER_DOWN = 1
    const val POINTER_MOVE = 2
    const val POINTER_UP = 3

    const val VID_CODEC_H264 = 0x10
    const val VID_CODEC_HEVC = 0x20
    const val VID_CODEC_AV1 = 0x30

    external fun nativeConnectSsh(
        host: String,
        port: Int,
        user: String,
        password: String,
        keyPem: String,
        remoteCmd: String,
        virtW: Int,
        virtH: Int,
        reversePort: Int,
        callback: TwCallback,
    ): Long

    external fun nativeConnectTcp(
        host: String,
        port: Int,
        virtW: Int,
        virtH: Int,
        callback: TwCallback,
    ): Long

    external fun nativeConnectOfflinePreview(
        virtW: Int,
        virtH: Int,
        callback: TwCallback,
    ): Long

    external fun nativeAttachSurface(ptr: Long, surface: Surface, width: Int, height: Int)
    external fun nativeResizeSurface(ptr: Long, width: Int, height: Int)
    external fun nativeDetachSurface(ptr: Long)
    external fun nativeRenderFrame(ptr: Long)
    external fun nativeTouch(ptr: Long, kind: Int, x: Float, y: Float, button: Int)
    external fun nativeWheel(ptr: Long, x: Float, y: Float, dx: Float, dy: Float)
    external fun nativePanZoom(ptr: Long, zoomFactor: Float, dpx: Float, dpy: Float)
    external fun nativeResetView(ptr: Long)
    external fun nativeKey(ptr: Long, key: String)
    external fun nativeChar(ptr: Long, ch: String)
    external fun nativePaste(ptr: Long, text: String)
    external fun nativePushDecodedYuv(
        ptr: Long,
        surfId: Int,
        width: Int,
        height: Int,
        y: ByteArray,
        u: ByteArray,
        v: ByteArray,
        yStride: Int,
        uStride: Int,
        vStride: Int,
        uvPixStride: Int,
    )
    external fun nativeSendClientFile(ptr: Long, name: String, data: ByteArray)
    external fun nativeListApps(ptr: Long)
    external fun nativeLaunchApp(ptr: Long, appId: String, args: String)
    external fun nativeSendFsEntries(
        ptr: Long,
        reqPath: String,
        isDirs: ByteArray,
        names: Array<String>,
        paths: Array<String>,
        sizes: LongArray,
    )
    external fun nativeDisconnect(ptr: Long)
}
