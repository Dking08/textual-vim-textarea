"""
_modal.py

All of the actual vim-editing logic, factored out of any specific base
class. This is the piece VimTextArea (built on plain Textual TextArea)
and VimTextAreaPlus (built on textual-textarea's TextAreaPlus) share --
motions, operators, counts, registers, visual mode, the command line
state machine.

VimModalMixin deliberately does NOT hook on_key or _on_key, and does NOT
define any Message subclasses. Both of those have to live on the
*concrete* class (VimTextArea / VimTextAreaPlus), not here, for two
reasons:

  1. Key routing genuinely differs between the two bases. Plain TextArea
     only has `_on_key`, so entering INSERT mode there means manually
     falling through to `super()._on_key(event)`. TextAreaPlus builds
     its own features (autocomplete, bracket-closing, smart indent)
     through the public `on_key` handler instead, and Textual calls
     every class in the MRO that defines its own `_on_key`/`on_key` in
     turn (see textual_vim_textarea.textarea_plus's module docstring for
     the full mechanics) -- so VimTextAreaPlus has to hook `on_key` and
     do nothing at all in INSERT mode, letting the event cascade through
     TextAreaPlus's own handler naturally. Folding both strategies into
     one method here would mean neither works correctly.

  2. Textual derives a message's handler method name from the class it's
     *nested inside* (`VimTextArea.StatusChanged` -> handler name
     `on_vim_text_area_status_changed`), not from where the logic that
     posts it happens to live. If `StatusChanged` were defined on this
     mixin instead, every consumer's handler name would become
     `on_vim_modal_mixin_status_changed` regardless of which concrete
     class they're actually using -- and worse, it would've silently
     changed already-shipped handler names for existing VimTextArea
     users. So each concrete class defines its own copy of the Message
     classes (a few lines of boilerplate); this mixin only ever
     *references* them dynamically via `self.ModeChanged(...)` etc.,
     which Python resolves against the instance's real class.

Any class mixing this in is expected to also provide (transitively, via
its other base) TextArea's own API: `.document`, `.selection`,
`.cursor_location`, `.get_line()`, `.get_text_range()`, `.delete()`,
`.insert()`, `.move_cursor()`, `.undo()`, `.redo()`,
`.get_cursor_line_start_location()`. It's also expected to define, on
itself, nested `ModeChanged`, `StatusChanged`, `CommandEntered`, and
`QuitRequested` Message classes, plus a `_do_save()` (async) and
`_do_quit()` method -- see VimTextArea / VimTextAreaPlus for the concrete
shape.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional, Tuple

from textual import events
from textual.reactive import reactive
from textual.widgets.text_area import Selection

Location = Tuple[int, int]
TextObjectSegment = Tuple[Location, Location, str]

# A "word" for w/b/e purposes: a run of keyword characters, OR a run of
# punctuation characters. Whitespace is always a separator. This mirrors
# vim's default (non-WORD) motion closely enough for everyday use.
_TOKEN_RE = re.compile(r"\w+|[^\w\s]+")
_WORD_OBJECT_RE = re.compile(r"\w+|[^\w\s]+|\s+")


class Mode(str, Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    VISUAL = "VISUAL"
    VISUAL_LINE = "V-LINE"
    COMMAND = "COMMAND"


class VimModalMixin:
    """Shared vim state machine: motions, operators, counts, registers,
    visual mode, and the command line. See the module docstring for what
    a concrete class mixing this in still has to provide itself.
    """

    mode: reactive[Mode] = reactive(Mode.NORMAL)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._count: str = ""
        self._pending_op: Optional[str] = None
        self._pending_text_object: Optional[str] = None
        self._pending_text_object_count: int = 1
        self._visual_text_object_range: Optional[Tuple[Location, Location]] = None
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
        pending = (
            f"{self._count}{self._pending_op or ''}{self._pending_text_object or ''}"
        )
        text = labels[self.mode]
        return f"{text} {pending}".rstrip()

    def watch_mode(self, mode: Mode) -> None:
        self.post_message(self.ModeChanged(mode))

    # ------------------------------------------------------------------
    # mode transitions
    # ------------------------------------------------------------------
    def _enter_insert(self) -> None:
        self.mode = Mode.INSERT

    def _enter_normal_mode(self, shift_cursor_left: bool = False) -> None:
        self._pending_op = None
        self._pending_text_object = None
        self._pending_text_object_count = 1
        self._visual_text_object_range = None
        self._pending_g = False
        self._count = ""
        if shift_cursor_left:
            row, col = self.cursor_location
            if col > 0:
                self.move_cursor((row, col - 1))
        else:
            self.move_cursor(self.selection.end)
        self.mode = Mode.NORMAL

    def _enter_visual(self, linewise: bool) -> None:
        self.mode = Mode.VISUAL_LINE if linewise else Mode.VISUAL
        if linewise:
            row, _ = self.cursor_location
            self.selection = Selection((row, 0), (row, len(str(self.get_line(row)))))

    # ------------------------------------------------------------------
    # command line (":w", ":q", ":wq", ":n", or anything else). Concrete
    # classes plug in what "save" and "quit" actually mean by overriding
    # _do_save()/_do_quit() -- see their docstrings.
    # ------------------------------------------------------------------
    async def _handle_command_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self._command_buffer = ""
            self.mode = Mode.NORMAL
            return
        if event.key == "enter":
            command = self._command_buffer
            self._command_buffer = ""
            self.mode = Mode.NORMAL
            await self._dispatch_command(command)
            return
        if event.key == "backspace":
            self._command_buffer = self._command_buffer[:-1]
            return
        if event.character and event.is_printable:
            self._command_buffer += event.character

    async def _dispatch_command(self, command: str) -> None:
        stripped = command.strip()
        if stripped in ("w", "write"):
            await self._do_save()
        elif stripped in ("q", "q!", "quit"):
            await self._do_quit()
        elif stripped in ("wq", "x"):
            await self._do_save()
            await self._do_quit()
        elif stripped.isdigit():
            # ":n" -- jump to line n (1-indexed, like vim)
            target_row = max(0, min(int(stripped) - 1, self.document.line_count - 1))
            self.move_cursor((target_row, 0))
        elif stripped:
            self.post_message(self.CommandEntered(stripped))

    async def _do_save(self) -> None:
        """Default ':w' behavior: post SaveRequested for a host app to
        handle. Override this if your base class has a real, directly
        callable save action to trigger instead (see VimTextAreaPlus)."""
        self.post_message(self.SaveRequested())

    async def _do_quit(self) -> None:
        """Default ':q' behavior: post QuitRequested. Deliberately just a
        message, not a direct app.exit() call -- what ':q' should mean
        (close a buffer? close the whole app?) is a host-app decision.
        Override this if your base class has a real, directly callable
        action to trigger instead (see VimTextAreaPlus, which tries
        "close_buffer" on an ancestor first)."""
        self.post_message(self.QuitRequested())

    async def _trigger_ancestor_action(self, action_name: str) -> bool:
        """Find the nearest ancestor with an `action_<name>` method and
        call it directly. Returns True if one was found and called.

        Deliberately NOT `self.run_action(action_name)` -- Textual
        resolves an un-prefixed action name against the *calling* widget
        by default, not an ancestor, so it would silently look for the
        action on this widget itself and do nothing if the action
        actually lives on a parent container (verified by reading
        `App._parse_action` -- there's no ancestor fallback there).
        """
        import inspect

        for node in self.ancestors:
            method = getattr(node, f"action_{action_name}", None)
            if callable(method):
                result = method()
                if inspect.isawaitable(result):
                    await result
                return True
        return False

    # ------------------------------------------------------------------
    # NORMAL / VISUAL dispatch
    # ------------------------------------------------------------------
    def _handle_normal_key(self, event: events.Key) -> bool:
        """Try to handle `event` as a vim NORMAL/VISUAL-mode command.

        Returns True if this was consumed as a vim action (including a
        deliberate no-op, like an unmapped printable letter -- that must
        still be swallowed, or it would fall through and get inserted as
        text). Returns False if this key means nothing to vim at all --
        the caller should NOT prevent_default()/stop() it in that case,
        so it can still reach whatever bindings the host app has
        configured.
        """
        key = event.key
        char = event.character

        if key == "escape":
            if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE):
                self._enter_normal_mode()
            else:
                self._count = ""
                self._pending_op = None
                self._pending_text_object = None
                self._pending_text_object_count = 1
                self._pending_g = False
            return True

        # -- resolve an inner/around text object --
        if self._pending_text_object:
            text_object = self._pending_text_object
            text_object_count = self._pending_text_object_count
            self._pending_text_object = None
            self._pending_text_object_count = 1

            if char not in ("w", '"', "'", "`"):
                self._pending_op = None
                self._pending_op_count = 1
                return True

            extending_visual_word = (
                char == "w"
                and text_object == "i"
                and self._pending_op is None
                and self.mode is Mode.VISUAL
                and self.selection.start != self.selection.end
            )
            text_range = self._text_object_range(
                char,
                around=text_object == "a",
                count=text_object_count + int(extending_visual_word),
            )
            if text_range is None:
                self._pending_op = None
                self._pending_op_count = 1
                return True

            start, end = text_range
            if self._pending_op:
                op = self._pending_op
                self._pending_op = None
                self._pending_op_count = 1
                if (
                    char == "w"
                    and text_object == "i"
                    and text_object_count == 1
                    and self.get_text_range(start, end) == self.document.newline
                ):
                    return True
                self._apply_operator_range(op, start, end)
            else:
                self._select_text_object(
                    start, end, object_key=char, around=text_object == "a"
                )
            return True

        # -- numeric count prefix (leading 0 is a motion, not a count) --
        if char is not None and char.isdigit() and (char != "0" or self._count):
            self._count += char
            return True

        count = int(self._count) if self._count else 1
        had_count = bool(self._count)
        self._count = ""

        if char == ":":
            self.mode = Mode.COMMAND
            self._command_buffer = ""
            self._pending_op = None
            return True

        # -- "gg" two-key sequence (also combines with a pending
        #    operator, e.g. "dgg") --
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
            return True
        if char == "g":
            self._pending_g = True
            return True

        # -- resolve an already-pending operator (d/c/y + motion) --
        if self._pending_op:
            if char in ("i", "a"):
                self._pending_text_object = char
                self._pending_text_object_count = self._pending_op_count * count
                return True

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
                return True
            # vim quirk: "cw"/"cW" act like "ce" -- they stop at the end
            # of the current word rather than swallowing trailing
            # whitespace the way "dw" does.
            motion_char = "e" if (op == "c" and char == "w") else char
            motion = self._resolve_motion(key, motion_char, combined_count, had_count)
            if motion is None:
                # invalid motion after an operator -- vim just cancels
                # the pending operator silently; still "ours" to consume
                return True
            target, linewise, inclusive = motion
            self._apply_operator_range(
                op, self.cursor_location, target, linewise=linewise, inclusive=inclusive
            )
            return True

        # -- visual mode: d/x/c/y act on the current selection --
        if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE) and char in ("d", "x", "c", "y"):
            self._visual_operator(char)
            return True

        # -- start a new operator --
        if char in ("d", "c", "y") and self.mode is Mode.NORMAL:
            self._pending_op = char
            self._pending_op_count = count
            return True

        # -- D / C / X: line-end / char shorthand operators --
        if char == "D":
            row, col = self.cursor_location
            self._apply_operator_range("d", (row, col), self._end_of_line(row), inclusive=True)
            return True
        if char == "C":
            row, col = self.cursor_location
            self._apply_operator_range("c", (row, col), self._end_of_line(row), inclusive=True)
            return True
        if char == "X":
            row, col = self.cursor_location
            start_col = max(0, col - count)
            if start_col < col:
                self._apply_operator_range("d", (row, start_col), (row, col))
            return True
        if char == "x":
            row, col = self.cursor_location
            line_len = len(str(self.get_line(row)))
            end_col = min(col + count, line_len)
            if end_col > col:
                self._apply_operator_range("d", (row, col), (row, end_col))
            return True

        # -- visual word text objects --
        if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE) and char in ("i", "a"):
            self._pending_text_object = char
            self._pending_text_object_count = count
            return True

        # -- enter insert mode --
        if char == "i":
            self._enter_insert()
            return True
        if char == "a":
            row, col = self.cursor_location
            line_len = len(str(self.get_line(row)))
            self.move_cursor((row, min(line_len, col + 1)))
            self._enter_insert()
            return True
        if char == "I":
            self.move_cursor(self.get_cursor_line_start_location(smart_home=True))
            self._enter_insert()
            return True
        if char == "A":
            row, _ = self.cursor_location
            self.move_cursor(self._end_of_line(row))
            self._enter_insert()
            return True
        if char == "o":
            row, _ = self.cursor_location
            end = self._end_of_line(row)
            self.insert("\n", location=end)
            self.move_cursor((row + 1, 0))
            self._enter_insert()
            return True
        if char == "O":
            row, _ = self.cursor_location
            self.insert("\n", location=(row, 0))
            self.move_cursor((row, 0))
            self._enter_insert()
            return True

        # -- visual mode toggles --
        if char == "v":
            if self.mode is Mode.VISUAL:
                self._enter_normal_mode()
            else:
                self._enter_visual(linewise=False)
            return True
        if char == "V":
            if self.mode is Mode.VISUAL_LINE:
                self._enter_normal_mode()
            else:
                self._enter_visual(linewise=True)
            return True

        # -- paste --
        if char == "p":
            self._paste(after=True)
            return True
        if char == "P":
            self._paste(after=False)
            return True

        # -- undo / redo --
        if char == "u":
            for _ in range(count):
                self.undo()
            return True
        if key == "ctrl+r":
            for _ in range(count):
                self.redo()
            return True

        # -- plain motions (also extend selection in visual modes) --
        motion = self._resolve_motion(key, char, count, had_count)
        if motion is not None:
            target, _linewise, _inclusive = motion
            select = self.mode in (Mode.VISUAL, Mode.VISUAL_LINE)
            if select:
                self._visual_text_object_range = None
            self.move_cursor(target, select=select)
            if self.mode is Mode.VISUAL_LINE:
                self._sync_visual_line_selection()
            return True

        # Nothing above recognized this key as a vim command.
        if char is not None and event.is_printable:
            # A stray printable character (an unmapped letter like 'z' or
            # 'm', or punctuation) must still be swallowed -- letting it
            # fall through would insert it as text, which real vim's
            # NORMAL mode never does.
            return True

        # A non-printable, unrecognized key (function keys, most
        # ctrl+letter combinations, etc.) isn't a vim thing at all --
        # don't consume it, so it can reach the host app's own bindings.
        return False

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

    def _text_object_range(
        self, key: str, around: bool, count: int
    ) -> Optional[Tuple[Location, Location]]:
        if key == "w":
            return self._word_text_object_range(around=around, count=count)
        if key in ('"', "'", "`"):
            return self._quoted_text_object_range(
                quote=key, around=around, include_quotes=count > 1
            )
        return None

    def _word_text_object_range(
        self, around: bool, count: int
    ) -> Optional[Tuple[Location, Location]]:
        segments = self._word_object_segments()
        if not segments:
            return None

        cursor = self.cursor_location
        segment_index = next(
            (
                index
                for index, (start, end, _) in enumerate(segments)
                if start <= cursor < end
            ),
            None,
        )
        if segment_index is None and cursor == self.document.end:
            segment_index = len(segments) - 1
        if segment_index is None:
            return None

        if not around:
            if segments[segment_index][2] == "newline" and count == 1:
                return segments[segment_index][0], segments[segment_index][1]
            remaining = count
            end_index = segment_index
            for index in range(segment_index, len(segments)):
                end_index = index
                if segments[index][2] != "newline":
                    remaining -= 1
                if remaining == 0:
                    break
            return segments[segment_index][0], segments[end_index][1]

        start_index = segment_index
        if segments[start_index][2] != "word":
            word_indices = [
                index
                for index in range(start_index + 1, len(segments))
                if segments[index][2] == "word"
            ]
            if not word_indices:
                word_indices = [
                    index
                    for index in range(start_index - 1, -1, -1)
                    if segments[index][2] == "word"
                ]
                if not word_indices:
                    return segments[start_index][0], segments[start_index][1]
                start_index = word_indices[0]
                return segments[start_index][0], segments[segment_index][1]
        else:
            word_indices = [
                index
                for index in range(start_index, len(segments))
                if segments[index][2] == "word"
            ]

        end_word_index = word_indices[min(count - 1, len(word_indices) - 1)]
        if segments[segment_index][2] != "word":
            return segments[segment_index][0], segments[end_word_index][1]

        end_index = end_word_index
        if end_index + 1 < len(segments) and segments[end_index + 1][2] == "space":
            end_index += 1
        elif start_index > 0 and segments[start_index - 1][2] == "space":
            start_index -= 1
        return segments[start_index][0], segments[end_index][1]

    def _word_object_segments(self) -> list[TextObjectSegment]:
        segments: list[TextObjectSegment] = []
        last_row = self.document.line_count - 1
        for row in range(self.document.line_count):
            line = str(self.get_line(row))
            for match in _WORD_OBJECT_RE.finditer(line):
                kind = "space" if match.group().isspace() else "word"
                segments.append(((row, match.start()), (row, match.end()), kind))
            if row < last_row:
                segments.append(((row, len(line)), (row + 1, 0), "newline"))
        return segments

    def _quoted_text_object_range(
        self, quote: str, around: bool, include_quotes: bool
    ) -> Optional[Tuple[Location, Location]]:
        row, col = self.cursor_location
        line = str(self.get_line(row))
        delimiters = [
            index
            for index, char in enumerate(line)
            if char == quote and not self._is_escaped(line, index)
        ]
        if len(delimiters) < 2:
            return None

        if col in delimiters:
            delimiter_index = delimiters.index(col)
            if delimiter_index % 2 == 0:
                if delimiter_index + 1 >= len(delimiters):
                    return None
                opening, closing = delimiters[delimiter_index : delimiter_index + 2]
            else:
                opening, closing = delimiters[delimiter_index - 1 : delimiter_index + 1]
        else:
            left = [index for index in delimiters if index < col]
            right = [index for index in delimiters if index > col]
            if not right:
                return None
            opening = left[-1] if left else delimiters[0]
            closing = right[0] if left else delimiters[1]

        if not around and not include_quotes:
            return (row, opening + 1), (row, closing)

        start = opening
        end = closing + 1
        if around:
            trailing_end = end
            while trailing_end < len(line) and line[trailing_end].isspace():
                trailing_end += 1
            if trailing_end > end:
                end = trailing_end
            else:
                while start > 0 and line[start - 1].isspace():
                    start -= 1
        return (row, start), (row, end)

    def _is_escaped(self, line: str, index: int) -> bool:
        backslashes = 0
        index -= 1
        while index >= 0 and line[index] == "\\":
            backslashes += 1
            index -= 1
        return backslashes % 2 == 1

    def _select_text_object(
        self,
        start: Location,
        end: Location,
        object_key: str,
        around: bool,
    ) -> None:
        was_linewise = self.mode is Mode.VISUAL_LINE
        extending = self.mode is Mode.VISUAL and self.selection.start != self.selection.end

        if extending and object_key == "w" and around:
            segments = self._word_object_segments()
            trailing = next(
                (
                    segment_end
                    for segment_start, segment_end, kind in segments
                    if segment_start == end and kind == "space"
                ),
                None,
            )
            if trailing is not None:
                end = trailing

        object_end = self._location_before(end)
        if was_linewise or not extending:
            self.selection = Selection(start, object_end)
            selection_start, selection_end = start, end
        else:
            forward = self.selection.start <= self.selection.end
            selection_start = min(self.selection.start, self.selection.end, start)
            selection_end = max(
                self._location_after(max(self.selection.start, self.selection.end)), end
            )
            inclusive_selection_end = self._location_before(selection_end)
            self.selection = (
                Selection(selection_start, inclusive_selection_end)
                if forward
                else Selection(inclusive_selection_end, selection_start)
            )
        self._visual_text_object_range = selection_start, selection_end
        self.mode = Mode.VISUAL

    def _location_before(self, location: Location) -> Location:
        row, col = location
        if col > 0:
            return row, col - 1
        if row > 0:
            return row - 1, len(str(self.get_line(row - 1)))
        return location

    def _location_after(self, location: Location) -> Location:
        row, col = location
        line_length = len(str(self.get_line(row)))
        if col < line_length:
            return row, col + 1
        if row + 1 < self.document.line_count:
            return row + 1, 0
        return location

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
            if r == row:
                candidates = [start for start, _end in self._tokens(r) if start < col]
            else:
                candidates = [start for start, _end in self._tokens(r)]
            if candidates:
                return r, candidates[-1]
            if r == 0:
                return 0, 0
            r -= 1
            toks = self._tokens(r)
            if toks:
                return r, toks[-1][0]
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
        if self._visual_text_object_range is not None:
            start, end = self._visual_text_object_range
            self._visual_text_object_range = None
            self._apply_operator_range(op, start, end)
            return
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
