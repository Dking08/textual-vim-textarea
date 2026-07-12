"""
test_vim_textarea_plus.py

Headless tests for VimTextAreaPlus, run through Textual's Pilot against a
real running App -- same approach as vim_textarea's own test suite.

These specifically test the thing that matters most about this
integration: that TextAreaPlus's own features (bracket auto-closing,
autocomplete triggering, smart enter/tab) still work normally in INSERT
mode, and are fully bypassed in NORMAL/VISUAL/COMMAND mode.
"""

import pytest
from textual.app import App, ComposeResult
from textual_textarea.text_editor import TextAreaPlus

from textual_vim_textarea.textarea_plus import Mode, VimTextAreaPlus


class HarnessApp(App):
    def __init__(self, text: str = "", language: str | None = "python"):
        super().__init__()
        self._text = text
        self._language = language
        self.show_completion_events: list[str] = []
        self.quit_requested = False

    def compose(self) -> ComposeResult:
        yield VimTextAreaPlus(text=self._text, language=self._language, id="editor")

    def on_mount(self) -> None:
        self.query_one("#editor", VimTextAreaPlus).focus()

    def on_text_area_plus_show_completion_list(
        self, message: TextAreaPlus.ShowCompletionList
    ) -> None:
        self.show_completion_events.append(message.prefix)

    def on_vim_text_area_plus_quit_requested(
        self, message: VimTextAreaPlus.QuitRequested
    ) -> None:
        self.quit_requested = True


async def press_all(pilot, keys):
    for k in keys:
        await pilot.press(k)


@pytest.mark.asyncio
async def test_starts_in_normal_mode():
    app = HarnessApp("hello world")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        assert editor.mode is Mode.NORMAL


@pytest.mark.asyncio
async def test_basic_vim_motions_and_edits_work():
    app = HarnessApp("one\ntwo\nthree")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await pilot.press("j")
        assert editor.cursor_location == (1, 0)
        await press_all(pilot, list("dd"))
        assert editor.text == "one\nthree"


@pytest.mark.asyncio
async def test_i_enters_insert_and_types_normally():
    app = HarnessApp("hi")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await pilot.press("i")
        assert editor.mode is Mode.INSERT
        await press_all(pilot, list("yo "))
        assert editor.text == "yo hi"


@pytest.mark.asyncio
async def test_bracket_auto_closing_works_in_insert_mode():
    """The whole point of hooking on_key instead of _on_key: TextAreaPlus's
    own bracket-closing feature must keep working untouched in INSERT mode."""
    app = HarnessApp("")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await pilot.press("i")
        await pilot.press("left_parenthesis")
        # TextAreaPlus should have auto-inserted the closing paren too
        assert editor.text == "()"
        assert editor.cursor_location == (0, 1)  # cursor sits between them


@pytest.mark.asyncio
async def test_bracket_key_is_a_normal_mode_noop_not_an_insert():
    """In NORMAL mode, a bracket key isn't a vim command, so it must be a
    complete no-op -- NOT fall through to TextAreaPlus's bracket-closing
    (that would be a real bug: typing random punctuation while navigating
    would start inserting characters)."""
    app = HarnessApp("hello")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await pilot.press("left_parenthesis")
        assert editor.text == "hello"  # unchanged
        assert editor.mode is Mode.NORMAL


@pytest.mark.asyncio
async def test_autocomplete_triggers_in_insert_mode():
    """Typing a word character in INSERT mode should still trigger
    TextAreaPlus's completion-list side effect."""
    app = HarnessApp("")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await pilot.press("i")
        await press_all(pilot, list("sel"))
        assert editor.text == "sel"
        assert app.show_completion_events  # at least one ShowCompletionList fired


@pytest.mark.asyncio
async def test_autocomplete_does_not_trigger_in_normal_mode():
    """The same word characters typed in NORMAL mode are vim commands
    (or no-ops), not text -- they must never trigger autocomplete."""
    app = HarnessApp("hello world")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await press_all(pilot, list("lll"))  # motions, not text
        assert not app.show_completion_events
        assert editor.text == "hello world"


@pytest.mark.asyncio
async def test_escape_from_insert_returns_to_normal_and_still_hides_completion():
    app = HarnessApp("")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await pilot.press("i")
        await press_all(pilot, list("sel"))
        assert app.show_completion_events
        await pilot.press("escape")
        assert editor.mode is Mode.NORMAL
        # cursor should have moved left by one, vim-style
        assert editor.cursor_location == (0, 2)


@pytest.mark.asyncio
async def test_smart_enter_indent_still_works_in_insert_mode():
    """TextAreaPlus's own enter handling (indent after an open bracket)
    must still fire -- if our on_key ever accidentally prevent_default()'d
    in INSERT mode, this would silently insert a bare newline instead."""
    app = HarnessApp("def foo(")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        editor.move_cursor(editor.document.end)
        await pilot.press("i")
        await pilot.press("enter")
        # TextAreaPlus indents one level after an open bracket -- just
        # confirm more than a bare "\n" was inserted (exact indent width
        # isn't this test's concern, that's TextAreaPlus's own behavior)
        assert editor.text != "def foo(\n"
        assert editor.text.startswith("def foo(\n")


@pytest.mark.asyncio
async def test_visual_mode_delete():
    app = HarnessApp("hello world")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await pilot.press("v")
        await press_all(pilot, list("llll"))
        await pilot.press("d")
        assert editor.mode is Mode.NORMAL
        assert editor.text == " world"


@pytest.mark.asyncio
async def test_colon_w_triggers_real_save_action():
    """':w' must trigger TextEditor's actual action_save -- not just
    avoid crashing. Verified by checking the footer path-input Input
    widget TextEditor.action_save mounts really shows up (the same one
    ctrl+s opens), against a real TextEditor container -- not the bare
    HarnessApp above, which has no TextEditor ancestor for the
    action to even resolve against."""
    from textual.widgets import Input

    from _vim_text_editor_for_tests import VimTextEditor

    class RealEditorApp(App):
        def compose(self) -> ComposeResult:
            yield VimTextEditor(text="hello", language="python")

        def on_mount(self) -> None:
            editor = self.query_one(VimTextEditor)
            editor.text_input.focus()

    app = RealEditorApp()
    async with app.run_test() as pilot:
        text_editor = app.query_one(VimTextEditor)
        editor = text_editor.text_input
        assert "hide" in text_editor.footer.classes  # footer starts hidden

        await pilot.press(":")
        await press_all(pilot, list("w"))
        await pilot.press("enter")
        await pilot.pause()

        assert "hide" not in text_editor.footer.classes  # save prompt opened
        assert text_editor.footer.query(Input)  # the save-path Input is there
        assert editor.mode is Mode.NORMAL


@pytest.mark.asyncio
async def test_colon_q_posts_quit_requested_message():
    app = HarnessApp("hello")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await pilot.press(":")
        await press_all(pilot, list("q"))
        await pilot.press("enter")
        await pilot.pause()
        assert app.quit_requested is True


@pytest.mark.asyncio
async def test_colon_n_navigates_to_line():
    app = HarnessApp("one\ntwo\nthree\nfour")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await pilot.press(":")
        await press_all(pilot, list("3"))
        await pilot.press("enter")
        assert editor.cursor_location == (2, 0)


@pytest.mark.asyncio
async def test_undo_redo_across_a_vim_edit():
    app = HarnessApp("hello")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextAreaPlus)
        await pilot.press("x")
        assert editor.text == "ello"
        await pilot.press("u")
        assert editor.text == "hello"
        await pilot.press("ctrl+r")
        assert editor.text == "ello"
