"""
textarea_plus.py

Vim-modal editing for apps built on `textual-textarea`'s `TextEditor` /
`TextAreaPlus` rather than plain
Textual `TextArea`. Requires the `textarea-plus` extra:

    pip install textual-vim-textarea[textarea-plus]

All the actual vim logic is shared with `vim_textarea.VimTextArea` via
`_modal.VimModalMixin`. This file only has what's specific to
TextAreaPlus: how keys get intercepted (genuinely different from plain
TextArea -- see below), the Message classes, and how ':w' triggers a
real save.

Why this isn't just VimTextArea reused
---------------------------------------
`TextAreaPlus(TextArea, inherit_bindings=False)` adds real editing
features on top of base TextArea through the *public* `on_key` handler,
not the *private* `_on_key` base TextArea itself uses for default
character insertion: bracket/quote auto-closing, smart enter (auto-
indent, jump inside brackets), tab/shift+tab indent handling, and an
autocomplete dropdown.

I verified Textual's actual dispatch order by reading
`MessagePump._get_dispatch_methods` / `_on_message` directly
(textual/message_pump.py), not by guessing from behavior:

  - For a Key event, Textual walks the widget's class MRO from most-
    derived to least-derived. For each class that defines its own
    `_on_key` or `on_key` (checked via `cls.__dict__`, private preferred
    over public *within that same class*), it calls that handler, then
    moves on to the next class in the MRO -- unless prevented.
  - `event.prevent_default()` sets `message._no_default_action = True`,
    which makes that walk `break` -- so calling it stops every *later*
    (more-base) class's handler from running at all.
  - `event.stop()` is a separate mechanism -- it only stops the event
    from *bubbling to the parent widget* afterwards (which is how
    some TUI's ctrl+s / ctrl+f / ctrl+g bindings on the outer
    TextEditor container end up firing even though the inner
    TextAreaPlus has actual focus).

TextAreaPlus.on_key handles special keys (enter, tab, brackets, escape)
by calling prevent_default() itself, fully replacing default TextArea
behavior for those keys. For plain printable characters it does NOT
prevent_default -- it just triggers autocomplete side effects and lets
the event keep falling through to base TextArea._on_key, which does the
actual insert.

So the correct way to layer vim modes on top is to also hook `on_key`
(matching TextAreaPlus's own handler name, so we sit earlier in the
MRO): in INSERT mode, do nothing at all for any key other than Escape --
let the event fall through exactly as if this class didn't exist, so
every TextAreaPlus feature keeps working untouched. In NORMAL / VISUAL /
COMMAND mode, prevent_default() + stop() immediately, exactly like
VimTextArea does, so TextAreaPlus never sees the key while not
inserting. (VimTextArea's `super()._on_key()` approach would NOT work
here -- it's a direct Python method call via `super()`, which completely
bypasses Textual's own per-class MRO dispatch loop and would skip
TextAreaPlus.on_key's side effects entirely.)

A real maintenance risk worth knowing about: `TextAreaPlus` is NOT part
of textual_textarea's public API (only `TextEditor` is exported from
`textual_textarea/__init__.py`). Importing it means reaching into
`textual_textarea.text_editor`, a private module path. If a future
textual-textarea release restructures that file, this import breaks with
no deprecation warning. Pin your textual-textarea version and re-check
this import on every upgrade.
"""

from __future__ import annotations

from textual import events
from textual.message import Message

from ._modal import Mode, VimModalMixin

try:
    from textual_textarea.text_editor import TextAreaPlus
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "VimTextAreaPlus requires the 'textarea-plus' extra: "
        "pip install textual-vim-textarea[textarea-plus]"
    ) from exc

__all__ = ["VimTextAreaPlus", "Mode"]


class VimTextAreaPlus(VimModalMixin, TextAreaPlus):
    """TextAreaPlus with vim modes layered on top via `on_key`, so every
    TextAreaPlus feature -- autocomplete, bracket/quote auto-closing,
    smart enter/tab indent -- keeps working exactly as before while in
    INSERT mode, and is fully bypassed while in NORMAL / VISUAL /
    COMMAND mode.
    """

    class ModeChanged(Message):
        def __init__(self, mode: Mode) -> None:
            self.mode = mode
            super().__init__()

    class StatusChanged(Message):
        def __init__(self, status: str) -> None:
            self.status = status
            super().__init__()

    class QuitRequested(Message):
        """Posted on ':q'. Deliberately NOT wired to anything by
        default -- closing a buffer vs. quitting the whole app is a real
        host-app product decision, not this widget's call to make."""

    class CommandEntered(Message):
        """Posted for any ':' command this widget doesn't itself handle."""

        def __init__(self, command: str) -> None:
            self.command = command
            super().__init__()

    # ------------------------------------------------------------------
    # key routing -- see the module docstring for exactly why this is
    # structured the way it is.
    # ------------------------------------------------------------------
    async def on_key(self, event: events.Key) -> None:
        if self.mode is Mode.INSERT:
            if event.key == "escape":
                # side effect only -- do NOT prevent_default/stop, so
                # TextAreaPlus's own escape handler (hide completion
                # list, collapse selection) still runs right after this
                self._enter_normal_mode(shift_cursor_left=True)
            return

        if self.mode is Mode.COMMAND:
            event.stop()
            event.prevent_default()
            await self._handle_command_key(event)
            self.post_message(self.StatusChanged(self.status_text))
            return

        # NORMAL / VISUAL / VISUAL_LINE: we own every key, TextAreaPlus
        # never sees it.
        event.stop()
        event.prevent_default()
        self._handle_normal_key(event)
        self.post_message(self.StatusChanged(self.status_text))

    # ------------------------------------------------------------------
    # ':w' triggers TextEditor's real save flow (the same
    # footer path-input ctrl+s opens) by walking up to the TextEditor
    # ancestor and calling its action_save directly -- see
    # VimModalMixin._trigger_ancestor_action's docstring for why plain
    # run_action() doesn't work for this.
    # ------------------------------------------------------------------
    async def _do_save(self) -> None:
        await self._trigger_ancestor_action("save")
