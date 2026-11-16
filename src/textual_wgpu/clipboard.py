"""System clipboard helpers (wl-clipboard / xclip / pbcopy / clip)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def write_clipboard(text: str) -> bool:
    """Write ``text`` to the OS clipboard. True if an external tool accepted it."""
    if text is None:
        return False
    commands: list[list[str]] = []
    if os.name == "nt":
        commands.append(["clip"])
    elif sys.platform == "darwin":
        commands.append(["pbcopy"])
    else:
        if shutil.which("wl-copy"):
            commands.append(["wl-copy"])
        if shutil.which("xclip"):
            commands.append(["xclip", "-selection", "clipboard"])
        if shutil.which("xsel"):
            commands.append(["xsel", "--clipboard", "--input"])
    for cmd in commands:
        try:
            subprocess.run(
                cmd,
                input=text,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return True
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            continue
    return False


def read_clipboard() -> str | None:
    """Read text from the OS clipboard, or None if unavailable / empty."""
    commands: list[list[str]] = []
    if os.name == "nt":
        # PowerShell Get-Clipboard — clip.exe is write-only.
        commands.append([
            "powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw",
        ])
    elif sys.platform == "darwin":
        commands.append(["pbpaste"])
    else:
        if shutil.which("wl-paste"):
            commands.append(["wl-paste", "-n"])
        if shutil.which("xclip"):
            commands.append(["xclip", "-selection", "clipboard", "-o"])
        if shutil.which("xsel"):
            commands.append(["xsel", "--clipboard", "--output"])
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            text = proc.stdout
            if text is None:
                continue
            # Drop a single trailing newline many paste tools add.
            if text.endswith("\r\n"):
                text = text[:-2]
            elif text.endswith("\n"):
                text = text[:-1]
            return text
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            continue
    return None
