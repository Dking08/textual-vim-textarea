"""
vim_textarea.py

A self-contained modal (vim-style) editing layer on top of Textual's
built-in TextArea widget.

Design
------
TextArea's default `_on_key` treats every printable key as "insert this
character". To get modal editing we override `_on_key`: in INSERT mode we
fall through to the default behavior, and in NORMAL / VISUAL / COMMAND
modes we swallow the event (`event.stop()` + `event.prevent_default()`)
and route it through our own dispatcher instead.

Covered (the ~90% of everyday vim usage that's worth it in a TUI app):
    Motions:    h j k l, 0, ^, $, w, b, e, gg, G, (arrow keys as aliases)
    Insert:     i, a, I, A, o, O                (Escape returns to NORMAL)
    Editing:    x, X, dd, dw/db/de, d$/D, cc, cw/cb/ce, c$/C, yy, yw/yb/ye,
                y$, p, P, u, ctrl+r
    Visual:     v (charwise), V (linewise), then d/x/c/y act on selection
    Counts:     numeric prefixes, e.g. 3j, 5dd, 2dw
    Command:    ':' opens a minimal command line; 'w' / 'q' / 'wq' post
                messages the host app can handle, anything else is posted
                as a generic CommandEntered message.

Deliberately skipped (diminishing returns for an in-app editor, not a
full text editor replacement): registers beyond the unnamed one, macros,
marks, regex :s///, folds, splits, jump lists.

This widget is meant to be used directly or subclassed. It emits
`VimTextArea.ModeChanged` whenever the mode changes so a host app can
drive a status/mode line, and exposes `status_text` for convenience.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional, Tuple

from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import TextArea
from textual.widgets.text_area import Selection

Location = Tuple[int, int]

# A "word" for w/b/e purposes: a run of keyword characters, OR a run of
# punctuation characters. Whitespace is always a separator. This mirrors
# vim's default (non-WORD) motion closely enough for everyday use.
_TOKEN_RE = re.compile(r"\w+|[^\w\s]+")


class Mode(str, Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    VISUAL = "VISUAL"
    VISUAL_LINE = "V-LINE"
    COMMAND = "COMMAND"


class VimTextArea(TextArea):
    """A TextArea with modal vim-style editing bound on top.

    Drop-in replacement for `textual.widgets.TextArea`. Subclass it the
    same way you would subclass TextArea; all of TextArea's own API
    (text, document, selection, themes, syntax highlighting, etc.)
    continues to work unchanged -- this class only intercepts key
    handling while not in INSERT mode.
    """

    mode: reactive[Mode] = reactive(Mode.NORMAL)

    class ModeChanged(Message):
        """Posted whenever the vim mode changes."""

        def __init__(self, mode: Mode) -> None:
            self.mode = mode
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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._count: str = ""
        self._pending_op: Optional[str] = None
        self._pending_g: bool = False
        self._register: str = ""
        self._register_linewise: bool = False
        self._command_buffer: str = ""
        self._pending_op_count: int = 1

    # ------------------------------------------------------------------
    # status line helper -- host apps can poll this to render e.g. a
    # bottom bar that looks like vim's own mode/command line.
    # ------------------------------------------------------------------
    @property
    def status_text(self) -> str:
        if self.mode is Mode.COMMAND:
            return f":{self._command_buffer}"
        labels = {
            Mode.NORMAL: "NORMAL",
            Mode.INSERT: "-- INSERT --",
            Mode.VISUAL: "-- VISUAL --",
            Mode.VISUAL_LINE: "-- VISUAL LINE --",
        }
        pending = f"{self._count}{self._pending_op or ''}"
        text = labels[self.mode]
        return f"{text} {pending}".rstrip()

    def watch_mode(self, mode: Mode) -> None:
        self.post_message(self.ModeChanged(mode))

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
            self._handle_command_key(event)
            return

        # NORMAL / VISUAL / VISUAL_LINE: we own every key.
        event.stop()
        event.prevent_default()
        self._handle_normal_key(event)

    # ------------------------------------------------------------------
    # mode transitions
    # ------------------------------------------------------------------
    def _enter_insert(self) -> None:
        self.mode = Mode.INSERT

    def _enter_normal_mode(self, shift_cursor_left: bool = False) -> None:
        self._pending_op = None
        self._pending_g = False
        self._count = ""
        if shift_cursor_left:
            row, col = self.cursor_location
            if col > 0:
                self.move_cursor((row, col - 1))
        else:
            # collapse any visual selection to a single point at cursor
            self.move_cursor(self.selection.end)
        self.mode = Mode.NORMAL

    def _enter_visual(self, linewise: bool) -> None:
        self.mode = Mode.VISUAL_LINE if linewise else Mode.VISUAL
        if linewise:
            row, _ = self.cursor_location
            self.selection = Selection((row, 0), (row, len(str(self.get_line(row)))))

    # ------------------------------------------------------------------
    # command line (":w", ":q", ":wq", or anything else)
    # ------------------------------------------------------------------
    def _handle_command_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self._command_buffer = ""
            self.mode = Mode.NORMAL
            return
        if event.key == "enter":
            command = self._command_buffer
            self._command_buffer = ""
            self.mode = Mode.NORMAL
            self._dispatch_command(command)
            return
        if event.key == "backspace":
            self._command_buffer = self._command_buffer[:-1]
            if not self._command_buffer and False:
                # (kept explicit: empty command line still stays open,
                # matching vim -- only Escape/Enter leave it)
                pass
            return
        if event.character and event.is_printable:
            self._command_buffer += event.character

    def _dispatch_command(self, command: str) -> None:
        stripped = command.strip()
        if stripped in ("w", "write"):
            self.post_message(self.SaveRequested())
        elif stripped in ("q", "q!", "quit"):
            self.post_message(self.QuitRequested())
        elif stripped in ("wq", "x"):
            self.post_message(self.SaveRequested())
            self.post_message(self.QuitRequested())
        elif stripped:
            self.post_message(self.CommandEntered(stripped))

    # ------------------------------------------------------------------
    # NORMAL / VISUAL dispatch
    # ------------------------------------------------------------------
    def _handle_normal_key(self, event: events.Key) -> None:
        key = event.key
        char = event.character

        # -- numeric count prefix (leading 0 is a motion, not a count) --
        if char is not None and char.isdigit() and (char != "0" or self._count):
            self._count += char
            return

        count = int(self._count) if self._count else 1
        had_count = bool(self._count)
        self._count = ""

        if key == "escape":
            if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE):
                self._enter_normal_mode()
            else:
                self._pending_op = None
                self._pending_g = False
            return

        if char == ":":
            self.mode = Mode.COMMAND
            self._command_buffer = ""
            self._pending_op = None
            return

        # -- "gg" two-key sequence (also combines with a pending operator,
        #    e.g. "dgg") --
        if self._pending_g:
            self._pending_g = False
            if char == "g":
                target_row = (
                    min(count - 1, self.document.line_count - 1) if had_count else 0
                )
                row, _ = self.cursor_location
                if self._pending_op:
                    op = self._pending_op
                    self._pending_op = None
                    self._apply_operator_range(op, (row, 0), (target_row, 0), linewise=True)
                else:
                    select = self.mode in (Mode.VISUAL, Mode.VISUAL_LINE)
                    self.move_cursor((target_row, 0), select=select)
                    if self.mode is Mode.VISUAL_LINE:
                        self._sync_visual_line_selection()
            return
        if char == "g":
            self._pending_g = True
            return

        # -- resolve an already-pending operator (d/c/y + motion) --
        if self._pending_op:
            op = self._pending_op
            self._pending_op = None
            # counts before the operator and before the motion multiply,
            # e.g. "3dd" -> 3, "d3w" -> 3, "2d3w" -> 6
            combined_count = self._pending_op_count * count
            self._pending_op_count = 1
            if char == op:  # dd / cc / yy
                row, _ = self.cursor_location
                end_row = min(row + combined_count - 1, self.document.line_count - 1)
                self._apply_operator_range(op, (row, 0), (end_row, 0), linewise=True)
                return
            # vim quirk: "cw"/"cW" act like "ce" -- they stop at the end of
            # the current word rather than swallowing trailing whitespace
            # the way "dw" does.
            motion_char = "e" if (op == "c" and char == "w") else char
            motion = self._resolve_motion(key, motion_char, combined_count, had_count)
            if motion is None:
                return
            target, linewise, inclusive = motion
            self._apply_operator_range(
                op, self.cursor_location, target, linewise=linewise, inclusive=inclusive
            )
            return

        # -- visual mode: d/x/c/y act on the current selection --
        if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE) and char in ("d", "x", "c", "y"):
            self._visual_operator(char)
            return

        # -- start a new operator --
        if char in ("d", "c", "y") and self.mode is Mode.NORMAL:
            self._pending_op = char
            self._pending_op_count = count
            return

        # -- D / C / X: line-end / char shorthand operators --
        if char == "D":
            row, col = self.cursor_location
            self._apply_operator_range("d", (row, col), self._end_of_line(row), inclusive=True)
            return
        if char == "C":
            row, col = self.cursor_location
            self._apply_operator_range("c", (row, col), self._end_of_line(row), inclusive=True)
            return
        if char == "X":
            row, col = self.cursor_location
            start_col = max(0, col - count)
            if start_col < col:
                self._apply_operator_range("d", (row, start_col), (row, col))
            return
        if char == "x":
            row, col = self.cursor_location
            line_len = len(str(self.get_line(row)))
            end_col = min(col + count, line_len)
            if end_col > col:
                self._apply_operator_range("d", (row, col), (row, end_col))
            return

        # -- enter insert mode --
        if char == "i":
            self._enter_insert()
            return
        if char == "a":
            row, col = self.cursor_location
            line_len = len(str(self.get_line(row)))
            self.move_cursor((row, min(line_len, col + 1)))
            self._enter_insert()
            return
        if char == "I":
            self.move_cursor(self.get_cursor_line_start_location(smart_home=True))
            self._enter_insert()
            return
        if char == "A":
            row, _ = self.cursor_location
            self.move_cursor(self._end_of_line(row))
            self._enter_insert()
            return
        if char == "o":
            row, _ = self.cursor_location
            end = self._end_of_line(row)
            self.insert("\n", location=end)
            self.move_cursor((row + 1, 0))
            self._enter_insert()
            return
        if char == "O":
            row, _ = self.cursor_location
            self.insert("\n", location=(row, 0))
            self.move_cursor((row, 0))
            self._enter_insert()
            return

        # -- visual mode toggles --
        if char == "v":
            if self.mode is Mode.VISUAL:
                self._enter_normal_mode()
            else:
                self._enter_visual(linewise=False)
            return
        if char == "V":
            if self.mode is Mode.VISUAL_LINE:
                self._enter_normal_mode()
            else:
                self._enter_visual(linewise=True)
            return

        # -- paste --
        if char == "p":
            self._paste(after=True)
            return
        if char == "P":
            self._paste(after=False)
            return

        # -- undo / redo --
        if char == "u":
            for _ in range(count):
                self.undo()
            return
        if key == "ctrl+r":
            for _ in range(count):
                self.redo()
            return

        # -- plain motions (also extend selection in visual modes) --
        motion = self._resolve_motion(key, char, count, had_count)
        if motion is not None:
            target, _linewise, _inclusive = motion
            select = self.mode in (Mode.VISUAL, Mode.VISUAL_LINE)
            self.move_cursor(target, select=select)
            if self.mode is Mode.VISUAL_LINE:
                self._sync_visual_line_selection()

    # ------------------------------------------------------------------
    # motions -- return (target_location, linewise, inclusive) or None
    # ------------------------------------------------------------------
    def _end_of_line(self, row: int) -> Location:
        return (row, len(str(self.get_line(row))))

    def _resolve_motion(
        self, key: str, char: Optional[str], count: int, had_count: bool
    ) -> Optional[Tuple[Location, bool, bool]]:
        row, col = self.cursor_location
        line_count = self.document.line_count

        if char == "h" or key == "left":
            return (row, max(0, col - count)), False, False
        if char == "l" or key == "right":
            line_len = len(str(self.get_line(row)))
            return (row, min(line_len, col + count)), False, False
        if char == "j" or key == "down":
            end_row = min(row + count, line_count - 1)
            return (end_row, col), True, False
        if char == "k" or key == "up":
            end_row = max(row - count, 0)
            return (end_row, col), True, False
        if char == "0":
            return (row, 0), False, False
        if char == "^":
            return self.get_cursor_line_start_location(smart_home=True), False, False
        if char == "$" or key == "end":
            end_row = min(row + count - 1, line_count - 1)
            return self._end_of_line(end_row), False, True
        if char == "w":
            return self._word_forward_location(count), False, False
        if char == "b":
            return self._word_backward_location(count), False, False
        if char == "e":
            return self._word_end_location(count), False, True
        if char == "G":
            target_row = min(count - 1, line_count - 1) if had_count else line_count - 1
            return (target_row, 0), True, False
        return None

    def _tokens(self, row: int):
        text = str(self.get_line(row))
        return [(m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]

    def _word_forward_location(self, count: int) -> Location:
        row, col = self.cursor_location
        for _ in range(count):
            row, col = self._single_word_forward(row, col)
        return row, col

    def _single_word_forward(self, row: int, col: int) -> Location:
        line_count = self.document.line_count
        for start, _end in self._tokens(row):
            if start > col:
                return row, start
        r = row
        while r + 1 < line_count:
            r += 1
            toks = self._tokens(r)
            if toks:
                return r, toks[0][0]
            return r, 0  # blank line is itself a word-stop
        return self.document.end

    def _word_backward_location(self, count: int) -> Location:
        row, col = self.cursor_location
        for _ in range(count):
            row, col = self._single_word_backward(row, col)
        return row, col

    def _single_word_backward(self, row: int, col: int) -> Location:
        r = row
        while True:
            candidates = [start for start, _end in self._tokens(r) if start < col or r != row]
            if r == row:
                candidates = [start for start, _end in self._tokens(r) if start < col]
            if candidates:
                return r, candidates[-1]
            if r == 0:
                return 0, 0
            r -= 1
            line_text = str(self.get_line(r))
            col = len(line_text) + 1  # allow full previous line to be searched
            toks = self._tokens(r)
            if toks:
                return r, toks[-1][0]
            # blank previous line counts as a stop
            return r, 0

    def _word_end_location(self, count: int) -> Location:
        row, col = self.cursor_location
        for _ in range(count):
            row, col = self._single_word_end(row, col)
        return row, col

    def _single_word_end(self, row: int, col: int) -> Location:
        line_count = self.document.line_count
        r, c = row, col
        while True:
            for start, end in self._tokens(r):
                if end - 1 > c:
                    return r, end - 1
            if r + 1 >= line_count:
                last_len = len(str(self.get_line(r)))
                return r, max(0, last_len - 1)
            r += 1
            c = -1

    # ------------------------------------------------------------------
    # visual-line selection upkeep
    # ------------------------------------------------------------------
    def _sync_visual_line_selection(self) -> None:
        start, end = self.selection
        row_a, row_b = start[0], end[0]
        lo, hi = min(row_a, row_b), max(row_a, row_b)
        if row_a <= row_b:
            new_start = (lo, 0)
            new_end = (hi, len(str(self.get_line(hi))))
        else:
            new_start = (hi, len(str(self.get_line(hi))))
            new_end = (lo, 0)
        self.selection = Selection(new_start, new_end)

    # ------------------------------------------------------------------
    # operators
    # ------------------------------------------------------------------
    def _apply_operator_range(
        self,
        op: str,
        a: Location,
        b: Location,
        linewise: bool = False,
        inclusive: bool = False,
    ) -> None:
        start, end = (a, b) if a <= b else (b, a)

        if linewise:
            start_row, end_row = start[0], end[0]
            if op == "c":
                content_start = (start_row, 0)
                content_end = self._end_of_line(end_row)
                text = self.get_text_range(content_start, content_end)
                self.delete(content_start, content_end)
                self._register, self._register_linewise = text, True
                self.move_cursor((start_row, 0))
                self._enter_insert()
                return

            full_text = self.get_text_range((start_row, 0), self._end_of_line(end_row))
            self._register, self._register_linewise = full_text, True
            if op == "d":
                line_count = self.document.line_count
                if end_row + 1 < line_count:
                    self.delete((start_row, 0), (end_row + 1, 0))
                    new_row = min(start_row, self.document.line_count - 1)
                elif start_row > 0:
                    prev_end = self._end_of_line(start_row - 1)
                    self.delete(prev_end, self._end_of_line(end_row))
                    new_row = start_row - 1
                else:
                    self.delete((start_row, 0), self._end_of_line(end_row))
                    new_row = 0
                self.move_cursor((new_row, 0))
            self._enter_normal_mode()
            return

        # charwise
        if inclusive:
            end = (end[0], end[1] + 1)
        text = self.get_text_range(start, end)
        self._register, self._register_linewise = text, False
        if op in ("d", "c"):
            self.delete(start, end)
            self.move_cursor(start)
        if op == "c":
            self._enter_insert()
        else:
            self._enter_normal_mode()

    def _visual_operator(self, char: str) -> None:
        op = "d" if char == "x" else char
        start, end = self.selection
        start, end = (start, end) if start <= end else (end, start)
        if self.mode is Mode.VISUAL_LINE:
            self._apply_operator_range(op, (start[0], 0), (end[0], 0), linewise=True)
        else:
            self._apply_operator_range(op, start, end, inclusive=True)

    # ------------------------------------------------------------------
    # paste
    # ------------------------------------------------------------------
    def _paste(self, after: bool) -> None:
        if not self._register:
            return
        row, col = self.cursor_location
        if self._register_linewise:
            text = self._register
            if not text.endswith("\n"):
                text += "\n"
            insert_row = row + 1 if after else row
            self.insert(text, location=(insert_row, 0))
            self.move_cursor((insert_row, 0))
        else:
            line_len = len(str(self.get_line(row)))
            insert_col = min(col + 1, line_len) if after else col
            self.insert(self._register, location=(row, insert_col))
            new_col = insert_col + len(self._register)
            self.move_cursor((row, max(insert_col, new_col - 1)))
