"""Exclusive workers: mid-flight cancel discards UI updates (no thread kill)."""

from __future__ import annotations

import threading
import time

from textual_wgpu.app import App, _worker_tls


def test_exclusive_supersede_sets_cancel():
    app = App()
    started = threading.Event()
    cancelled_seen = threading.Event()

    def first() -> None:
        started.set()
        for _ in range(50):
            if app.current_worker_cancelled():
                cancelled_seen.set()
                return
            time.sleep(0.02)

    app.run_worker(first, name="load", exclusive=True, group="g")
    assert started.wait(1.0)
    app.run_worker(lambda: None, name="load", exclusive=True, group="g")
    assert cancelled_seen.wait(1.0)


def test_call_from_thread_drops_cancelled_worker():
    app = App()
    ran: list[str] = []
    gate = threading.Event()
    done = threading.Event()

    def stale() -> None:
        gate.wait(1.0)
        app.call_from_thread(ran.append, "stale")
        done.set()

    app.run_worker(stale, name="w", exclusive=True, group="g")
    # Supersede before the stale worker posts UI work.
    app.run_worker(lambda: None, name="w", exclusive=True, group="g")
    gate.set()
    assert done.wait(1.0)
    # Give call_soon / inline run a beat.
    time.sleep(0.05)
    assert ran == []


def test_call_from_thread_runs_active_worker():
    app = App()
    ran: list[str] = []
    done = threading.Event()

    def active() -> None:
        app.call_from_thread(ran.append, "ok")
        done.set()

    app.run_worker(active, name="w", exclusive=True, group="g")
    assert done.wait(1.0)
    time.sleep(0.05)
    assert ran == ["ok"]


def test_tls_cleared_after_worker():
    app = App()
    done = threading.Event()

    def work() -> None:
        assert getattr(_worker_tls, "cancel", None) is not None
        done.set()

    app.run_worker(work, name="w", exclusive=True, group="g")
    assert done.wait(1.0)
    time.sleep(0.05)
    assert getattr(_worker_tls, "cancel", None) is None
