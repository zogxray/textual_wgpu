"""Worker / ``@work`` — asyncio-native background jobs (Textual subset).

Sync callables still run in daemon threads (``thread=True`` / non-async), so
existing exclusive-worker cancel via TLS keeps working. Async callables become
``asyncio.Task``s on the app loop.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable, Coroutine
from enum import Enum, auto
from functools import wraps
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

# Re-export TLS for exclusive thread workers (shared with App.run_worker).
_worker_tls = threading.local()


class WorkerState(Enum):
    PENDING = auto()
    RUNNING = auto()
    CANCELLED = auto()
    ERROR = auto()
    SUCCESS = auto()


class Worker:
    """Handle for a running (or finished) background job."""

    def __init__(
        self,
        app: Any,
        *,
        name: str = "worker",
        group: str = "default",
        exclusive: bool = False,
    ) -> None:
        self.app = app
        self.name = name
        self.group = group
        self.exclusive = exclusive
        self.state = WorkerState.PENDING
        self.error: BaseException | None = None
        self._result: Any = None
        self._task: asyncio.Task[Any] | None = None
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._async_cancel: asyncio.Event | None = None
        self._done = threading.Event()
        self._fut: asyncio.Future[Any] | None = None

    @property
    def result(self) -> Any:
        return self._result

    @property
    def is_cancelled(self) -> bool:
        return self.state == WorkerState.CANCELLED or self._cancel_event.is_set()

    @property
    def is_finished(self) -> bool:
        return self.state in (
            WorkerState.CANCELLED, WorkerState.ERROR, WorkerState.SUCCESS,
        )

    @property
    def is_running(self) -> bool:
        return self.state == WorkerState.RUNNING

    def cancel(self) -> None:
        """Request cooperative cancel (thread Event + task.cancel)."""
        self._cancel_event.set()
        if self._async_cancel is not None:
            self._async_cancel.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self.state in (WorkerState.PENDING, WorkerState.RUNNING):
            self.state = WorkerState.CANCELLED

    async def wait(self) -> Any:
        """Await completion; return result (or re-raise)."""
        if self._fut is not None:
            return await self._fut
        if self._task is not None:
            return await self._task
        # Thread worker: poll.
        while not self._done.wait(0.05):
            await asyncio.sleep(0.05)
        if self.state == WorkerState.ERROR and self.error is not None:
            raise self.error
        return self._result

    def _finish_ok(self, result: Any) -> None:
        self._result = result
        if self.state != WorkerState.CANCELLED:
            self.state = WorkerState.SUCCESS
        self._done.set()
        self._resolve_fut(result)

    def _finish_err(self, exc: BaseException) -> None:
        self.error = exc
        if self.state != WorkerState.CANCELLED:
            self.state = WorkerState.ERROR
        self._done.set()
        if self._fut is not None and not self._fut.done():
            self._fut.set_exception(exc)

    def _resolve_fut(self, result: Any) -> None:
        if self._fut is not None and not self._fut.done():
            if self.state == WorkerState.CANCELLED:
                self._fut.cancel()
            else:
                self._fut.set_result(result)


def work(
    method: Callable[..., Any] | None = None,
    *,
    name: str = "",
    group: str = "default",
    exclusive: bool = False,
    thread: bool = False,
) -> Any:
    """Decorator: method → launches a ``Worker`` on the owning App.

    Async methods run on the app asyncio loop. Sync methods require
    ``thread=True`` (same rule as Textual).
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, Worker]:
        is_coro = inspect.iscoroutinefunction(fn)
        if not is_coro and not thread:
            raise TypeError(
                f"@work on non-async {fn.__name__!r} requires thread=True"
            )

        @wraps(fn)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> Worker:
            if not args:
                raise TypeError("@work methods need a self/app owner")
            owner = args[0]
            app = owner if hasattr(owner, "spawn_worker") else getattr(owner, "app", None)
            if app is None or not hasattr(app, "spawn_worker"):
                raise RuntimeError("@work requires an App (or mounted widget)")
            worker_name = name or fn.__name__
            if is_coro:
                async def _runner() -> Any:
                    return await fn(*args, **kwargs)

                return app.spawn_worker(
                    _runner,
                    name=worker_name,
                    group=group,
                    exclusive=exclusive,
                    thread=False,
                )

            def _sync() -> Any:
                return fn(*args, **kwargs)

            return app.spawn_worker(
                _sync,
                name=worker_name,
                group=group,
                exclusive=exclusive,
                thread=True,
            )

        return wrapped  # type: ignore[return-value]

    if method is not None:
        return decorator(method)
    return decorator


def get_current_worker() -> Worker | None:
    """Return the Worker bound to this thread / task context, if any."""
    return getattr(_worker_tls, "worker", None)
