//! C-ABI and Android JNI bindings for ``textual-wgpu`` Rust core.

use std::ffi::CStr;
use std::os::raw::c_char;
use std::sync::{Arc, Mutex};

pub use tw_client::ClientHandle;
pub use tw_proto::{Frame, Op, Priority};
pub use tw_render::{GpuCompositor, SceneState, ViewportTransform};

#[no_mangle]
pub extern "C" fn tw_proto_version() -> u32 {
    tw_proto::PROTOCOL_VERSION as u32
}

#[no_mangle]
pub unsafe extern "C" fn tw_send_key(ptr: *const ClientHandle, key_utf8: *const c_char) {
    if ptr.is_null() || key_utf8.is_null() {
        return;
    }
    let handle = unsafe { &*ptr };
    if let Ok(s) = unsafe { CStr::from_ptr(key_utf8) }.to_str() {
        handle.send_key(s);
    }
}

pub struct RemoteSession {
    pub client: Option<ClientHandle>,
    pub offline_state: Option<Arc<Mutex<SceneState>>>,
    pub transform: ViewportTransform,
    pub surface_size: (u32, u32),
    pub server_size: (u32, u32),
    pub gpu: Option<GpuState>,
}

pub struct GpuState {
    pub instance: wgpu::Instance,
    pub surface: wgpu::Surface<'static>,
    pub device: wgpu::Device,
    pub queue: wgpu::Queue,
    pub config: wgpu::SurfaceConfiguration,
    pub compositor: GpuCompositor,
    #[cfg(target_os = "android")]
    pub _native_window: ndk::native_window::NativeWindow,
}

impl RemoteSession {
    pub fn state_arc(&self) -> Option<Arc<Mutex<SceneState>>> {
        if let Some(c) = &self.client {
            Some(Arc::clone(&c.state))
        } else {
            self.offline_state.as_ref().map(Arc::clone)
        }
    }
}

#[cfg(target_os = "android")]
mod android_jni {
    use super::*;
    use jni::objects::{GlobalRef, JByteArray, JClass, JObject, JString};
    use jni::sys::{jfloat, jint, jlong};
    use jni::{JNIEnv, JavaVM};
    use raw_window_handle::{
        AndroidDisplayHandle, AndroidNdkWindowHandle, RawDisplayHandle, RawWindowHandle,
    };
    use std::ptr::NonNull;
    use tw_client::{spawn_ssh_session_cbs, spawn_tcp_session_cbs, ClientCallbacks, SshConfig};
    use tw_proto::{DrawOp, RECT_FLAG_FILL, RECT_FLAG_OUTLINE};
    use tw_render::render_mesh_rgba;

    fn build_callbacks(vm: Arc<JavaVM>, cb_ref: Arc<GlobalRef>) -> ClientCallbacks {
        let vm_r = Arc::clone(&vm);
        let cb_r = Arc::clone(&cb_ref);
        let vm_s = Arc::clone(&vm);
        let cb_s = Arc::clone(&cb_ref);
        let vm_c = Arc::clone(&vm);
        let cb_c = Arc::clone(&cb_ref);
        let vm_a = Arc::clone(&vm);
        let cb_a = Arc::clone(&cb_ref);
        let vm_q = Arc::clone(&vm);
        let cb_q = Arc::clone(&cb_ref);
        let vm_v = Arc::clone(&vm);
        let cb_v = Arc::clone(&cb_ref);
        let vm_f = Arc::clone(&vm);
        let cb_f = Arc::clone(&cb_ref);

        ClientCallbacks {
            on_commit: Arc::new(move || call_void(&vm_r, &cb_r, "onRedraw")),
            on_status: Arc::new(move |status| call_str(&vm_s, &cb_s, "onStatus", status)),
            on_clipboard: Arc::new(move |clip| call_str(&vm_c, &cb_c, "onClipboard", clip)),
            on_audio: Arc::new(move |sr, ch, pcm| {
                if let Ok(mut env) = vm_a.attach_current_thread() {
                    if let Ok(arr) = env.byte_array_from_slice(pcm) {
                        if let Err(e) = env.call_method(
                            cb_a.as_obj(),
                            "onAudioPcm",
                            "(II[B)V",
                            &[(sr as jint).into(), (ch as jint).into(), (&arr).into()],
                        ) {
                            let _ = env.exception_clear();
                            call_str(&vm_a, &cb_a, "onStatus", &format!("Audio JNI err: {e}"));
                        }
                    }
                }
            }),
            on_audio_stop: Arc::new(move || call_void(&vm_q, &cb_q, "onAudioStop")),
            on_video_nalu: Arc::new(move |surf_id, cflag, pts_us, is_kf, data| {
                if let Ok(mut env) = vm_v.attach_current_thread() {
                    if let Ok(arr) = env.byte_array_from_slice(data) {
                        if let Err(e) = env.call_method(
                            cb_v.as_obj(),
                            "onVideoNalu",
                            "(IIJZ[B)V",
                            &[
                                (surf_id as jint).into(),
                                (cflag as jint).into(),
                                (pts_us as jlong).into(),
                                (if is_kf { 1u8 } else { 0u8 }).into(),
                                (&arr).into(),
                            ],
                        ) {
                            let _ = env.exception_clear();
                            call_str(&vm_v, &cb_v, "onStatus", &format!("Video JNI err: {e}"));
                        }
                    }
                }
            }),
            on_open_client_file: Arc::new(move |accept| {
                call_str(&vm_f, &cb_f, "onOpenClientFile", accept);
            }),
        }
    }

    fn jstr(env: &mut JNIEnv, s: &JString) -> String {
        if s.is_null() {
            return String::new();
        }
        env.get_string(s).map(|x| x.into()).unwrap_or_default()
    }

    fn call_void(vm: &JavaVM, cb: &GlobalRef, method: &str) {
        if let Ok(mut env) = vm.attach_current_thread() {
            let _ = env.call_method(cb.as_obj(), method, "()V", &[]);
        }
    }

    fn call_str(vm: &JavaVM, cb: &GlobalRef, method: &str, msg: &str) {
        if let Ok(mut env) = vm.attach_current_thread() {
            if let Ok(js) = env.new_string(msg) {
                let _ = env.call_method(cb.as_obj(), method, "(Ljava/lang/String;)V", &[(&js).into()]);
            }
        }
    }

    fn pollster_block_on<F: std::future::Future>(fut: F) -> F::Output {
        use std::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};
        fn noop(_: *const ()) {}
        fn clone(p: *const ()) -> RawWaker {
            RawWaker::new(p, &VTABLE)
        }
        static VTABLE: RawWakerVTable = RawWakerVTable::new(clone, noop, noop, noop);
        let waker = unsafe { Waker::from_raw(RawWaker::new(std::ptr::null(), &VTABLE)) };
        let mut cx = Context::from_waker(&waker);
        let mut pinned = Box::pin(fut);
        loop {
            match pinned.as_mut().poll(&mut cx) {
                Poll::Ready(val) => return val,
                Poll::Pending => std::thread::yield_now(),
            }
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeConnectSsh(
        mut env: JNIEnv,
        _cls: JClass,
        host: JString,
        port: jint,
        user: JString,
        password: JString,
        key_pem: JString,
        remote_cmd: JString,
        virt_w: jint,
        virt_h: jint,
        reverse_port: jint,
        callback: JObject,
    ) -> jlong {
        let vm = Arc::new(env.get_java_vm().expect("JavaVM"));
        let cb_ref = Arc::new(env.new_global_ref(callback).expect("global ref"));
        let pw = jstr(&mut env, &password);
        let pem = jstr(&mut env, &key_pem);
        let cfg = SshConfig {
            host: jstr(&mut env, &host),
            port: port.max(1) as u16,
            user: jstr(&mut env, &user),
            password: if pw.is_empty() { None } else { Some(pw) },
            key_pem: if pem.is_empty() { None } else { Some(pem) },
            key_passphrase: None,
            remote_cmd: jstr(&mut env, &remote_cmd),
            reverse_forward_port: if reverse_port > 0 { Some(reverse_port as u16) } else { None },
        };

        let cbs = build_callbacks(Arc::clone(&vm), Arc::clone(&cb_ref));

        let virt_size = if virt_w > 0 && virt_h > 0 {
            Some((virt_w as u32, virt_h as u32))
        } else {
            None
        };
        let server_size = virt_size.unwrap_or((900, 600));

        match spawn_ssh_session_cbs(cfg, cbs) {
            Ok((client, _join)) => {
                let sess = Box::new(RemoteSession {
                    client: Some(client),
                    offline_state: None,
                    transform: ViewportTransform {
                        virtual_size: virt_size,
                        zoom: 1.0,
                        pan_u: 0.0,
                        pan_v: 0.0,
                    },
                    surface_size: (1, 1),
                    server_size,
                    gpu: None,
                });
                Box::into_raw(sess) as jlong
            }
            Err(e) => {
                call_str(&vm, &cb_ref, "onStatus", &format!("Spawn failed: {e}"));
                0
            }
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeConnectTcp(
        mut env: JNIEnv,
        _cls: JClass,
        host: JString,
        port: jint,
        virt_w: jint,
        virt_h: jint,
        callback: JObject,
    ) -> jlong {
        let vm = Arc::new(env.get_java_vm().expect("JavaVM"));
        let cb_ref = Arc::new(env.new_global_ref(callback).expect("global ref"));
        let h = jstr(&mut env, &host);
        let p = port.max(1) as u16;
        let cbs = build_callbacks(Arc::clone(&vm), Arc::clone(&cb_ref));

        let virt_size = if virt_w > 0 && virt_h > 0 {
            Some((virt_w as u32, virt_h as u32))
        } else {
            None
        };
        let server_size = virt_size.unwrap_or((900, 600));

        match spawn_tcp_session_cbs(h, p, cbs) {
            Ok((client, _join)) => {
                let sess = Box::new(RemoteSession {
                    client: Some(client),
                    offline_state: None,
                    transform: ViewportTransform {
                        virtual_size: virt_size,
                        zoom: 1.0,
                        pan_u: 0.0,
                        pan_v: 0.0,
                    },
                    surface_size: (1, 1),
                    server_size,
                    gpu: None,
                });
                Box::into_raw(sess) as jlong
            }
            Err(e) => {
                call_str(&vm, &cb_ref, "onStatus", &format!("TCP spawn failed: {e}"));
                0
            }
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeConnectOfflinePreview(
        env: JNIEnv,
        _cls: JClass,
        virt_w: jint,
        virt_h: jint,
        callback: JObject,
    ) -> jlong {
        let vm = env.get_java_vm().expect("JavaVM");
        let cb_ref = env.new_global_ref(callback).expect("global ref");
        let w = if virt_w > 0 { virt_w as u32 } else { 1100 };
        let h = if virt_h > 0 { virt_h as u32 } else { 720 };
        let mut st = SceneState::default();
        st.title = "textual-wgpu · Android Preview".into();
        st.bg_rgba = [22, 20, 28, 255];
        st.styles.insert(
            1,
            tw_proto::StyleDef {
                style_id: 1,
                fg: 0xF5E6CCFF,
                bg: 0,
                flags: 0,
                font_id: 0,
                size: 16,
            },
        );
        let ops = vec![
            DrawOp::Rect {
                flags: RECT_FLAG_FILL | RECT_FLAG_OUTLINE,
                x0: 24,
                y0: 24,
                x1: (w as i16) - 24,
                y1: 96,
                fill: 0x2A2436FF,
                outline: 0xD4AD72FF,
                width: 2,
            },
            DrawOp::Text {
                x: 44,
                y: 48,
                style_id: 1,
                text: "textual-wgpu Android Core (Vulkan) · Offline Preview".into(),
            },
            DrawOp::ClearRect {
                x0: 24,
                y0: 120,
                x1: (w as i16) - 24,
                y1: (h as i16) - 24,
            },
            DrawOp::Rect {
                flags: RECT_FLAG_OUTLINE,
                x0: 24,
                y0: 120,
                x1: (w as i16) - 24,
                y1: (h as i16) - 24,
                fill: 0,
                outline: 0x6CB2EBFF,
                width: 2,
            },
        ];
        st.layers.insert("preview".into(), ([0, 0, w as i16, h as i16], ops));
        st.order = vec!["preview".into()];
        let pw = w.saturating_sub(50).max(64);
        let ph = h.saturating_sub(146).max(64);
        st.planes.insert(
            1,
            tw_render::PlaneData {
                rect: [25, 121, (w as i16) - 25, (h as i16) - 25],
                kind: tw_proto::PLANE_KIND_3D_CLIENT,
                flags: 0,
                rgba: render_mesh_rgba(pw, ph, 0.6, 0.35, 1.0, [18, 22, 30, 255], [108, 178, 235]),
                size: (pw, ph),
            },
        );
        call_str(&vm, &cb_ref, "onStatus", "Offline Vulkan preview ready");
        call_void(&vm, &cb_ref, "onRedraw");
        let sess = Box::new(RemoteSession {
            client: None,
            offline_state: Some(Arc::new(Mutex::new(st))),
            transform: ViewportTransform {
                virtual_size: Some((w, h)),
                zoom: 1.0,
                pan_u: 0.0,
                pan_v: 0.0,
            },
            surface_size: (1, 1),
            server_size: (w, h),
            gpu: None,
        });
        Box::into_raw(sess) as jlong
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeAttachSurface(
        env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
        surface_obj: JObject,
        width: jint,
        height: jint,
    ) {
        if ptr == 0 || surface_obj.is_null() {
            return;
        }
        let sess = unsafe { &mut *(ptr as *mut RemoteSession) };
        let Some(nw) = (unsafe { ndk::native_window::NativeWindow::from_surface(env.get_raw(), surface_obj.as_raw()) }) else {
            return;
        };
        let Some(win_ptr) = NonNull::new(nw.ptr().as_ptr() as *mut std::ffi::c_void) else {
            return;
        };
        let instance = wgpu::Instance::default();
        let raw_display = RawDisplayHandle::Android(AndroidDisplayHandle::new());
        let raw_window = RawWindowHandle::AndroidNdk(AndroidNdkWindowHandle::new(win_ptr));
        let surface = match unsafe {
            instance.create_surface_unsafe(wgpu::SurfaceTargetUnsafe::RawHandle {
                raw_display_handle: raw_display,
                raw_window_handle: raw_window,
            })
        } {
            Ok(s) => s,
            Err(_) => return,
        };
        let adapter = match pollster_block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
            compatible_surface: Some(&surface),
            ..Default::default()
        })) {
            Some(a) => a,
            None => return,
        };
        let Ok((device, queue)) = pollster_block_on(adapter.request_device(&Default::default(), None)) else {
            return;
        };
        let sw = width.max(1) as u32;
        let sh = height.max(1) as u32;
        sess.surface_size = (sw, sh);
        if let Some(vs) = sess.transform.virtual_size {
            sess.server_size = vs;
        } else {
            // Scale dense mobile displays cleanly (~2x logical density)
            let scale = if sw > 900 || sh > 900 { 2 } else { 1 };
            sess.server_size = ((sw / scale).max(320), (sh / scale).max(240));
        }
        if let Some(c) = &sess.client {
            c.send_resize(sess.server_size.0, sess.server_size.1);
        }

        let caps = surface.get_capabilities(&adapter);
        let fmt = caps.formats.iter().copied().find(|f| !f.is_srgb()).unwrap_or(caps.formats[0]);
        let config = wgpu::SurfaceConfiguration {
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            format: fmt,
            width: sw,
            height: sh,
            present_mode: wgpu::PresentMode::AutoVsync,
            alpha_mode: caps.alpha_modes[0],
            view_formats: vec![],
            desired_maximum_frame_latency: 2,
        };
        surface.configure(&device, &config);
        let compositor = GpuCompositor::new(&device, fmt);
        sess.gpu = Some(GpuState {
            instance,
            surface,
            device,
            queue,
            config,
            compositor,
            _native_window: nw,
        });
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeResizeSurface(
        _env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
        width: jint,
        height: jint,
    ) {
        if ptr == 0 || width <= 0 || height <= 0 {
            return;
        }
        let sess = unsafe { &mut *(ptr as *mut RemoteSession) };
        let sw = width as u32;
        let sh = height as u32;
        sess.surface_size = (sw, sh);
        if let Some(gpu) = &mut sess.gpu {
            gpu.config.width = sw;
            gpu.config.height = sh;
            gpu.surface.configure(&gpu.device, &gpu.config);
        }
        if sess.transform.virtual_size.is_none() {
            let scale = if sw > 900 || sh > 900 { 2 } else { 1 };
            sess.server_size = ((sw / scale).max(320), (sh / scale).max(240));
            if let Some(c) = &sess.client {
                c.send_resize(sess.server_size.0, sess.server_size.1);
            }
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeDetachSurface(
        _env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { &mut *(ptr as *mut RemoteSession) };
        sess.gpu = None;
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeRenderFrame(
        _env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { &mut *(ptr as *mut RemoteSession) };
        let Some(state_arc) = sess.state_arc() else { return };
        let Some(gpu) = &mut sess.gpu else { return };
        let Ok(frame) = gpu.surface.get_current_texture() else {
            return;
        };
        let view = frame.texture.create_view(&Default::default());
        if let Ok(st) = state_arc.lock() {
            gpu.compositor.render_state_with_transform(
                &gpu.device,
                &gpu.queue,
                &view,
                &st,
                sess.server_size,
                sess.surface_size,
                &sess.transform,
            );
        }
        frame.present();
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeTouch(
        _env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
        kind: jint,
        x: jfloat,
        y: jfloat,
        button: jint,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { &*(ptr as *mut RemoteSession) };
        if let Some(c) = &sess.client {
            let (sx, sy) = sess.transform.screen_to_server(x, y, sess.surface_size, sess.server_size);
            c.send_pointer(kind as u8, sx, sy, button as u8);
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeWheel(
        _env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
        x: jfloat,
        y: jfloat,
        dx: jfloat,
        dy: jfloat,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { &*(ptr as *mut RemoteSession) };
        if let Some(c) = &sess.client {
            let (sx, sy) = sess.transform.screen_to_server(x, y, sess.surface_size, sess.server_size);
            c.send_wheel(sx, sy, dx, dy);
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativePanZoom(
        _env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
        zoom_factor: jfloat,
        dpx: jfloat,
        dpy: jfloat,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { &mut *(ptr as *mut RemoteSession) };
        sess.transform.apply_pan_zoom(zoom_factor, dpx, dpy, sess.surface_size);
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeResetView(
        _env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { &mut *(ptr as *mut RemoteSession) };
        sess.transform.reset();
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeKey(
        mut env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
        key: JString,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { &*(ptr as *mut RemoteSession) };
        let s = jstr(&mut env, &key);
        if let Some(c) = &sess.client {
            c.send_key(&s);
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeChar(
        mut env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
        ch: JString,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { &*(ptr as *mut RemoteSession) };
        let s = jstr(&mut env, &ch);
        if let Some(c) = &sess.client {
            c.send_char(&s);
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativePaste(
        mut env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
        text: JString,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { &*(ptr as *mut RemoteSession) };
        let s = jstr(&mut env, &text);
        if let Some(c) = &sess.client {
            c.send_paste(&s);
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativePushDecodedYuv(
        env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
        surf_id: jint,
        width: jint,
        height: jint,
        y_arr: JByteArray,
        u_arr: JByteArray,
        v_arr: JByteArray,
        y_stride: jint,
        u_stride: jint,
        v_stride: jint,
        uv_pix_stride: jint,
    ) {
        if ptr == 0 || width <= 0 || height <= 0 {
            return;
        }
        let sess = unsafe { &*(ptr as *mut RemoteSession) };
        let Some(state_arc) = sess.state_arc() else { return };
        let Ok(y) = env.convert_byte_array(&y_arr) else { return };
        let Ok(u) = env.convert_byte_array(&u_arr) else { return };
        let Ok(v) = env.convert_byte_array(&v_arr) else { return };
        if let Ok(mut st) = state_arc.lock() {
            st.push_decoded_yuv420(
                surf_id as u32,
                width as u32,
                height as u32,
                &y,
                &u,
                &v,
                y_stride.max(1) as usize,
                u_stride.max(1) as usize,
                v_stride.max(1) as usize,
                uv_pix_stride.max(1) as usize,
            );
        };
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeSendClientFile(
        mut env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
        name: JString,
        data: JByteArray,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { &*(ptr as *mut RemoteSession) };
        let fname = jstr(&mut env, &name);
        let Ok(bytes) = env.convert_byte_array(&data) else { return };
        if let Some(c) = &sess.client {
            c.send_client_file(&fname, &bytes);
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_textualwgpu_app_TwBridge_nativeDisconnect(
        _env: JNIEnv,
        _cls: JClass,
        ptr: jlong,
    ) {
        if ptr == 0 {
            return;
        }
        let sess = unsafe { Box::from_raw(ptr as *mut RemoteSession) };
        if let Some(c) = &sess.client {
            c.quit();
        }
    }
}
