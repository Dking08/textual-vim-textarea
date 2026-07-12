"""
vim_textarea.py

Modal (vim-style) editing on top of plain Textual `TextArea`. All the
actual vim logic (motions, operators, counts, registers, visual mode,
command line) lives in `_modal.VimModalMixin`, shared with
`textarea_plus.VimTextAreaPlus`. This file only has what's genuinely
specific to a plain `TextArea` base: how keys get intercepted, and the
Message classes host apps listen for.

Key routing
-----------
TextArea's default `_on_key` treats every printable key as "insert this
character". To get modal editing we override `_on_key`: in INSERT mode
we fall through to the default behavior via `super()._on_key(event)`,
and in NORMAL / VISUAL / COMMAND modes we swallow the event
(`event.stop()` + `event.prevent_default()`) and route it through the
shared dispatcher instead.

(If you're using `textual-textarea`'s `TextEditor`/`TextAreaPlus` instead
of plain `TextArea` -- e.g. this is the base Harlequin's own editor
widget builds on -- use `textarea_plus.VimTextAreaPlus` instead. Its key
routing is different on purpose; see that module's docstring for why
this file's `super()._on_key()` approach would silently break
TextAreaPlus's own features.)
"""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea

from ._modal import Mode, VimModalMixin

__all__ = ["VimTextArea", "Mode"]


class VimTextArea(VimModalMixin, TextArea):
    """A TextArea with modal vim-style editing bound on top.

    Drop-in replacement for `textual.widgets.TextArea`. Subclass it the
    same way you would subclass TextArea; all of TextArea's own API
    (text, document, selection, themes, syntax highlighting, etc.)
    continues to work unchanged -- this class only intercepts key
    handling while not in INSERT mode.
    """

    class ModeChanged(Message):
        """Posted whenever the vim mode changes."""

        def __init__(self, mode: Mode) -> None:
            self.mode = mode
            super().__init__()

    class StatusChanged(Message):
        """Posted after every NORMAL/VISUAL/COMMAND keystroke this widget
        handles -- covers pending counts, pending operators, and the
        command-line buffer changing, none of which necessarily change
        `mode` itself. A host app's status bar should listen for this
        (in addition to, or instead of, ModeChanged) to stay accurate."""

        def __init__(self, status: str) -> None:
            self.status = status
            super().__init__()

    class SaveRequested(Message):
        """Posted on ':w' or ':wq'."""

    class QuitRequested(Message):
        """Posted on ':q' or ':wq'."""

    class CommandEntered(Message):
        """Posted for any ':' command this widget doesn't itself handle."""

        def __init__(self, command: str) -> None:
            self.command = command
            super().__init__()

    # ------------------------------------------------------------------
    # key routing
    # ------------------------------------------------------------------
    async def _on_key(self, event: events.Key) -> None:
        if self.mode is Mode.INSERT:
            if event.key == "escape":
                event.stop()
                event.prevent_default()
                self._enter_normal_mode(shift_cursor_left=True)
                return
            await super()._on_key(event)
            return

        if self.mode is Mode.COMMAND:
            event.stop()
            event.prevent_default()
            await self._handle_command_key(event)
            self.post_message(self.StatusChanged(self.status_text))
            return

        # NORMAL / VISUAL / VISUAL_LINE: we own every key.
        event.stop()
        event.prevent_default()
        self._handle_normal_key(event)
        self.post_message(self.StatusChanged(self.status_text))
