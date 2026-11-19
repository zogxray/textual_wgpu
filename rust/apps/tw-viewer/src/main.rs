use std::collections::HashMap;
use std::io::{BufRead, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use tw_client::{
    load_saved_nodes, spawn_command_session_cbs, spawn_tcp_session_cbs, ClientCallbacks,
    ClientHandle,
};
use tw_proto::{
    FsEntry, POINTER_DOWN, POINTER_MOVE, POINTER_UP, VID_CODEC_AV1, VID_CODEC_HEVC,
};
use tw_render::{GpuCompositor, SceneState};
use winit::application::ApplicationHandler;
use winit::dpi::LogicalSize;
use winit::event::{ElementState, KeyEvent, MouseButton, MouseScrollDelta, WindowEvent};
use winit::event_loop::{ActiveEventLoop, EventLoop, EventLoopProxy};
use winit::keyboard::{Key, ModifiersState, NamedKey};
use winit::window::{Window, WindowId};

#[derive(Debug, Clone, Copy)]
enum UserEvent {
    Redraw,
    Connected,
    Focus,
}

struct ViewerApp {
    client: ClientHandle,
    proxy: EventLoopProxy<UserEvent>,
    window: Option<Arc<Window>>,
    instance: wgpu::Instance,
    surface: Option<wgpu::Surface<'static>>,
    device: Option<wgpu::Device>,
    queue: Option<wgpu::Queue>,
    config: Option<wgpu::SurfaceConfiguration>,
    compositor: Option<GpuCompositor>,
    modifiers: ModifiersState,
    cursor_pos: (f64, f64),
    size: (u32, u32),
}

impl ViewerApp {
    fn new(client: ClientHandle, proxy: EventLoopProxy<UserEvent>) -> Self {
        Self {
            client,
            proxy,
            window: None,
            instance: wgpu::Instance::default(),
            surface: None,
            device: None,
            queue: None,
            config: None,
            compositor: None,
            modifiers: ModifiersState::empty(),
            cursor_pos: (0.0, 0.0),
            size: (1100, 720),
        }
    }
}

fn map_named_key(n: NamedKey) -> Option<&'static str> {
    Some(match n {
        NamedKey::Enter => "enter",
        NamedKey::Escape => "escape",
        NamedKey::Tab => "tab",
        NamedKey::Backspace => "backspace",
        NamedKey::Delete => "delete",
        NamedKey::ArrowUp => "up",
        NamedKey::ArrowDown => "down",
        NamedKey::ArrowLeft => "left",
        NamedKey::ArrowRight => "right",
        NamedKey::Home => "home",
        NamedKey::End => "end",
        NamedKey::PageUp => "pageup",
        NamedKey::PageDown => "pagedown",
        NamedKey::Space => "space",
        NamedKey::F1 => "f1",
        NamedKey::F2 => "f2",
        NamedKey::F3 => "f3",
        NamedKey::F4 => "f4",
        NamedKey::F5 => "f5",
        NamedKey::F6 => "f6",
        NamedKey::F7 => "f7",
        NamedKey::F8 => "f8",
        NamedKey::F9 => "f9",
        NamedKey::F10 => "f10",
        _ => return None,
    })
}

fn build_window_icon() -> Option<winit::window::Icon> {
    let sz = 64usize;
    let mut rgba = vec![0u8; sz * sz * 4];
    let s = sz as f32 / 24.0;
    let dist_seg = |px: f32, py: f32, ax: f32, ay: f32, bx: f32, by: f32| -> f32 {
        let vx = bx - ax;
        let vy = by - ay;
        let wx = px - ax;
        let wy = py - ay;
        let len2 = vx * vx + vy * vy;
        let t = if len2 > 0.0 { (wx * vx + wy * vy) / len2 } else { 0.0 }.clamp(0.0, 1.0);
        let dx = px - (ax + t * vx);
        let dy = py - (ay + t * vy);
        (dx * dx + dy * dy).sqrt()
    };
    for y in 0..sz {
        for x in 0..sz {
            let ux = (x as f32 + 0.5) / s;
            let uy = (y as f32 + 0.5) / s;
            // Rounded box [2, 22] x [2, 22] radius 5
            let cx = ux.clamp(7.0, 17.0);
            let cy = uy.clamp(7.0, 17.0);
            let box_d = ((ux - cx).powi(2) + (uy - cy).powi(2)).sqrt() - 5.0;
            if box_d > 0.8 {
                continue;
            }
            let mut col: [u8; 4] = if box_d > -1.4 {
                [212, 173, 114, 255] // #D4AD72 border
            } else {
                [31, 27, 38, 255] // #1F1B26 fill
            };
            let line_d = dist_seg(ux, uy, 8.0, 8.5, 16.0, 8.5)
                .min(dist_seg(ux, uy, 8.0, 8.5, 12.0, 16.0))
                .min(dist_seg(ux, uy, 16.0, 8.5, 12.0, 16.0));
            if line_d <= 0.75 {
                col = [245, 240, 232, 255]; // #F5F0E8
            }
            for (nx, ny, r, c) in [
                (8.0f32, 8.5f32, 2.25f32, [212u8, 173, 114, 255]),
                (16.0, 8.5, 2.25, [124, 198, 254, 255]),
                (12.0, 16.0, 2.45, [154, 230, 180, 255]),
            ] {
                if ((ux - nx).powi(2) + (uy - ny).powi(2)).sqrt() <= r {
                    col = c;
                }
            }
            let idx = (y * sz + x) * 4;
            rgba[idx..idx + 4].copy_from_slice(&col);
        }
    }
    winit::window::Icon::from_rgba(rgba, sz as u32, sz as u32).ok()
}

impl ApplicationHandler<UserEvent> for ViewerApp {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        let attrs = Window::default_attributes()
            .with_title("textual-wgpu · Rust Viewer")
            .with_inner_size(LogicalSize::new(1100.0, 720.0))
            .with_window_icon(build_window_icon());
        let win = Arc::new(event_loop.create_window(attrs).expect("create window"));
        let phys = win.inner_size();
        self.size = (phys.width.max(1), phys.height.max(1));

        let surface = self.instance.create_surface(Arc::clone(&win)).expect("wgpu surface");
        let adapter = pollster_block_on(self.instance.request_adapter(&wgpu::RequestAdapterOptions {
            compatible_surface: Some(&surface),
            ..Default::default()
        }))
        .expect("adapter");
        let (device, queue) = pollster_block_on(adapter.request_device(&Default::default(), None)).expect("device");
        let caps = surface.get_capabilities(&adapter);
        let fmt = caps.formats.iter().copied().find(|f| !f.is_srgb()).unwrap_or(caps.formats[0]);
        let config = wgpu::SurfaceConfiguration {
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            format: fmt,
            width: self.size.0,
            height: self.size.1,
            present_mode: wgpu::PresentMode::AutoVsync,
            alpha_mode: caps.alpha_modes[0],
            view_formats: vec![],
            desired_maximum_frame_latency: 2,
        };
        surface.configure(&device, &config);
        let comp = GpuCompositor::new(&device, fmt);

        self.window = Some(win);
        self.surface = Some(surface);
        self.device = Some(device);
        self.queue = Some(queue);
        self.config = Some(config);
        self.compositor = Some(comp);
        self.client.send_resize(self.size.0, self.size.1);
        let _ = self.proxy.send_event(UserEvent::Redraw);
    }

    fn user_event(&mut self, _event_loop: &ActiveEventLoop, event: UserEvent) {
        if matches!(event, UserEvent::Connected) {
            self.client.send_resize(self.size.0, self.size.1);
        }
        if matches!(event, UserEvent::Focus) {
            if let Some(win) = &self.window {
                win.set_visible(true);
                win.focus_window();
            }
        }
        if let Some(win) = &self.window {
            win.request_redraw();
        }
    }

    fn window_event(&mut self, event_loop: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        match event {
            WindowEvent::CloseRequested => event_loop.exit(),
            WindowEvent::ModifiersChanged(mods) => {
                self.modifiers = mods.state();
            }
            WindowEvent::Resized(ns) => {
                if ns.width > 0 && ns.height > 0 {
                    self.size = (ns.width, ns.height);
                    if let (Some(surf), Some(dev), Some(cfg)) = (&self.surface, &self.device, &mut self.config) {
                        cfg.width = ns.width;
                        cfg.height = ns.height;
                        surf.configure(dev, cfg);
                    }
                    self.client.send_resize(ns.width, ns.height);
                }
            }
            WindowEvent::CursorMoved { position, .. } => {
                self.cursor_pos = (position.x, position.y);
                self.client.send_pointer(POINTER_MOVE, position.x as f32, position.y as f32, 0);
            }
            WindowEvent::MouseInput { state, button, .. } => {
                let b = match button {
                    MouseButton::Left => 1,
                    MouseButton::Right => 2,
                    MouseButton::Middle => 3,
                    _ => 1,
                };
                let kind = if state == ElementState::Pressed { POINTER_DOWN } else { POINTER_UP };
                self.client.send_pointer(kind, self.cursor_pos.0 as f32, self.cursor_pos.1 as f32, b);
            }
            WindowEvent::MouseWheel { delta, .. } => {
                let (dx, dy) = match delta {
                    MouseScrollDelta::LineDelta(x, y) => (-x * 40.0, -y * 45.0),
                    MouseScrollDelta::PixelDelta(p) => (-p.x as f32, -p.y as f32),
                };
                self.client.send_wheel(self.cursor_pos.0 as f32, self.cursor_pos.1 as f32, dx, dy);
            }
            WindowEvent::KeyboardInput {
                event: KeyEvent { logical_key, state: ElementState::Pressed, text, .. },
                ..
            } => {
                let ctrl = self.modifiers.control_key();
                let shift = self.modifiers.shift_key();
                if let Key::Named(nk) = logical_key {
                    if let Some(k) = map_named_key(nk) {
                        let key_str = if ctrl && k == "enter" {
                            "ctrl+enter".to_string()
                        } else if shift && matches!(k, "left" | "right" | "up" | "down" | "home" | "end" | "tab") {
                            format!("shift+{k}")
                        } else {
                            k.to_string()
                        };
                        self.client.send_key(&key_str);
                        if k == "space" && !ctrl {
                            self.client.send_char(" ");
                        }
                    }
                } else if let Key::Character(ch) = &logical_key {
                    let low = ch.to_lowercase();
                    if ctrl {
                        self.client.send_key(&format!("ctrl+{low}"));
                    } else {
                        self.client.send_key(&low);
                        if let Some(t) = text.as_deref() {
                            self.client.send_char(t);
                        } else {
                            self.client.send_char(ch.as_str());
                        }
                    }
                }
            }
            WindowEvent::RedrawRequested => {
                if let (Some(surf), Some(dev), Some(q), Some(comp), Some(win)) =
                    (&self.surface, &self.device, &self.queue, &mut self.compositor, &self.window)
                {
                    if let Ok(frame) = surf.get_current_texture() {
                        let t0 = std::time::Instant::now();
                        let view = frame.texture.create_view(&Default::default());
                        let st = self.client.state.lock().unwrap();
                        win.set_title(&st.title);
                        comp.render_state(dev, q, &view, &st, self.size);
                        drop(st);
                        frame.present();
                        let ms = t0.elapsed().as_secs_f64() * 1000.0;
                        if ms >= 16.0 {
                            eprintln!("[tw-perf] gpu render_state {:.1}ms ({}x{})", ms, self.size.0, self.size.1);
                        }
                    }
                }
            }
            _ => {}
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

fn find_v4l2_capture_device() -> String {
    if let Ok(rd) = std::fs::read_dir("/sys/class/video4linux") {
        let mut names: Vec<String> = rd
            .flatten()
            .filter_map(|e| e.file_name().into_string().ok())
            .filter(|n| n.starts_with("video"))
            .collect();
        names.sort();
        for name in names {
            let idx_path = format!("/sys/class/video4linux/{name}/index");
            let idx = std::fs::read_to_string(&idx_path).unwrap_or_else(|_| "0".into());
            if idx.trim() == "0" {
                let dev = format!("/dev/{name}");
                if std::path::Path::new(&dev).exists() {
                    return dev;
                }
            }
        }
    }
    "/dev/video0".to_string()
}

fn map_ctl_mode_to_key(raw: &str) -> Option<&'static str> {
    if raw.contains("\"overview\"") { Some("f1") }
    else if raw.contains("\"controls\"") { Some("f2") }
    else if raw.contains("\"data\"") { Some("f3") }
    else if raw.contains("\"editor\"") { Some("f4") }
    else if raw.contains("\"themes\"") { Some("f5") }
    else if raw.contains("\"planes\"") { Some("f6") }
    else if raw.contains("\"media\"") { Some("f7") }
    else if raw.contains("\"nodes\"") { Some("f8") }
    else if raw.contains("\"composer\"") { Some("f9") }
    else { None }
}

fn find_hub_python() -> String {
    if let Ok(venv) = std::env::var("VIRTUAL_ENV") {
        let p = std::path::PathBuf::from(venv).join("bin/python");
        if p.exists() {
            return p.to_string_lossy().into_owned();
        }
    }
    let home = std::env::var("HOME").unwrap_or_default();
    let cached = std::path::PathBuf::from(&home).join(".cache/textual-wgpu/python_bin");
    if let Ok(p) = std::fs::read_to_string(cached) {
        let p = p.trim();
        if !p.is_empty() && std::path::Path::new(p).exists() {
            return p.to_string();
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        for anc in exe.ancestors().take(6) {
            for rel in ["venv/bin/python", ".venv/bin/python"] {
                let cand = anc.join(rel);
                if cand.exists() {
                    return cand.to_string_lossy().into_owned();
                }
            }
        }
    }
    "python3".to_string()
}

fn connect_media_hub(sock_path: &std::path::Path, hub_args: &[&str]) -> Option<UnixStream> {
    if let Ok(s) = UnixStream::connect(sock_path) {
        return Some(s);
    }
    let py = find_hub_python();
    let mut cmd = Command::new(&py);
    cmd.args(["-m", "textual_wgpu.remote.media_hub"])
        .args(hub_args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if cmd.spawn().is_err() {
        return None;
    }
    for _ in 0..50 {
        std::thread::sleep(std::time::Duration::from_millis(50));
        if let Ok(s) = UnixStream::connect(sock_path) {
            return Some(s);
        }
    }
    None
}

fn stop_audio(slot: &Arc<Mutex<Option<Child>>>) {
    if let Ok(mut guard) = slot.lock() {
        if let Some(mut ch) = guard.take() {
            let _ = ch.kill();
            let _ = ch.wait();
        }
    }
}

fn play_audio(slot: &Arc<Mutex<Option<Child>>>, sample_rate: u32, channels: u8, pcm: &[u8]) {
    stop_audio(slot);
    let ch = channels.max(1) as u16;
    let sr = sample_rate.max(8000);
    let byte_rate = sr * ch as u32 * 2;
    let block_align = ch * 2;
    let data_len = pcm.len() as u32;
    let riff_len = 36u32.saturating_add(data_len);

    let mut wav = Vec::with_capacity(44 + pcm.len());
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&riff_len.to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16u32.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes());
    wav.extend_from_slice(&ch.to_le_bytes());
    wav.extend_from_slice(&sr.to_le_bytes());
    wav.extend_from_slice(&byte_rate.to_le_bytes());
    wav.extend_from_slice(&block_align.to_le_bytes());
    wav.extend_from_slice(&16u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_len.to_le_bytes());
    wav.extend_from_slice(pcm);

    let path = std::env::temp_dir().join("textual-wgpu-viewer-audio.wav");
    if std::fs::write(&path, &wav).is_err() {
        return;
    }
    for player in ["pw-play", "paplay", "aplay", "afplay"] {
        if let Ok(child) = Command::new(player)
            .arg(&path)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        {
            if let Ok(mut guard) = slot.lock() {
                *guard = Some(child);
            }
            break;
        }
    }
}

struct FfmpegDecoder {
    codec_flag: u8,
    stdin: Option<std::process::ChildStdin>,
    proc: Child,
}

impl FfmpegDecoder {
    fn spawn(
        surf_id: u32,
        codec_flag: u8,
        state: Arc<Mutex<SceneState>>,
        proxy: EventLoopProxy<UserEvent>,
    ) -> Option<Self> {
        let fmt = if codec_flag == VID_CODEC_HEVC {
            "hevc"
        } else if codec_flag == VID_CODEC_AV1 {
            "ivf"
        } else {
            "h264"
        };
        let mut proc = Command::new("ffmpeg")
            .args([
                "-y", "-loglevel", "error",
                "-flags", "low_delay", "-flush_packets", "1",
                "-probesize", "32", "-analyzeduration", "0", "-threads", "1",
                "-f", fmt, "-i", "pipe:0",
                "-f", "yuv4mpegpipe", "-pix_fmt", "yuv420p", "pipe:1",
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .ok()?;
        let stdin = proc.stdin.take();
        let mut stdout = proc.stdout.take()?;
        std::thread::spawn(move || {
            let mut buf: Vec<u8> = Vec::with_capacity(512 * 1024);
            let mut tmp = [0u8; 65536];
            let mut w: u32 = 0;
            let mut h: u32 = 0;
            let mut got_stream_hdr = false;
            loop {
                match stdout.read(&mut tmp) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => buf.extend_from_slice(&tmp[..n]),
                }
                if !got_stream_hdr {
                    let Some(nl) = buf.iter().position(|&b| b == b'\n') else { continue };
                    let hdr = String::from_utf8_lossy(&buf[..nl]);
                    for tok in hdr.split_whitespace() {
                        if let Some(ws) = tok.strip_prefix('W') {
                            w = ws.parse().unwrap_or(0);
                        } else if let Some(hs) = tok.strip_prefix('H') {
                            h = hs.parse().unwrap_or(0);
                        }
                    }
                    buf.drain(..nl + 1);
                    got_stream_hdr = true;
                }
                loop {
                    let Some(nl) = buf.iter().position(|&b| b == b'\n') else { break };
                    let line = &buf[..nl];
                    if !line.starts_with(b"FRAME") {
                        buf.drain(..nl + 1);
                        continue;
                    }
                    if w < 2 || h < 2 {
                        buf.drain(..nl + 1);
                        continue;
                    }
                    let y_len = (w as usize) * (h as usize);
                    let uv_len = ((w as usize) / 2) * ((h as usize) / 2);
                    let total = nl + 1 + y_len + uv_len * 2;
                    if buf.len() < total {
                        break;
                    }
                    let y_off = nl + 1;
                    let u_off = y_off + y_len;
                    let v_off = u_off + uv_len;
                    if let Ok(mut st) = state.lock() {
                        st.push_decoded_yuv420(
                            surf_id,
                            w,
                            h,
                            &buf[y_off..u_off],
                            &buf[u_off..v_off],
                            &buf[v_off..v_off + uv_len],
                            w as usize,
                            (w as usize) / 2,
                            (w as usize) / 2,
                            1,
                        );
                    }
                    buf.drain(..total);
                    let _ = proxy.send_event(UserEvent::Redraw);
                }
            }
        });
        Some(Self { codec_flag, stdin, proc })
    }

    fn feed(&mut self, data: &[u8]) {
        if let Some(st) = &mut self.stdin {
            let _ = st.write_all(data);
            let _ = st.flush();
        }
    }
}

impl Drop for FfmpegDecoder {
    fn drop(&mut self) {
        self.stdin.take();
        let _ = self.proc.kill();
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(|s| s.as_str()) == Some("--nodes") {
        for n in load_saved_nodes() {
            println!("{:<14} [{:<11}] {:<24} {}:{}", n.id, n.role, n.name, n.host, n.port);
        }
        return;
    }
    let event_loop = EventLoop::<UserEvent>::with_user_event().build().expect("event loop");
    let proxy = event_loop.create_proxy();
    let proxy_cb = proxy.clone();
    let audio_slot: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));
    let audio_play_slot = Arc::clone(&audio_slot);
    let audio_stop_slot = Arc::clone(&audio_slot);

    let decoders: Arc<Mutex<HashMap<u32, FfmpegDecoder>>> = Arc::new(Mutex::new(HashMap::new()));
    let state_slot: Arc<Mutex<Option<Arc<Mutex<SceneState>>>>> = Arc::new(Mutex::new(None));
    let client_slot: Arc<Mutex<Option<ClientHandle>>> = Arc::new(Mutex::new(None));

    let dec_map = Arc::clone(&decoders);
    let st_for_vid = Arc::clone(&state_slot);
    let proxy_vid = proxy.clone();
    let client_for_file = Arc::clone(&client_slot);
    let client_for_fsl = Arc::clone(&client_slot);
    let client_for_fsg = Arc::clone(&client_slot);
    let cam_stop_slot: Arc<Mutex<Option<Arc<AtomicBool>>>> = Arc::new(Mutex::new(None));
    let cam_ctl_stop = Arc::clone(&cam_stop_slot);
    let client_for_cam = Arc::clone(&client_slot);
    let mic_stop_slot: Arc<Mutex<Option<Arc<AtomicBool>>>> = Arc::new(Mutex::new(None));
    let mic_ctl_stop = Arc::clone(&mic_stop_slot);
    let client_for_mic = Arc::clone(&client_slot);

    if std::net::TcpStream::connect(("127.0.0.1", 7423)).is_err() {
        let py = find_hub_python();
        let _ = Command::new(&py)
            .args(["-m", "textual_wgpu.remote.media_hub", "--serve-resources"])
            .process_group(0)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
    }
    if std::env::var_os("DISPLAY").is_some() || std::env::var_os("WAYLAND_DISPLAY").is_some() {
        let home = std::env::var("HOME").unwrap_or_default();
        let pid_path = std::path::PathBuf::from(home).join(".cache/textual-wgpu/tray.pid");
        let alive = std::fs::read_to_string(&pid_path)
            .ok()
            .and_then(|s| s.trim().parse::<u32>().ok())
            .map(|pid| std::path::PathBuf::from(format!("/proc/{pid}")).exists())
            .unwrap_or(false);
        if !alive {
            let py = find_hub_python();
            let _ = Command::new(&py)
                .args(["-m", "textual_wgpu.remote.cli", "tray"])
                .process_group(0)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn();
        }
    }

    let dec_map_status = Arc::clone(&decoders);
    let proxy_status = proxy.clone();
    let cbs = ClientCallbacks {
        on_commit: Arc::new(move || {
            let _ = proxy_cb.send_event(UserEvent::Redraw);
        }),
        on_status: Arc::new(move |status| {
            eprintln!("[tw-viewer] {status}");
            if status.starts_with("Connected (") {
                let _ = proxy_status.send_event(UserEvent::Connected);
            } else if status.starts_with("Disconnected") {
                if let Ok(mut map) = dec_map_status.lock() {
                    map.clear();
                }
            }
        }),
        on_clipboard: Arc::new(|_| {}),
        on_audio: Arc::new(move |sr, ch, pcm| play_audio(&audio_play_slot, sr, ch, pcm)),
        on_audio_stop: Arc::new(move || stop_audio(&audio_stop_slot)),
        on_video_nalu: Arc::new(move |surf_id, cflag, _pts, _kf, data| {
            let Some(st_arc) = st_for_vid.lock().ok().and_then(|g| g.clone()) else {
                return;
            };
            if let Ok(mut map) = dec_map.lock() {
                let needs_new = map.get(&surf_id).map(|d| d.codec_flag != cflag).unwrap_or(true);
                if needs_new {
                    if let Some(d) = FfmpegDecoder::spawn(surf_id, cflag, st_arc, proxy_vid.clone()) {
                        map.insert(surf_id, d);
                    }
                }
                if let Some(d) = map.get_mut(&surf_id) {
                    d.feed(data);
                }
            }
        }),
        on_open_client_file: Arc::new(move |_accept| {
            let ch_opt = client_for_file.lock().ok().and_then(|g| g.clone());
            std::thread::spawn(move || {
                if let Ok(out) = Command::new("zenity")
                    .args(["--file-selection", "--title=Pick media file for remote app"])
                    .output()
                {
                    if out.status.success() {
                        let p = String::from_utf8_lossy(&out.stdout).trim().to_string();
                        if !p.is_empty() {
                            if let Ok(bytes) = std::fs::read(&p) {
                                let name = std::path::Path::new(&p)
                                    .file_name()
                                    .map(|s| s.to_string_lossy().into_owned())
                                    .unwrap_or_else(|| "media.bin".into());
                                if let Some(ch) = ch_opt {
                                    ch.send_client_file(&name, &bytes);
                                }
                            }
                        }
                    }
                }
            });
        }),
        on_fs_list: Arc::new(move |req_path| {
            let t0 = std::time::Instant::now();
            let Some(ch) = client_for_fsl.lock().ok().and_then(|g| g.clone()) else {
                return;
            };
            let mut entries = Vec::new();
            let home = std::env::var("HOME").unwrap_or_else(|_| "/".into());
            if req_path.is_empty() {
                for label in ["Downloads", "Pictures", "Videos", "Music", "Home"] {
                    let p = if label == "Home" {
                        std::path::PathBuf::from(&home)
                    } else {
                        std::path::PathBuf::from(&home).join(label)
                    };
                    if p.is_dir() {
                        entries.push(FsEntry {
                            is_dir: true,
                            name: label.to_string(),
                            path: p.to_string_lossy().into_owned(),
                            size: 0,
                        });
                    }
                }
            } else if let Ok(rd) = std::fs::read_dir(req_path) {
                for item in rd.flatten() {
                    let name = item.file_name().to_string_lossy().into_owned();
                    if name.starts_with('.') {
                        continue;
                    }
                    let Ok(meta) = item.metadata() else { continue };
                    entries.push(FsEntry {
                        is_dir: meta.is_dir(),
                        name,
                        path: item.path().to_string_lossy().into_owned(),
                        size: if meta.is_dir() { 0 } else { meta.len() },
                    });
                }
                entries.sort_by(|a, b| b.is_dir.cmp(&a.is_dir).then(a.name.to_lowercase().cmp(&b.name.to_lowercase())));
            }
            eprintln!(
                "[tw-fs] FS_LIST {:?} -> {} items ({:.1}ms)",
                req_path,
                entries.len(),
                t0.elapsed().as_secs_f64() * 1000.0
            );
            ch.send_fs_entries(req_path, &entries);
        }),
        on_fs_get: Arc::new(move |req_path| {
            let Some(ch) = client_for_fsg.lock().ok().and_then(|g| g.clone()) else {
                return;
            };
            let req = req_path.to_string();
            std::thread::spawn(move || {
                if req == "__picker__" {
                    if let Ok(out) = Command::new("zenity")
                        .args(["--file-selection", "--title=Pick media file"])
                        .output()
                    {
                        if out.status.success() {
                            let p = String::from_utf8_lossy(&out.stdout).trim().to_string();
                            if !p.is_empty() {
                                if let Ok(bytes) = std::fs::read(&p) {
                                    let name = std::path::Path::new(&p)
                                        .file_name()
                                        .map(|s| s.to_string_lossy().into_owned())
                                        .unwrap_or_else(|| "media.bin".into());
                                    ch.send_client_file(&name, &bytes);
                                }
                            }
                        }
                    }
                    return;
                }
                if let Ok(bytes) = std::fs::read(&req) {
                    let name = std::path::Path::new(&req)
                        .file_name()
                        .map(|s| s.to_string_lossy().into_owned())
                        .unwrap_or_else(|| "media.bin".into());
                    eprintln!("[tw-fs] FS_GET sending {} ({} KB)", name, bytes.len() / 1024);
                    ch.send_client_file(&name, &bytes);
                }
            });
        }),
        on_cam_ctl: Arc::new(move |mode, cam_id| {
            if let Ok(mut g) = cam_ctl_stop.lock() {
                if let Some(prev) = g.take() {
                    prev.store(true, Ordering::Relaxed);
                }
            }
            if mode == 0 {
                eprintln!("[tw-cam] stopped");
                return;
            }
            let Some(ch) = client_for_cam.lock().ok().and_then(|g| g.clone()) else { return };
            let cam_dev = if cam_id.starts_with('/') && std::path::Path::new(cam_id).exists() {
                cam_id.to_string()
            } else {
                find_v4l2_capture_device()
            };
            let flag = Arc::new(AtomicBool::new(false));
            *cam_ctl_stop.lock().unwrap() = Some(Arc::clone(&flag));
            eprintln!("[tw-cam] subscribing to shared hub for {cam_dev} (mode={mode})");
            std::thread::spawn(move || {
                let home = std::env::var("HOME").unwrap_or_default();
                let dev_name = std::path::Path::new(&cam_dev)
                    .file_name()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_else(|| "video0".into());
                let sock_path = std::path::PathBuf::from(home).join(format!(".cache/textual-wgpu/cam_{dev_name}.sock"));
                let Some(mut stream) = connect_media_hub(&sock_path, &["--video", &cam_dev]) else {
                    eprintln!("[tw-cam] failed to connect media_hub for {cam_dev}");
                    return;
                };
                let _ = stream.set_read_timeout(Some(std::time::Duration::from_millis(200)));
                let mut hdr = [0u8; 13];
                let mut payload = Vec::new();
                while !flag.load(Ordering::Relaxed) {
                    match stream.read_exact(&mut hdr) {
                        Ok(()) => {}
                        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::TimedOut => continue,
                        Err(_) => break,
                    }
                    let pts_us = u64::from_le_bytes(hdr[0..8].try_into().unwrap());
                    let flags = hdr[8];
                    let plen = u32::from_le_bytes(hdr[9..13].try_into().unwrap()) as usize;
                    if plen > 16 * 1024 * 1024 {
                        break;
                    }
                    payload.resize(plen, 0);
                    let mut got = 0usize;
                    let mut broken = false;
                    while got < plen && !flag.load(Ordering::Relaxed) {
                        match stream.read(&mut payload[got..]) {
                            Ok(0) => { broken = true; break; }
                            Ok(n) => got += n,
                            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::TimedOut => continue,
                            Err(_) => { broken = true; break; }
                        }
                    }
                    if broken || flag.load(Ordering::Relaxed) {
                        break;
                    }
                    ch.send_video_nalu(pts_us, flags, &payload);
                }
            });
        }),
        on_mic_ctl: Arc::new(move |mode| {
            if let Ok(mut g) = mic_ctl_stop.lock() {
                if let Some(prev) = g.take() {
                    prev.store(true, Ordering::Relaxed);
                }
            }
            if mode == 0 {
                eprintln!("[tw-mic] stopped");
                return;
            }
            let Some(ch) = client_for_mic.lock().ok().and_then(|g| g.clone()) else { return };
            let flag = Arc::new(AtomicBool::new(false));
            *mic_ctl_stop.lock().unwrap() = Some(Arc::clone(&flag));
            eprintln!("[tw-mic] subscribing to shared mic hub");
            std::thread::spawn(move || {
                let home = std::env::var("HOME").unwrap_or_default();
                let sock_path = std::path::PathBuf::from(home).join(".cache/textual-wgpu/mic_default.sock");
                let Some(mut stream) = connect_media_hub(&sock_path, &["--mic"]) else {
                    eprintln!("[tw-mic] failed to connect mic media_hub");
                    return;
                };
                let _ = stream.set_read_timeout(Some(std::time::Duration::from_millis(200)));
                let mut hdr = [0u8; 13];
                let mut pcm = Vec::new();
                while !flag.load(Ordering::Relaxed) {
                    match stream.read_exact(&mut hdr) {
                        Ok(()) => {}
                        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::TimedOut => continue,
                        Err(_) => break,
                    }
                    let plen = u32::from_le_bytes(hdr[9..13].try_into().unwrap()) as usize;
                    if plen > 1024 * 1024 {
                        break;
                    }
                    pcm.resize(plen, 0);
                    let mut got = 0usize;
                    let mut broken = false;
                    while got < plen && !flag.load(Ordering::Relaxed) {
                        match stream.read(&mut pcm[got..]) {
                            Ok(0) => { broken = true; break; }
                            Ok(n) => got += n,
                            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::TimedOut => continue,
                            Err(_) => { broken = true; break; }
                        }
                    }
                    if broken || flag.load(Ordering::Relaxed) {
                        break;
                    }
                    ch.send_audio_pcm(16000, 1, &pcm);
                }
            });
        }),
        ..Default::default()
    };

    let (client, _join) = if args.first().map(|s| s.as_str()) == Some("--node") && args.len() >= 2 {
        let nid = &args[1];
        let nodes = load_saved_nodes();
        let node = nodes
            .into_iter()
            .find(|n| n.id == *nid || n.name.eq_ignore_ascii_case(nid))
            .unwrap_or_else(|| panic!("Saved node not found: {nid}"));
        if node.transport == "tcp" {
            spawn_tcp_session_cbs(node.host, node.port, cbs).expect("spawn node tcp")
        } else if node.transport == "local" {
            spawn_command_session_cbs(
                find_hub_python(),
                vec!["-m".into(), "textual_wgpu".into()],
                vec![("TEXTUAL_WGPU_REMOTE".into(), "stdio".into())],
                cbs,
            )
            .expect("spawn node local")
        } else {
            let target = if node.user.is_empty() {
                node.host.clone()
            } else {
                format!("{}@{}", node.user, node.host)
            };
            spawn_command_session_cbs(
                "ssh".into(),
                vec![
                    "-T".into(),
                    "-o".into(),
                    "Compression=no".into(),
                    "-p".into(),
                    node.port.to_string(),
                    target,
                    node.remote_cmd,
                ],
                vec![],
                cbs,
            )
            .expect("spawn node ssh")
        }
    } else if args.first().map(|s| s.as_str()) == Some("--tcp") {
        let target = args.get(1).map(|s| s.as_str()).unwrap_or("127.0.0.1:7420");
        let (host, port) = if let Some((h, p)) = target.rsplit_once(':') {
            (h.to_string(), p.parse::<u16>().unwrap_or(7420))
        } else {
            let p = args.get(2).and_then(|s| s.parse::<u16>().ok()).unwrap_or(7420);
            (target.to_string(), p)
        };
        spawn_tcp_session_cbs(host, port, cbs).expect("spawn tcp session")
    } else if args.is_empty() {
        // No args → just start the top-bar tray + resource service, don't open a window.
        // The tray's menu opens the actual app/viewer when the user clicks.
        eprintln!("[tw-viewer] Starting top-bar tray + resource service only.");
        eprintln!("[tw-viewer] Use the tray menu to open apps, or run with --tcp / --ssh / --node to connect.");
        return;
    } else {
        let (program, cmd_args, envs) = if args[0] == "--ssh" && args.len() >= 2 {
            let host = args[1].clone();
            let rest: Vec<String> = args[2..].iter().filter(|s| s.as_str() != "--").cloned().collect();
            let mut ssh_args = vec!["-T".into(), "-o".into(), "Compression=no".into(), host];
            if rest.is_empty() {
                ssh_args.extend(["TEXTUAL_WGPU_REMOTE=stdio".into(), "python3".into(), "-m".into(), "textual_wgpu".into()]);
            } else {
                if !rest.iter().any(|a| a.starts_with("TEXTUAL_WGPU_REMOTE=")) {
                    ssh_args.push("TEXTUAL_WGPU_REMOTE=stdio".into());
                }
                ssh_args.extend(rest);
            }
            ("ssh".to_string(), ssh_args, vec![])
        } else {
            (
                args[0].clone(),
                args[1..].to_vec(),
                vec![("TEXTUAL_WGPU_REMOTE".into(), "stdio".into())],
            )
        };

        spawn_command_session_cbs(program, cmd_args, envs, cbs).expect("spawn session")
    };

    *state_slot.lock().unwrap() = Some(Arc::clone(&client.state));
    *client_slot.lock().unwrap() = Some(client.clone());

    let ctl_home = std::env::var("HOME").unwrap_or_default();
    let ctl_sock_path = std::path::PathBuf::from(ctl_home).join(".cache/textual-wgpu/viewer_ctl.sock");
    let _ = std::fs::remove_file(&ctl_sock_path);
    let ctl_stop = Arc::new(AtomicBool::new(false));
    let ctl_stop_bg = Arc::clone(&ctl_stop);
    let ctl_client = client.clone();
    let ctl_proxy = proxy.clone();
    let ctl_sock_bg = ctl_sock_path.clone();
    std::thread::spawn(move || {
        let Ok(listener) = UnixListener::bind(&ctl_sock_bg) else { return };
        let _ = listener.set_nonblocking(true);
        while !ctl_stop_bg.load(Ordering::Relaxed) {
            match listener.accept() {
                Ok((mut conn, _)) => {
                    let mut line = String::new();
                    let _ = std::io::BufReader::new(&conn).read_line(&mut line);
                    let trimmed = line.trim();
                    let mut ok = true;
                    if trimmed.contains("\"focus\"") {
                        let _ = ctl_proxy.send_event(UserEvent::Focus);
                    } else if let Some(key) = map_ctl_mode_to_key(trimmed) {
                        ctl_client.send_key(key);
                        let _ = ctl_proxy.send_event(UserEvent::Focus);
                    } else {
                        ok = false;
                    }
                    let _ = conn.write_all(if ok { b"{\"ok\":true}\n" } else { b"{\"ok\":false}\n" });
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(std::time::Duration::from_millis(80));
                }
                Err(_) => break,
            }
        }
    });

    let mut app = ViewerApp::new(client, proxy);
    event_loop.run_app(&mut app).expect("run app");
    ctl_stop.store(true, Ordering::Relaxed);
    let _ = std::fs::remove_file(&ctl_sock_path);
    stop_audio(&audio_slot);
    if let Ok(mut g) = cam_stop_slot.lock() {
        if let Some(f) = g.take() { f.store(true, Ordering::Relaxed); }
    };
    if let Ok(mut g) = mic_stop_slot.lock() {
        if let Some(f) = g.take() { f.store(true, Ordering::Relaxed); }
    };
}
