use std::process::Stdio;
use std::sync::{Arc, Mutex};

use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::process::Command;
use tokio::sync::mpsc;
use tw_proto::{
    decode_audio_pcm, decode_vid_nalu, encode_caps, encode_client_file, encode_input_pointer,
    encode_input_wheel, encode_resize, unpack_header, Frame, FrameReassembler, Op, Priority,
    PriorityMux, HEADER_SIZE, VID_CODEC_MASK, VID_FLAG_KEYFRAME,
};
use tw_render::SceneState;

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
        }
    }
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
        _connected_port: u32,
        _originator_address: &str,
        _originator_port: u32,
        _session: &mut russh::client::Session,
    ) -> Result<(), Self::Error> {
        if let Some(port) = self.reverse_local_port {
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

async fn pump_frame_stream<R, W>(
    mut reader: R,
    mut writer: W,
    mut rx: mpsc::UnboundedReceiver<Frame>,
    tx_caps: mpsc::UnboundedSender<Frame>,
    state: Arc<Mutex<SceneState>>,
    cbs: ClientCallbacks,
) where
    R: AsyncRead + Unpin + Send + 'static,
    W: AsyncWrite + Unpin + Send + 'static,
{
    let writer_task = tokio::spawn(async move {
        let mut mux = PriorityMux::new(4096, 2);
        while let Some(f) = rx.recv().await {
            mux.push(f, true, false);
            while let Ok(more) = rx.try_recv() {
                mux.push(more, true, false);
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
    });

    let mut reasm = FrameReassembler::default();
    let mut hdr = [0u8; HEADER_SIZE];
    loop {
        if reader.read_exact(&mut hdr).await.is_err() {
            break;
        }
        let (_vf, _p, _op, _sid, _seq, plen) = unpack_header(&hdr);
        let mut body = vec![0u8; plen as usize];
        if !body.is_empty() && reader.read_exact(&mut body).await.is_err() {
            break;
        }
        if let Ok(Some(frame)) = reasm.feed(&hdr, &body) {
            dispatch_incoming_frame(&frame, &tx_caps, &state, &cbs);
        }
    }
    writer_task.abort();
}

fn dispatch_incoming_frame(
    frame: &Frame,
    tx_caps: &mpsc::UnboundedSender<Frame>,
    state: &Arc<Mutex<SceneState>>,
    cbs: &ClientCallbacks,
) {
    if frame.op == Op::Hello {
        let _ = tx_caps.send(Frame::new(
            Op::Caps,
            encode_caps(true, 1024, "hevc,h264,av1"),
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
        Op::VidNalu => {
            if let Ok((pts, flags, _mat, raw)) = decode_vid_nalu(&frame.payload) {
                let cflag = flags & VID_CODEC_MASK;
                if cflag != 0 {
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
    let (tx, rx) = mpsc::unbounded_channel::<Frame>();
    let tx_caps = tx.clone();

    let handle = std::thread::spawn(move || {
        let rt = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("tokio runtime");
        rt.block_on(async move {
            (cbs.on_status)(&format!("Connecting TCP {host}:{port}…"));
            let stream = match TcpStream::connect((host.as_str(), port)).await {
                Ok(s) => s,
                Err(e) => {
                    (cbs.on_status)(&format!("TCP connect failed: {e}"));
                    return;
                }
            };
            (cbs.on_status)(&format!("Connected ({host}:{port})"));
            let (reader, writer) = stream.into_split();
            pump_frame_stream(reader, writer, rx, tx_caps, state_bg, cbs.clone()).await;
            (cbs.on_status)("Disconnected");
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
                reverse_local_port: cfg.reverse_forward_port,
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

            if let Some(rport) = cfg.reverse_forward_port {
                match session.tcpip_forward("127.0.0.1", rport as u32).await {
                    Ok(_) => on_status(&format!("Tunnel 127.0.0.1:{rport} → phone ready")),
                    Err(e) => on_status(&format!("Reverse tunnel err: {e}")),
                }
            }

            let mut channel = match session.channel_open_session().await {
                Ok(c) => c,
                Err(e) => {
                    on_status(&format!("Channel error: {e}"));
                    return;
                }
            };
            if let Err(e) = channel.exec(true, cfg.remote_cmd.as_bytes()).await {
                on_status(&format!("Exec failed: {e}"));
                return;
            }
            on_status("Connected");

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
                        mux.push(f, true, false);
                        while let Ok(more) = rx.try_recv() {
                            mux.push(more, true, false);
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
    let (tx, rx) = mpsc::unbounded_channel::<Frame>();
    let tx_caps = tx.clone();

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
            pump_frame_stream(
                stdout,
                stdin,
                rx,
                tx_caps,
                state_bg,
                cbs,
            )
            .await;
            let _ = child.kill().await;
        });
    });

    Ok((ClientHandle { tx, state, on_clipboard: None }, handle))
}
