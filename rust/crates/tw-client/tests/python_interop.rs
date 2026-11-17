use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tw_client::{spawn_command_session_cbs, ClientCallbacks};
use tw_proto::{VID_CODEC_H264, VID_CODEC_HEVC};

fn find_python() -> String {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let venv_py = manifest_dir.join("../../../venv/bin/python");
    if venv_py.is_file() {
        return venv_py.to_string_lossy().into_owned();
    }
    let parent_venv = manifest_dir.join("../../../../venv/bin/python");
    if parent_venv.is_file() {
        return parent_venv.to_string_lossy().into_owned();
    }
    "python3".to_string()
}

#[test]
fn rust_client_drives_python_demo_over_stdio() {
    let py = find_python();
    let nalus: Arc<Mutex<Vec<(u8, usize)>>> = Arc::new(Mutex::new(Vec::new()));
    let nalus_cb = Arc::clone(&nalus);
    let cbs = ClientCallbacks {
        on_video_nalu: Arc::new(move |_sid, cflag, _pts, _kf, data| {
            nalus_cb.lock().unwrap().push((cflag, data.len()));
        }),
        ..Default::default()
    };
    let (client, _join) = spawn_command_session_cbs(
        py,
        vec!["-m".into(), "textual_wgpu".into()],
        vec![("TEXTUAL_WGPU_REMOTE".into(), "stdio".into())],
        cbs,
    )
    .expect("spawn python demo");

    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        {
            let st = client.state.lock().unwrap();
            if !st.order.is_empty() && st.title.contains("textual-wgpu") {
                break;
            }
        }
        assert!(Instant::now() < deadline, "timed out waiting for initial FRAME_COMMIT from Python");
        std::thread::sleep(Duration::from_millis(25));
    }

    // Switch to F6 (3D Scene) and verify BUF_WRITE client-side 3D plane is populated
    client.send_key("f6");
    let deadline3d = Instant::now() + Duration::from_secs(5);
    loop {
        {
            let st = client.state.lock().unwrap();
            if st.planes.values().any(|pl| !pl.rgba.is_empty() && pl.size.0 > 0) {
                break;
            }
        }
        assert!(Instant::now() < deadline3d, "timed out waiting for F6 3D plane");
        std::thread::sleep(Duration::from_millis(25));
    }

    // Switch to F7 (Media) and verify @image photo blob + Video plane (HEVC/H264 NALU or RGBA) arrive
    client.send_key("f7");
    let deadline_media = Instant::now() + Duration::from_secs(8);
    loop {
        {
            let st = client.state.lock().unwrap();
            let got_nalu = !nalus.lock().unwrap().is_empty();
            let got_video = got_nalu || st.planes.values().any(|pl| pl.kind == 1 && !pl.rgba.is_empty());
            if !st.blobs.is_empty() && got_video {
                let ui = st.render_ui(1100, 720);
                assert_eq!(ui.width, 1100);
                assert_eq!(ui.height, 720);
                break;
            }
        }
        assert!(Instant::now() < deadline_media, "timed out waiting for F7 Media photo + video");
        std::thread::sleep(Duration::from_millis(25));
    }
    {
        let got = nalus.lock().unwrap();
        if !got.is_empty() {
            assert!(got.iter().any(|(c, len)| (*c == VID_CODEC_HEVC || *c == VID_CODEC_H264) && *len > 0));
        }
    }

    // Trigger 'a' (Play Synth Audio) and verify AUDIO_PCM reaches the Rust client
    client.send_key("a");
    let deadline_audio = Instant::now() + Duration::from_secs(5);
    loop {
        {
            let st = client.state.lock().unwrap();
            if st.last_audio.as_ref().map(|(sr, ch, pcm)| (*sr, *ch, pcm.len())) == Some((22050, 1, 176400)) {
                break;
            }
        }
        assert!(Instant::now() < deadline_audio, "timed out waiting for F7 AUDIO_PCM in Rust client");
        std::thread::sleep(Duration::from_millis(25));
    }
}
