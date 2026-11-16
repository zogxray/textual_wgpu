"""Messages — Textual ``textual.messages`` parity."""

from __future__ import annotations

from .message import Message


class ExitApp(Message):
    pass


class Update(Message):
    pass


class CloseMessages(Message):
    pass


class Prompt(Message):
    pass
