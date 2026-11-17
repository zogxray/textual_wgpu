"""Peer AI resource discovery, streaming multimodal inference, camera snapshot & TTS synthesis."""

from __future__ import annotations

import base64
import io
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image


@dataclass(slots=True)
class AiProviderInfo:
    resource_id: str
    label: str
    backend: str  # "ollama" | "openai" | "gemini" | "builtin"
    endpoint: str
    model: str
    scope: str = "local"  # "local" | "cloud"
    api_key: str = ""


def _http_get_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 0.25) -> dict[str, Any] | None:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _load_ai_config() -> list[dict[str, Any]]:
    cfg_path = Path.home() / ".config" / "textual-wgpu" / "ai.json"
    if not cfg_path.is_file():
        return []
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        items = raw.get("providers") if isinstance(raw, dict) else raw
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def discover_local_ai_providers(*, probe_network: bool = True) -> list[AiProviderInfo]:
    """Discover local GPU/CPU LLM endpoints and optional Cloud AI providers."""
    out: list[AiProviderInfo] = []
    if probe_network and not os.environ.get("PYTEST_CURRENT_TEST"):
        # 1) Local Ollama (127.0.0.1:11434)
        ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        if not ollama_host.startswith("http"):
            ollama_host = f"http://{ollama_host}"
        tags = _http_get_json(f"{ollama_host}/api/tags", timeout=0.2)
        if tags and isinstance(tags.get("models"), list):
            for m in tags["models"][:6]:
                if not isinstance(m, dict):
                    continue
                mname = str(m.get("name") or "").strip()
                if not mname:
                    continue
                out.append(
                    AiProviderInfo(
                        resource_id=f"ollama:{mname}",
                        label=f"Local AI · {mname} (Ollama)",
                        backend="ollama",
                        endpoint=ollama_host,
                        model=mname,
                        scope="local",
                    ),
                )

        # 2) Local llama-server / vLLM / OpenAI-compatible endpoint
        oai_base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
        oai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if oai_base:
            hdrs = {"Authorization": f"Bearer {oai_key}"} if oai_key else {}
            models_resp = _http_get_json(f"{oai_base}/models", headers=hdrs, timeout=0.25)
            is_local = ("127.0.0.1" in oai_base) or ("localhost" in oai_base)
            mname = os.environ.get("OPENAI_MODEL", "").strip()
            if models_resp and isinstance(models_resp.get("data"), list) and models_resp["data"]:
                mname = mname or str(models_resp["data"][0].get("id") or "default")
            if mname:
                out.append(
                    AiProviderInfo(
                        resource_id=f"openai:{mname}",
                        label=f"{'Local' if is_local else 'Cloud'} AI · {mname}",
                        backend="openai",
                        endpoint=oai_base,
                        model=mname,
                        scope="local" if is_local else "cloud",
                        api_key=oai_key,
                    ),
                )
        elif oai_key:
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
            out.append(
                AiProviderInfo(
                    resource_id=f"openai:{model}",
                    label=f"Cloud AI · {model}",
                    backend="openai",
                    endpoint="https://api.openai.com/v1",
                    model=model,
                    scope="cloud",
                    api_key=oai_key,
                ),
            )

        gem_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if gem_key:
            gmodel = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
            out.append(
                AiProviderInfo(
                    resource_id=f"gemini:{gmodel}",
                    label=f"Cloud AI · {gmodel}",
                    backend="gemini",
                    endpoint="https://generativelanguage.googleapis.com/v1beta",
                    model=gmodel,
                    scope="cloud",
                    api_key=gem_key,
                ),
            )

    for cfg in _load_ai_config():
        rid = str(cfg.get("id") or "").strip()
        backend = str(cfg.get("backend") or "openai").strip()
        endpoint = str(cfg.get("endpoint") or "").rstrip("/")
        model = str(cfg.get("model") or "").strip()
        if not rid or not model:
            continue
        out.append(
            AiProviderInfo(
                resource_id=rid,
                label=str(cfg.get("label") or f"AI · {model}"),
                backend=backend,
                endpoint=endpoint,
                model=model,
                scope=str(cfg.get("scope") or "cloud"),
                api_key=str(cfg.get("api_key") or ""),
            ),
        )

    # Always expose the zero-dependency offline Meta-OS analyzer so every node can reason offline
    out.append(
        AiProviderInfo(
            resource_id="builtin:analyzer",
            label="Local Meta-OS Analyzer (Offline)",
            backend="builtin",
            endpoint="local://builtin",
            model="analyzer-v1",
            scope="local",
        ),
    )
    return out


def _builtin_analyze(prompt: str, *, context: str = "", image_b64: str = "") -> str:
    parts: list[str] = []
    if image_b64:
        try:
            raw = base64.b64decode(image_b64)
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            w, h = im.size
            # Compute simple scene brightness & dominant color region summary
            small = im.resize((16, 16))
            px = list(small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata())
            avg_r = sum(p[0] for p in px) // len(px)
            avg_g = sum(p[1] for p in px) // len(px)
            avg_b = sum(p[2] for p in px) // len(px)
            luma = int(0.299 * avg_r + 0.587 * avg_g + 0.114 * avg_b)
            tone = "bright" if luma > 165 else ("dim/dark" if luma < 75 else "balanced")
            parts.append(f"Visual frame ({w}x{h}, {tone} lighting, avg RGB={avg_r},{avg_g},{avg_b}).")
        except Exception:  # noqa: BLE001
            parts.append("Visual frame attached.")
    if context.strip():
        lines = [ln.strip() for ln in context.strip().splitlines() if ln.strip()]
        preview = " | ".join(lines[:4])
        if len(preview) > 220:
            preview = preview[:217] + "..."
        parts.append(f"Context ({len(lines)} lines, {len(context)} chars): {preview}")
    p = prompt.strip() or "Summarize active inputs."
    if not parts:
        return f"[Local Analyzer] Received prompt: {p}"
    return f"[Local Analyzer] {p} -> " + " ".join(parts)


def run_ai_inference(
    provider: AiProviderInfo | None,
    prompt: str,
    *,
    context: str = "",
    image_b64: str = "",
    on_token: Callable[[str], None] | None = None,
    timeout: float = 45.0,
) -> str:
    """Execute streaming or single-shot inference against any discovered AI provider."""
    if provider is None or provider.backend == "builtin":
        ans = _builtin_analyze(prompt, context=context, image_b64=image_b64)
        if callable(on_token):
            on_token(ans)
        return ans

    full_prompt = prompt.strip()
    if context.strip():
        full_prompt = f"{full_prompt}\n\nContext:\n{context.strip()}"

    if provider.backend == "ollama":
        body: dict[str, Any] = {
            "model": provider.model,
            "prompt": full_prompt,
            "stream": True,
        }
        if image_b64:
            body["images"] = [image_b64]
        req = urllib.request.Request(
            f"{provider.endpoint}/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        chunks: list[str] = []
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                txt = raw_line.decode("utf-8", errors="replace").strip()
                if not txt:
                    continue
                obj = json.loads(txt)
                tok = str(obj.get("response") or "")
                if tok:
                    chunks.append(tok)
                    if callable(on_token):
                        on_token(tok)
                if obj.get("done"):
                    break
        return "".join(chunks).strip()

    if provider.backend == "openai":
        content: Any = full_prompt
        if image_b64:
            content = [
                {"type": "text", "text": full_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ]
        body = {
            "model": provider.model,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
        }
        hdrs = {"Content-Type": "application/json"}
        if provider.api_key:
            hdrs["Authorization"] = f"Bearer {provider.api_key}"
        req = urllib.request.Request(
            f"{provider.endpoint}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=hdrs,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8", errors="replace"))
        ans = str(((obj.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if ans and callable(on_token):
            on_token(ans)
        return ans

    if provider.backend == "gemini":
        parts: list[dict[str, Any]] = [{"text": full_prompt}]
        if image_b64:
            parts.append({"inline_data": {"mime_type": "image/png", "data": image_b64}})
        body = {"contents": [{"parts": parts}]}
        url = f"{provider.endpoint}/models/{provider.model}:generateContent?key={provider.api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8", errors="replace"))
        cand = (obj.get("candidates") or [{}])[0]
        cparts = ((cand.get("content") or {}).get("parts") or [])
        ans = "".join(str(p.get("text") or "") for p in cparts if isinstance(p, dict)).strip()
        if ans and callable(on_token):
            on_token(ans)
        return ans

    ans = _builtin_analyze(prompt, context=context, image_b64=image_b64)
    if callable(on_token):
        on_token(ans)
    return ans


def snapshot_camera_frame(
    host: str,
    port: int,
    resource_id: str,
    *,
    timeout: float = 3.5,
) -> tuple[int, int, bytes] | None:
    """Subscribe briefly to a camera resource, grab 1 frame, and encode as PNG bytes."""
    if int(port or 0) <= 0:
        from .media_codec import LocalCameraStreamer

        facing = 2 if resource_id == "back" else 1
        device = resource_id if resource_id.startswith("/") else None
        cam = LocalCameraStreamer(facing=facing, device=device, emit_rgba=True)
        try:
            deadline = time.monotonic() + max(0.3, float(timeout))
            while time.monotonic() < deadline:
                frame = cam.pop_rgba()
                if frame is not None:
                    w, h, rgba = frame
                    im = Image.frombytes("RGBA", (w, h), rgba)
                    buf = io.BytesIO()
                    im.save(buf, format="PNG")
                    return (w, h, buf.getvalue())
                time.sleep(0.03)
            return None
        finally:
            cam.close()

    from .media_hub import RemoteCameraSubscriber

    sub = RemoteCameraSubscriber(host, port, resource_id, emit_rgba=True)
    try:
        deadline = time.monotonic() + max(0.3, float(timeout))
        while time.monotonic() < deadline:
            frame = sub.pop_rgba()
            if frame is not None:
                w, h, rgba = frame
                im = Image.frombytes("RGBA", (w, h), rgba)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                return (w, h, buf.getvalue())
            time.sleep(0.03)
        return None
    finally:
        sub.close()


def synthesize_speech_pcm(text: str, *, sample_rate: int = 16000) -> bytes:
    """Synthesize mono 16kHz PCM16 speech via espeak-ng/espeak, or fallback confirmation tone."""
    clean = (text or "").strip()[:400]
    for bin_name in ("espeak-ng", "espeak"):
        exe = shutil.which(bin_name)
        if not exe or not clean:
            continue
        try:
            proc = subprocess.run(
                [exe, "--stdout", "-s", "165", clean],
                capture_output=True,
                timeout=8.0,
            )
            if proc.returncode == 0 and len(proc.stdout) > 44 and proc.stdout[:4] == b"RIFF":
                # Parse WAV header & resample if needed
                import wave

                with wave.open(io.BytesIO(proc.stdout), "rb") as wf:
                    sr = wf.getframerate()
                    ch = wf.getnchannels()
                    pcm = wf.readframes(wf.getnframes())
                if sr == sample_rate and ch == 1:
                    return pcm
                ff = shutil.which("ffmpeg")
                if ff:
                    p2 = subprocess.run(
                        [
                            ff, "-y", "-loglevel", "error",
                            "-f", "s16le", "-ar", str(sr), "-ac", str(ch), "-i", "pipe:0",
                            "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "pipe:1",
                        ],
                        input=pcm,
                        capture_output=True,
                        timeout=8.0,
                    )
                    if p2.returncode == 0 and p2.stdout:
                        return p2.stdout
                return pcm
        except Exception:  # noqa: BLE001
            pass

    # Fallback: crisp two-note Meta-OS confirmation chime (16kHz mono PCM16, 0.36s)
    dur_samples = int(sample_rate * 0.36)
    out = bytearray(dur_samples * 2)
    for i in range(dur_samples):
        freq = 523.25 if i < dur_samples // 2 else 659.25
        local = (i % (dur_samples // 2)) / max(1, dur_samples // 2)
        env = math.sin(math.pi * min(1.0, local / 0.12)) * math.exp(-3.0 * local)
        val = max(-32767, min(32767, int(math.sin(2.0 * math.pi * freq * i / sample_rate) * env * 14000)))
        struct.pack_into("<h", out, i * 2, val)
    return bytes(out)
