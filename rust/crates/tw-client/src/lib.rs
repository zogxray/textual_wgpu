use std::process::Stdio;
use std::sync::{Arc, Mutex, OnceLock, RwLock};

use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::process::Command;
use tokio::sync::mpsc;
use tw_proto::{
    decode_app_list, decode_audio_pcm, decode_launch_status, decode_vid_nalu, encode_audio_pcm,
    encode_caps_with_device, encode_client_file, encode_fs_entries, encode_input_pointer,
    encode_input_wheel, encode_launch_app, encode_resize, encode_vid_nalu, unpack_header, AppEntry,
    Frame, FrameReassembler, FsEntry, Op, Priority, PriorityMux, HEADER_SIZE, VID_CODEC_MASK,
    VID_FLAG_KEYFRAME, VID_ROT_MASK, VID_ROT_SHIFT,
};
use tw_render::SceneState;

#[derive(Debug, Clone)]
pub struct LocalDeviceInfo {
    pub device_id: String,
    pub device_name: String,
    pub device_kind: String,
    pub cameras: String,
}

static LOCAL_DEVICE_INFO: OnceLock<RwLock<LocalDeviceInfo>> = OnceLock::new();

fn default_local_device_info() -> LocalDeviceInfo {
    let device_id = ["/etc/machine-id", "/var/lib/dbus/machine-id"]
        .iter()
        .find_map(|p| std::fs::read_to_string(p).ok().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()))
        .unwrap_or_else(|| {
            std::fs::read_to_string("/etc/hostname")
                .map(|s| s.trim().to_string())
                .unwrap_or_else(|_| "desktop".into())
        });
    let device_name = std::fs::read_to_string("/etc/hostname")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("HOSTNAME").ok().filter(|s| !s.is_empty()))
        .unwrap_or_else(|| "Workstation".into());
    let mut cams = Vec::new();
    if let Ok(rd) = std::fs::read_dir("/sys/class/video4linux") {
        let mut nodes: Vec<String> = rd
            .flatten()
            .filter_map(|e| e.file_name().into_string().ok())
            .filter(|n| n.starts_with("video"))
            .collect();
        nodes.sort();
        for node in nodes {
            let idx = std::fs::read_to_string(format!("/sys/class/video4linux/{node}/index"))
                .unwrap_or_else(|_| "0".into());
            if idx.trim() != "0" {
                continue;
            }
            let dev = format!("/dev/{node}");
            if !std::path::Path::new(&dev).exists() {
                continue;
            }
            let cname = std::fs::read_to_string(format!("/sys/class/video4linux/{node}/name"))
                .map(|s| s.trim().to_string())
                .unwrap_or_else(|_| node.clone());
            cams.push(format!("{dev}={cname}"));
        }
    }
    LocalDeviceInfo {
        device_id,
        device_name,
        device_kind: "desktop".into(),
        cameras: cams.join(";"),
    }
}

pub fn set_local_device_info(info: LocalDeviceInfo) {
    let lock = LOCAL_DEVICE_INFO.get_or_init(|| RwLock::new(default_local_device_info()));
    if let Ok(mut g) = lock.write() {
        *g = info;
    }
}

pub fn get_local_device_info() -> LocalDeviceInfo {
    let lock = LOCAL_DEVICE_INFO.get_or_init(|| RwLock::new(default_local_device_info()));
    lock.read().map(|g| g.clone()).unwrap_or_else(|_| default_local_device_info())
}

#[derive(Clone)]
pub struct ClientHandle {
    tx: mpsc::UnboundedSender<Frame>,
    pub state: Arc<Mutex<SceneState>>,
    pub on_clipboard: Option<Arc<dyn Fn(&str) + Send + Sync>>,
}

impl ClientHandle {
    pub fn send_key(&self, key: &str) {
        let _ = self.tx.send(Frame::new(Op::InputKey, key.as_bytes().to_vec(), Priority::P0Control));
    }

    pub fn send_char(&self, ch: &str) {
        if !ch.is_empty() {
            let _ = self.tx.send(Frame::new(Op::InputChar, ch.as_bytes().to_vec(), Priority::P0Control));
        }
    }

    pub fn send_pointer(&self, kind: u8, x: f32, y: f32, button: u8) {
        let _ = self.tx.send(Frame::new(
            Op::InputPointer,
            encode_input_pointer(kind, x, y, button),
            Priority::P0Control,
        ));
    }

    pub fn send_wheel(&self, x: f32, y: f32, dx: f32, dy: f32) {
        let _ = self.tx.send(Frame::new(
            Op::InputWheel,
            encode_input_wheel(x, y, dx, dy),
            Priority::P0Control,
        ));
    }

    pub fn send_resize(&self, w: u32, h: u32) {
        let _ = self.tx.send(Frame::new(Op::Resize, encode_resize(w, h), Priority::P0Control));
    }

    pub fn send_paste(&self, text: &str) {
        let _ = self.tx.send(Frame::new(Op::ClipPaste, text.as_bytes().to_vec(), Priority::P0Control));
    }

    pub fn send_client_file(&self, name: &str, data: &[u8]) {
        let _ = self.tx.send(Frame::new(
            Op::ClientFileData,
            encode_client_file(name, data),
            Priority::P3Bulk,
        ));
    }

    pub fn send_fs_entries(&self, req_path: &str, entries: &[FsEntry]) {
        let _ = self.tx.send(Frame::new(
            Op::FsEntries,
            encode_fs_entries(req_path, entries),
            Priority::P0Control,
        ));
    }

    pub fn send_video_nalu(&self, pts_us: u64, flags: u8, nalu: &[u8]) {
        let _ = self.tx.send(Frame::new(
            Op::VidNalu,
            encode_vid_nalu(pts_us, flags, nalu),
            Priority::P2Media,
        ));
    }

    pub fn send_audio_pcm(&self, sample_rate: u32, channels: u8, pcm: &[u8]) {
        let _ = self.tx.send(Frame::new(
            Op::AudioPcm,
            encode_audio_pcm(sample_rate, channels, 1, pcm),
            Priority::P1Ui,
        ));
    }

    pub fn list_apps(&self) {
        let _ = self.tx.send(Frame::new(Op::ListApps, Vec::new(), Priority::P0Control));
    }

    pub fn launch_app(&self, app_id: &str, args: &str) {
        let _ = self.tx.send(Frame::new(
            Op::LaunchApp,
            encode_launch_app(app_id, args),
            Priority::P0Control,
        ));
    }

    pub fn quit(&self) {
        self.send_key("q");
    }
}

#[derive(Clone)]
pub struct ClientCallbacks {
    pub on_commit: Arc<dyn Fn() + Send + Sync>,
    pub on_status: Arc<dyn Fn(&str) + Send + Sync>,
    pub on_clipboard: Arc<dyn Fn(&str) + Send + Sync>,
    pub on_audio: Arc<dyn Fn(u32, u8, &[u8]) + Send + Sync>,
    pub on_audio_stop: Arc<dyn Fn() + Send + Sync>,
    pub on_video_nalu: Arc<dyn Fn(u32, u8, u64, bool, &[u8]) + Send + Sync>,
    pub on_open_client_file: Arc<dyn Fn(&str) + Send + Sync>,
    pub on_app_list: Arc<dyn Fn(&[AppEntry]) + Send + Sync>,
    pub on_launch_status: Arc<dyn Fn(bool, &str, &str) + Send + Sync>,
    pub on_fs_list: Arc<dyn Fn(&str) + Send + Sync>,
    pub on_fs_get: Arc<dyn Fn(&str) + Send + Sync>,
    pub on_cam_ctl: Arc<dyn Fn(u8, &str) + Send + Sync>,
    pub on_mic_ctl: Arc<dyn Fn(u8) + Send + Sync>,
}

impl Default for ClientCallbacks {
    fn default() -> Self {
        Self {
            on_commit: Arc::new(|| {}),
            on_status: Arc::new(|_| {}),
            on_clipboard: Arc::new(|_| {}),
            on_audio: Arc::new(|_, _, _| {}),
            on_audio_stop: Arc::new(|| {}),
            on_video_nalu: Arc::new(|_, _, _, _, _| {}),
            on_open_client_file: Arc::new(|_| {}),
            on_app_list: Arc::new(|_| {}),
            on_launch_status: Arc::new(|_, _, _| {}),
            on_fs_list: Arc::new(|_| {}),
            on_fs_get: Arc::new(|_| {}),
            on_cam_ctl: Arc::new(|_, _| {}),
            on_mic_ctl: Arc::new(|_| {}),
        }
    }
}

#[derive(Debug, Clone)]
pub struct SavedNode {
    pub id: String,
    pub name: String,
    pub role: String,
    pub transport: String,
    pub host: String,
    pub port: u16,
    pub user: String,
    pub remote_cmd: String,
}

fn extract_json_str(obj: &str, key: &str) -> Option<String> {
    let pat = format!("\"{key}\"");
    let kpos = obj.find(&pat)?;
    let rest = &obj[kpos + pat.len()..];
    let cpos = rest.find(':')?;
    let val = rest[cpos + 1..].trim_start();
    if let Some(after_q) = val.strip_prefix('"') {
        let end = after_q.find('"')?;
        Some(after_q[..end].to_string())
    } else {
        let end = val.find([',', '}']).unwrap_or(val.len());
        Some(val[..end].trim().to_string())
    }
}

pub fn load_saved_nodes() -> Vec<SavedNode> {
    let mut out = Vec::new();
    let home = std::env::var("HOME").unwrap_or_default();
    let path = std::path::PathBuf::from(home).join(".config/textual-wgpu/nodes.json");
    let Ok(txt) = std::fs::read_to_string(path) else {
        return vec![
            SavedNode {
                id: "local".into(),
                name: "This Device".into(),
                role: "workstation".into(),
                transport: "local".into(),
                host: "127.0.0.1".into(),
                port: 7420,
                user: "local".into(),
                remote_cmd: "python3 -m textual_wgpu".into(),
            },
            SavedNode {
                id: "workstation".into(),
                name: "Workstation GPU".into(),
                role: "workstation".into(),
                transport: "ssh".into(),
                host: "192.168.1.10".into(),
                port: 22,
                user: "user".into(),
                remote_cmd: "TEXTUAL_WGPU_REMOTE=stdio python3 -m textual_wgpu".into(),
            },
            SavedNode {
                id: "phone".into(),
                name: "Phone (Reverse / LAN)".into(),
                role: "phone".into(),
                transport: "tcp".into(),
                host: "127.0.0.1".into(),
                port: 7420,
                user: "android".into(),
                remote_cmd: "python3 -m textual_wgpu".into(),
            },
            SavedNode {
                id: "cloud".into(),
                name: "Cloud Compute Node".into(),
                role: "cloud".into(),
                transport: "ssh".into(),
                host: "cloud.example.net".into(),
                port: 22,
                user: "ubuntu".into(),
                remote_cmd: "TEXTUAL_WGPU_REMOTE=stdio python3 -m textual_wgpu".into(),
            },
        ];
    };
    for chunk in txt.split('{').skip(1) {
        let Some(end) = chunk.find('}') else { continue };
        let obj = &chunk[..end];
        let Some(id) = extract_json_str(obj, "id") else { continue };
        if id.is_empty() {
            continue;
        }
        out.push(SavedNode {
            id: id.clone(),
            name: extract_json_str(obj, "name").unwrap_or(id),
            role: extract_json_str(obj, "role").unwrap_or_else(|| "workstation".into()),
            transport: extract_json_str(obj, "transport").unwrap_or_else(|| "ssh".into()),
            host: extract_json_str(obj, "host").unwrap_or_else(|| "127.0.0.1".into()),
            port: extract_json_str(obj, "port").and_then(|s| s.parse().ok()).unwrap_or(22),
            user: extract_json_str(obj, "user").unwrap_or_else(|| "user".into()),
            remote_cmd: extract_json_str(obj, "remote_cmd")
                .unwrap_or_else(|| "TEXTUAL_WGPU_REMOTE=stdio python3 -m textual_wgpu".into()),
        });
    }
    out
}

#[derive(Debug, Clone)]
pub struct SshConfig {
    pub host: String,
    pub port: u16,
    pub user: String,
    pub password: Option<String>,
    pub key_pem: Option<String>,
    pub key_passphrase: Option<String>,
    pub remote_cmd: String,
    pub reverse_forward_port: Option<u16>,
    pub reverse_local_port: Option<u16>,
    pub direct_tcpip_port: Option<u16>,
}

pub fn default_ssh_launch_cmd() -> String {
    concat!(
        "sh -c '",
        "if [ -f \"$HOME/.cache/textual-wgpu/python_bin\" ]; then ",
        "p=$(cat \"$HOME/.cache/textual-wgpu/python_bin\"); ",
        "[ -x \"$p\" ] && \"$p\" -c \"import textual_wgpu\" 2>/dev/null && ",
        "exec env TEXTUAL_WGPU_REMOTE=stdio \"$p\" -m textual_wgpu; fi; ",
        "for p in python3 \"$HOME/.local/bin/python3\" \"$HOME/.venv/bin/python\" \"$HOME/venv/bin/python\" ",
        "\"$HOME/.local/share/pipx/venvs/textual-wgpu/bin/python\" ",
        "\"$HOME\"/*/venv/bin/python \"$HOME\"/*/.venv/bin/python ",
        "\"$HOME\"/*/*/venv/bin/python \"$HOME\"/*/*/.venv/bin/python ",
        "\"$HOME\"/*/*/*/venv/bin/python \"$HOME\"/*/*/*/.venv/bin/python; do ",
        "[ -x \"$p\" ] || command -v \"$p\" >/dev/null 2>&1 || continue; ",
        "\"$p\" -c \"import textual_wgpu\" 2>/dev/null && ",
        "exec env TEXTUAL_WGPU_REMOTE=stdio \"$p\" -m textual_wgpu; ",
        "done; echo \"textual_wgpu not found\" >&2; exit 1'"
    )
    .to_string()
}

struct SshClientHandler {
    reverse_local_port: Option<u16>,
}

#[async_trait::async_trait]
impl russh::client::Handler for SshClientHandler {
    type Error = russh::Error;

    async fn check_server_key(
        &mut self,
        _server_public_key: &russh::keys::ssh_key::PublicKey,
    ) -> Result<bool, Self::Error> {
        Ok(true)
    }

    async fn server_channel_open_forwarded_tcpip(
        &mut self,
        channel: russh::Channel<russh::client::Msg>,
        _connected_address: &str,
        connected_port: u32,
        _originator_address: &str,
        _originator_port: u32,
        _session: &mut russh::client::Session,
    ) -> Result<(), Self::Error> {
        let target_port = if (7424..=7425).contains(&connected_port) {
            Some(7423u16)
        } else {
            self.reverse_local_port
        };
        if let Some(port) = target_port {
            tokio::spawn(async move {
                if let Ok(mut local) = TcpStream::connect(("127.0.0.1", port)).await {
                    let mut stream = channel.into_stream();
                    let _ = tokio::io::copy_bidirectional(&mut stream, &mut local).await;
                }
            });
        }
        Ok(())
    }
}

async fn pump_frame_stream_mut<R, W>(
    mut reader: R,
    mut writer: W,
    rx: &mut mpsc::UnboundedReceiver<Frame>,
    tx_caps: &mpsc::WeakUnboundedSender<Frame>,
    state: &Arc<Mutex<SceneState>>,
    cbs: &ClientCallbacks,
) where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let mut mux = PriorityMux::new(4096, 2);
    let mut reasm = FrameReassembler::default();
    let mut rx_buf: Vec<u8> = Vec::with_capacity(65536);
    let mut tmp = [0u8; 32768];
    loop {
        tokio::select! {
            maybe_out = rx.recv() => {
                let Some(f) = maybe_out else { break };
                let comp = !matches!(f.op, Op::ClientFileData | Op::VidNalu | Op::PutBlob);
                mux.push(f, comp, false);
                while let Ok(more) = rx.try_recv() {
                    let mc = !matches!(more.op, Op::ClientFileData | Op::VidNalu | Op::PutBlob);
                    mux.push(more, mc, false);
                }
                while let Some(pkt) = mux.pop_packet() {
                    if writer.write_all(&pkt).await.is_err() {
                        return;
                    }
                }
                if writer.flush().await.is_err() {
                    return;
                }
            }
            rd = reader.read(&mut tmp) => {
                let Ok(n) = rd else { break };
                if n == 0 {
                    break;
                }
                rx_buf.extend_from_slice(&tmp[..n]);
                while rx_buf.len() >= HEADER_SIZE {
                    let hdr: [u8; HEADER_SIZE] = rx_buf[..HEADER_SIZE].try_into().unwrap();
                    let (_vf, _p, _op, _sid, _seq, plen) = unpack_header(&hdr);
                    let total = HEADER_SIZE + plen as usize;
                    if rx_buf.len() < total {
                        break;
                    }
                    let body = rx_buf[HEADER_SIZE..total].to_vec();
                    rx_buf.drain(..total);
                    if let Ok(Some(frame)) = reasm.feed(&hdr, &body) {
                        if let Some(tx) = tx_caps.upgrade() {
                            dispatch_incoming_frame(&frame, &tx, state, cbs);
                        }
                    }
                }
            }
        }
    }
}

fn dispatch_incoming_frame(
    frame: &Frame,
    tx_caps: &mpsc::UnboundedSender<Frame>,
    state: &Arc<Mutex<SceneState>>,
    cbs: &ClientCallbacks,
) {
    if frame.op == Op::Hello {
        let info = get_local_device_info();
        let _ = tx_caps.send(Frame::new(
            Op::Caps,
            encode_caps_with_device(
                true,
                1024,
                "hevc,h264,av1",
                &info.device_id,
                &info.device_name,
                &info.device_kind,
                &info.cameras,
            ),
            Priority::P0Control,
        ));
    }
    match frame.op {
        Op::ClipSet => {
            let txt = String::from_utf8_lossy(&frame.payload).into_owned();
            (cbs.on_clipboard)(&txt);
        }
        Op::AudioPcm => {
            if let Ok((sr, ch, _fl, pcm)) = decode_audio_pcm(&frame.payload) {
                (cbs.on_audio)(sr, ch, pcm);
            }
        }
        Op::AudioStop => {
            (cbs.on_audio_stop)();
        }
        Op::OpenClientFile => {
            let accept = String::from_utf8_lossy(&frame.payload).into_owned();
            (cbs.on_open_client_file)(&accept);
        }
        Op::FsList => {
            let path = String::from_utf8_lossy(&frame.payload).into_owned();
            (cbs.on_fs_list)(&path);
        }
        Op::FsGet => {
            let path = String::from_utf8_lossy(&frame.payload).into_owned();
            (cbs.on_fs_get)(&path);
        }
        Op::CamCtl => {
            let mode = frame.payload.first().copied().unwrap_or(0);
            let cam_id = if frame.payload.len() > 1 {
                String::from_utf8_lossy(&frame.payload[1..]).into_owned()
            } else {
                String::new()
            };
            (cbs.on_cam_ctl)(mode, &cam_id);
        }
        Op::MicCtl => {
            let mode = frame.payload.first().copied().unwrap_or(0);
            (cbs.on_mic_ctl)(mode);
        }
        Op::AppList => {
            if let Ok(apps) = decode_app_list(&frame.payload) {
                (cbs.on_app_list)(&apps);
            }
        }
        Op::LaunchStatus => {
            if let Ok((ok, app_id, msg)) = decode_launch_status(&frame.payload) {
                (cbs.on_launch_status)(ok, &app_id, &msg);
            }
        }
        Op::VidNalu => {
            if let Ok((pts, flags, _mat, raw)) = decode_vid_nalu(&frame.payload) {
                let cflag = flags & VID_CODEC_MASK;
                if cflag != 0 {
                    let rot_idx = (flags & VID_ROT_MASK) >> VID_ROT_SHIFT;
                    if let Ok(mut st) = state.lock() {
                        st.set_plane_rotation(frame.surf_id, rot_idx);
                    }
                    (cbs.on_video_nalu)(
                        frame.surf_id,
                        cflag,
                        pts,
                        (flags & VID_FLAG_KEYFRAME) != 0,
                        raw,
                    );
                }
            }
        }
        _ => {}
    }
    let commit = {
        let mut st = state.lock().unwrap();
        st.apply_frame(frame)
    };
    if commit {
        (cbs.on_commit)();
    }
}

pub fn spawn_tcp_session_cbs(
    host: String,
    port: u16,
    cbs: ClientCallbacks,
) -> std::io::Result<(ClientHandle, std::thread::JoinHandle<()>)> {
    let state = Arc::new(Mutex::new(SceneState::default()));
    let state_bg = Arc::clone(&state);
    let (tx, mut rx) = mpsc::unbounded_channel::<Frame>();
    let tx_weak = tx.downgrade();

    let handle = std::thread::spawn(move || {
        let rt = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("tokio runtime");
        rt.block_on(async move {
            let mut ever_connected = false;
            let mut announced_wait = false;
            loop {
                if tx_weak.strong_count() == 0 {
                    break;
                }
                if !announced_wait {
                    announced_wait = true;
                    (cbs.on_status)(&format!("Waiting for TCP {host}:{port}…"));
                    if let Ok(mut st) = state_bg.lock() {
                        st.title = format!("textual-wgpu · Waiting for {host}:{port}…");
                    }
                    (cbs.on_commit)();
                }
                let stream = match TcpStream::connect((host.as_str(), port)).await {
                    Ok(s) => s,
                    Err(_) => {
                        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                        continue;
                    }
                };
                while rx.try_recv().is_ok() {}
                if ever_connected {
                    if let Ok(mut st) = state_bg.lock() {
                        st.layers.clear();
                        st.order.clear();
                        st.planes.clear();
                        st.buffers.clear();
                        st.styles.clear();
                    }
                }
                announced_wait = false;
                (cbs.on_status)(&format!("Connected ({host}:{port})"));
                let (reader, writer) = stream.into_split();
                let t0 = std::time::Instant::now();
                pump_frame_stream_mut(reader, writer, &mut rx, &tx_weak, &state_bg, &cbs).await;
                if t0.elapsed() > std::time::Duration::from_millis(250) {
                    ever_connected = true;
                }
                (cbs.on_audio_stop)();
                if tx_weak.strong_count() == 0 {
                    (cbs.on_status)("Disconnected");
                    break;
                }
                (cbs.on_status)(&format!("Disconnected · reconnecting to {host}:{port}…"));
                if let Ok(mut st) = state_bg.lock() {
                    st.title = format!("textual-wgpu · Reconnecting {host}:{port}…");
                }
                (cbs.on_commit)();
                tokio::time::sleep(std::time::Duration::from_millis(500)).await;
            }
        });
    });

    Ok((ClientHandle { tx, state, on_clipboard: None }, handle))
}

pub fn spawn_tcp_session<F, S, C, A, Q>(
    host: String,
    port: u16,
    on_commit: F,
    on_status: S,
    on_clipboard: C,
    on_audio: A,
    on_audio_stop: Q,
) -> std::io::Result<(ClientHandle, std::thread::JoinHandle<()>)>
where
    F: Fn() + Send + Sync + 'static,
    S: Fn(&str) + Send + Sync + 'static,
    C: Fn(&str) + Send + Sync + 'static,
    A: Fn(u32, u8, &[u8]) + Send + Sync + 'static,
    Q: Fn() + Send + Sync + 'static,
{
    spawn_tcp_session_cbs(
        host,
        port,
        ClientCallbacks {
            on_commit: Arc::new(on_commit),
            on_status: Arc::new(on_status),
            on_clipboard: Arc::new(on_clipboard),
            on_audio: Arc::new(on_audio),
            on_audio_stop: Arc::new(on_audio_stop),
            ..Default::default()
        },
    )
}

pub fn spawn_ssh_session<F, S>(
    cfg: SshConfig,
    on_commit: F,
    on_status: S,
) -> std::io::Result<(ClientHandle, std::thread::JoinHandle<()>)>
where
    F: Fn() + Send + Sync + 'static,
    S: Fn(&str) + Send + Sync + 'static,
{
    spawn_ssh_session_with_clipboard(cfg, on_commit, on_status, |_| {})
}

pub fn spawn_ssh_session_with_clipboard<F, S, C>(
    cfg: SshConfig,
    on_commit: F,
    on_status: S,
    on_clipboard: C,
) -> std::io::Result<(ClientHandle, std::thread::JoinHandle<()>)>
where
    F: Fn() + Send + Sync + 'static,
    S: Fn(&str) + Send + Sync + 'static,
    C: Fn(&str) + Send + Sync + 'static,
{
    spawn_ssh_session_full(cfg, on_commit, on_status, on_clipboard, |_, _, _| {}, || {})
}

pub fn spawn_ssh_session_full<F, S, C, A, Q>(
    cfg: SshConfig,
    on_commit: F,
    on_status: S,
    on_clipboard: C,
    on_audio: A,
    on_audio_stop: Q,
) -> std::io::Result<(ClientHandle, std::thread::JoinHandle<()>)>
where
    F: Fn() + Send + Sync + 'static,
    S: Fn(&str) + Send + Sync + 'static,
    C: Fn(&str) + Send + Sync + 'static,
    A: Fn(u32, u8, &[u8]) + Send + Sync + 'static,
    Q: Fn() + Send + Sync + 'static,
{
    spawn_ssh_session_cbs(
        cfg,
        ClientCallbacks {
            on_commit: Arc::new(on_commit),
            on_status: Arc::new(on_status),
            on_clipboard: Arc::new(on_clipboard),
            on_audio: Arc::new(on_audio),
            on_audio_stop: Arc::new(on_audio_stop),
            ..Default::default()
        },
    )
}

pub fn spawn_ssh_session_cbs(
    cfg: SshConfig,
    cbs: ClientCallbacks,
) -> std::io::Result<(ClientHandle, std::thread::JoinHandle<()>)> {
    let state = Arc::new(Mutex::new(SceneState::default()));
    let state_bg = Arc::clone(&state);
    let (tx, mut rx) = mpsc::unbounded_channel::<Frame>();
    let tx_caps = tx.clone();
    let on_status = Arc::clone(&cbs.on_status);

    let handle = std::thread::spawn(move || {
        let rt = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("tokio runtime");
        rt.block_on(async move {
            on_status(&format!("Connecting to {}:{}…", cfg.host, cfg.port));
            let r_cfg = Arc::new(russh::client::Config::default());
            let handler = SshClientHandler {
                reverse_local_port: cfg.reverse_local_port.or(cfg.reverse_forward_port),
            };
            let mut session = match russh::client::connect(r_cfg, (cfg.host.as_str(), cfg.port), handler).await {
                Ok(s) => s,
                Err(e) => {
                    on_status(&format!("SSH connect failed: {e}"));
                    return;
                }
            };

            let mut authed = false;
            if let Some(pem) = cfg.key_pem.as_deref().filter(|s| !s.trim().is_empty()) {
                match russh_keys::decode_secret_key(pem, cfg.key_passphrase.as_deref()) {
                    Ok(kp) => {
                        if let Ok(key_with_alg) = russh_keys::key::PrivateKeyWithHashAlg::new(Arc::new(kp), None) {
                            if let Ok(ok) = session.authenticate_publickey(&cfg.user, key_with_alg).await {
                                authed = ok;
                            }
                        }
                    }
                    Err(e) => {
                        on_status(&format!("Invalid SSH key: {e}"));
                    }
                }
            }
            if !authed {
                if let Some(pw) = cfg.password.as_deref() {
                    match session.authenticate_password(&cfg.user, pw).await {
                        Ok(ok) => authed = ok,
                        Err(e) => {
                            on_status(&format!("Auth error: {e}"));
                            return;
                        }
                    }
                }
            }
            if !authed {
                on_status("SSH authentication rejected");
                return;
            }

            // Try direct-tcpip FIRST before setting up reverse tcpip_forward so we never loop back to ourselves.
            let mut channel = if let Some(dport) = cfg.direct_tcpip_port.filter(|&p| p > 0) {
                match session
                    .channel_open_direct_tcpip("127.0.0.1", dport as u32, "127.0.0.1", 0)
                    .await
                {
                    Ok(c) => {
                        on_status(&format!("Attached to running server (127.0.0.1:{dport})"));
                        c
                    }
                    Err(_) => {
                        on_status("Starting remote server…");
                        let c = match session.channel_open_session().await {
                            Ok(c) => c,
                            Err(e) => {
                                on_status(&format!("Channel error: {e}"));
                                return;
                            }
                        };
                        let cmd = if cfg.remote_cmd.trim().is_empty() {
                            default_ssh_launch_cmd()
                        } else {
                            cfg.remote_cmd.clone()
                        };
                        if let Err(e) = c.exec(true, cmd.as_bytes()).await {
                            on_status(&format!("Exec failed: {e}"));
                            return;
                        }
                        on_status("Connected");
                        c
                    }
                }
            } else {
                let c = match session.channel_open_session().await {
                    Ok(c) => c,
                    Err(e) => {
                        on_status(&format!("Channel error: {e}"));
                        return;
                    }
                };
                let cmd = if cfg.remote_cmd.trim().is_empty() {
                    default_ssh_launch_cmd()
                } else {
                    cfg.remote_cmd.clone()
                };
                if let Err(e) = c.exec(true, cmd.as_bytes()).await {
                    on_status(&format!("Exec failed: {e}"));
                    return;
                }
                on_status("Connected");
                c
            };

            if let Some(rport) = cfg.reverse_forward_port {
                match session.tcpip_forward("127.0.0.1", rport as u32).await {
                    Ok(_) => on_status(&format!("Phone demo shared at 127.0.0.1:{rport}")),
                    Err(e) => on_status(&format!("Reverse tunnel err: {e}")),
                }
            }

            // Expose local device Resource Service (127.0.0.1:7423) to remote peer at 7424..7425
            for rport in 7424..=7425u32 {
                if session.tcpip_forward("127.0.0.1", rport).await.is_ok() {
                    break;
                }
            }
            let session = Arc::new(session);
            // Expose remote peer's Resource Service (127.0.0.1:7423) locally at 7426..7427 (non-overlapping!)
            let mut res_listener = None;
            for lport in 7426..=7427u16 {
                if let Ok(l) = tokio::net::TcpListener::bind(("127.0.0.1", lport)).await {
                    res_listener = Some(l);
                    break;
                }
            }
            let res_bridge_task = res_listener.map(|listener| {
                let sess = Arc::clone(&session);
                tokio::spawn(async move {
                    while let Ok((mut local_sock, _)) = listener.accept().await {
                        let s = Arc::clone(&sess);
                        tokio::spawn(async move {
                            if let Ok(ch) = s
                                .channel_open_direct_tcpip("127.0.0.1", 7423, "127.0.0.1", 0)
                                .await
                            {
                                let mut remote_stream = ch.into_stream();
                                let _ = tokio::io::copy_bidirectional(&mut local_sock, &mut remote_stream).await;
                            }
                        });
                    }
                })
            });

            let mut mux = PriorityMux::new(4096, 2);
            let mut reasm = FrameReassembler::default();
            let mut rx_buf: Vec<u8> = Vec::with_capacity(65536);
            let mut got_hello = false;
            let mut last_err = String::new();
            let mut exit_code: Option<u32> = None;

            loop {
                tokio::select! {
                    maybe_out = rx.recv() => {
                        let Some(f) = maybe_out else { break };
                        let comp = !matches!(f.op, Op::ClientFileData | Op::VidNalu | Op::PutBlob);
                        mux.push(f, comp, false);
                        while let Ok(more) = rx.try_recv() {
                            let mc = !matches!(more.op, Op::ClientFileData | Op::VidNalu | Op::PutBlob);
                            mux.push(more, mc, false);
                        }
                        while let Some(pkt) = mux.pop_packet() {
                            if channel.data(&pkt[..]).await.is_err() {
                                return;
                            }
                        }
                    }
                    msg = channel.wait() => {
                        let Some(msg) = msg else { break };
                        match msg {
                            russh::ChannelMsg::Data { data } => {
                                rx_buf.extend_from_slice(&data);
                                while rx_buf.len() >= HEADER_SIZE {
                                    let hdr: [u8; HEADER_SIZE] = rx_buf[..HEADER_SIZE].try_into().unwrap();
                                    let (_vf, _p, _op, _sid, _seq, plen) = unpack_header(&hdr);
                                    let total = HEADER_SIZE + plen as usize;
                                    if rx_buf.len() < total {
                                        break;
                                    }
                                    let body = rx_buf[HEADER_SIZE..total].to_vec();
                                    rx_buf.drain(..total);
                                    if let Ok(Some(frame)) = reasm.feed(&hdr, &body) {
                                        if frame.op == Op::Hello {
                                            got_hello = true;
                                        }
                                        dispatch_incoming_frame(&frame, &tx_caps, &state_bg, &cbs);
                                    }
                                }
                            }
                            russh::ChannelMsg::ExtendedData { data, .. } => {
                                let text = String::from_utf8_lossy(&data);
                                let trimmed = text.trim();
                                if !trimmed.is_empty() {
                                    last_err = trimmed.lines().last().unwrap_or(trimmed).to_string();
                                    on_status(&last_err);
                                }
                            }
                            russh::ChannelMsg::ExitStatus { exit_status } => {
                                exit_code = Some(exit_status);
                            }
                            russh::ChannelMsg::Eof | russh::ChannelMsg::Close => break,
                            _ => {}
                        }
                    }
                }
            }
            if let Some(t) = res_bridge_task {
                t.abort();
            }
            drop(session);
            if !got_hello && !last_err.is_empty() {
                on_status(&format!("Exited ({}): {}", exit_code.unwrap_or(1), last_err));
            } else if let Some(code) = exit_code.filter(|c| *c != 0) {
                on_status(&format!("Disconnected (exit {code})"));
            } else {
                on_status("Disconnected");
            }
        });
    });

    Ok((ClientHandle { tx, state, on_clipboard: None }, handle))
}

pub fn spawn_command_session<F>(
    program: String,
    args: Vec<String>,
    envs: Vec<(String, String)>,
    on_commit: F,
) -> std::io::Result<(ClientHandle, std::thread::JoinHandle<()>)>
where
    F: Fn() + Send + Sync + 'static,
{
    spawn_command_session_full(program, args, envs, on_commit, |_| {}, |_, _, _| {}, || {})
}

pub fn spawn_command_session_full<F, C, A, Q>(
    program: String,
    args: Vec<String>,
    envs: Vec<(String, String)>,
    on_commit: F,
    on_clipboard: C,
    on_audio: A,
    on_audio_stop: Q,
) -> std::io::Result<(ClientHandle, std::thread::JoinHandle<()>)>
where
    F: Fn() + Send + Sync + 'static,
    C: Fn(&str) + Send + Sync + 'static,
    A: Fn(u32, u8, &[u8]) + Send + Sync + 'static,
    Q: Fn() + Send + Sync + 'static,
{
    spawn_command_session_cbs(
        program,
        args,
        envs,
        ClientCallbacks {
            on_commit: Arc::new(on_commit),
            on_clipboard: Arc::new(on_clipboard),
            on_audio: Arc::new(on_audio),
            on_audio_stop: Arc::new(on_audio_stop),
            ..Default::default()
        },
    )
}

pub fn spawn_command_session_cbs(
    program: String,
    args: Vec<String>,
    envs: Vec<(String, String)>,
    cbs: ClientCallbacks,
) -> std::io::Result<(ClientHandle, std::thread::JoinHandle<()>)> {
    let state = Arc::new(Mutex::new(SceneState::default()));
    let state_bg = Arc::clone(&state);
    let (tx, mut rx) = mpsc::unbounded_channel::<Frame>();
    let tx_weak = tx.downgrade();

    let handle = std::thread::spawn(move || {
        let rt = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("tokio runtime");
        rt.block_on(async move {
            let mut cmd = Command::new(&program);
            cmd.args(&args)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit());
            for (k, v) in envs {
                cmd.env(k, v);
            }
            let mut child = match cmd.spawn() {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("tw-client spawn error: {e}");
                    return;
                }
            };
            let stdout = child.stdout.take().unwrap();
            let stdin = child.stdin.take().unwrap();
            pump_frame_stream_mut(
                stdout,
                stdin,
                &mut rx,
                &tx_weak,
                &state_bg,
                &cbs,
            )
            .await;
            let _ = child.kill().await;
        });
    });

    Ok((ClientHandle { tx, state, on_clipboard: None }, handle))
}
