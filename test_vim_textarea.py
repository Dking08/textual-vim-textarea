"""
test_vim_textarea.py

Headless behavioral tests for VimTextArea, driven through Textual's own
Pilot (simulated keypresses against a real running App -- not a hand
rolled mock). Run with:

    python3 -m pytest test_vim_textarea.py -v
"""

import pytest
from textual.app import App, ComposeResult

from vim_textarea import Mode, VimTextArea


class HarnessApp(App):
    def __init__(self, text: str = "", language: str | None = None):
        super().__init__()
        self._text = text
        self._language = language

    def compose(self) -> ComposeResult:
        yield VimTextArea(self._text, id="editor")

    def on_mount(self) -> None:
        self.query_one("#editor", VimTextArea).focus()

    def on_vim_text_area_quit_requested(self, message: VimTextArea.QuitRequested) -> None:
        self.exit()


async def press_all(pilot, keys):
    for k in keys:
        await pilot.press(k)


@pytest.mark.asyncio
async def test_starts_in_normal_mode():
    app = HarnessApp("hello world")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        assert editor.mode is Mode.NORMAL


@pytest.mark.asyncio
async def test_i_enters_insert_and_escape_returns_normal():
    app = HarnessApp("hello world")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("i")
        assert editor.mode is Mode.INSERT
        await press_all(pilot, list("Hi! "))
        assert editor.text == "Hi! hello world"
        await pilot.press("escape")
        assert editor.mode is Mode.NORMAL


@pytest.mark.asyncio
async def test_hjkl_movement():
    app = HarnessApp("abc\ndef\nghi")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        assert editor.cursor_location == (0, 0)
        await pilot.press("l")
        await pilot.press("l")
        assert editor.cursor_location == (0, 2)
        await pilot.press("j")
        assert editor.cursor_location == (1, 2)
        await pilot.press("h")
        assert editor.cursor_location == (1, 1)
        await pilot.press("k")
        assert editor.cursor_location == (0, 1)


@pytest.mark.asyncio
async def test_count_prefixed_movement():
    app = HarnessApp("one two three four five")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("3l"))
        assert editor.cursor_location == (0, 3)


@pytest.mark.asyncio
async def test_x_deletes_char_under_cursor():
    app = HarnessApp("hello")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("x")
        assert editor.text == "ello"


@pytest.mark.asyncio
async def test_dd_deletes_line_and_yy_p_duplicates_line():
    app = HarnessApp("one\ntwo\nthree")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("j")  # move to "two"
        await press_all(pilot, list("dd"))
        assert editor.text == "one\nthree"

        # now yank "one" and paste below cursor
        editor.move_cursor((0, 0))
        await press_all(pilot, list("yy"))
        await pilot.press("p")
        assert editor.text == "one\none\nthree"


@pytest.mark.asyncio
async def test_dw_deletes_word():
    app = HarnessApp("hello world foo")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("dw"))
        assert editor.text == "world foo"


@pytest.mark.asyncio
async def test_cw_deletes_word_and_enters_insert():
    app = HarnessApp("hello world")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("cw"))
        assert editor.mode is Mode.INSERT
        # real vim's "cw" acts like "ce": it stops at the end of the word
        # and does NOT swallow the trailing space the way "dw" would
        assert editor.text == " world"
        await press_all(pilot, list("hi"))
        assert editor.text == "hi world"


@pytest.mark.asyncio
async def test_d_dollar_and_capital_d():
    app = HarnessApp("hello world")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("lll"))  # cursor at col 3 ('l' in hello)
        await pilot.press("D")
        assert editor.text == "hel"


@pytest.mark.asyncio
async def test_visual_mode_delete():
    app = HarnessApp("hello world")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("v")
        assert editor.mode is Mode.VISUAL
        await press_all(pilot, list("llll"))  # select "hello"
        await pilot.press("d")
        assert editor.mode is Mode.NORMAL
        assert editor.text == " world"


@pytest.mark.asyncio
async def test_visual_line_mode_delete():
    app = HarnessApp("one\ntwo\nthree\nfour")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("V")
        assert editor.mode is Mode.VISUAL_LINE
        await pilot.press("j")  # extend to cover "one" and "two"
        await pilot.press("d")
        assert editor.text == "three\nfour"
        assert editor.mode is Mode.NORMAL


@pytest.mark.asyncio
async def test_gg_and_capital_g():
    app = HarnessApp("one\ntwo\nthree\nfour\nfive")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("G")
        assert editor.cursor_location == (4, 0)
        await press_all(pilot, list("gg"))
        assert editor.cursor_location == (0, 0)


@pytest.mark.asyncio
async def test_o_and_capital_o_open_lines():
    app = HarnessApp("middle")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("o")
        assert editor.mode is Mode.INSERT
        await press_all(pilot, list("below"))
        await pilot.press("escape")
        assert editor.text == "middle\nbelow"

        editor.move_cursor((0, 0))
        await pilot.press("O")
        await press_all(pilot, list("above"))
        await pilot.press("escape")
        assert editor.text == "above\nmiddle\nbelow"


@pytest.mark.asyncio
async def test_undo_redo():
    app = HarnessApp("hello")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("x")
        assert editor.text == "ello"
        await pilot.press("u")
        assert editor.text == "hello"
        await pilot.press("ctrl+r")
        assert editor.text == "ello"


@pytest.mark.asyncio
async def test_command_mode_quit_message():
    app = HarnessApp("hello")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press(":")
        assert editor.mode is Mode.COMMAND
        await press_all(pilot, list("q"))
        assert editor.status_text == ":q"
        await pilot.press("enter")
        assert editor.mode is Mode.NORMAL
        # app should have received QuitRequested and exited
        await pilot.pause()
        assert app._exit is True


@pytest.mark.asyncio
async def test_dgg_deletes_from_cursor_to_top():
    app = HarnessApp("one\ntwo\nthree\nfour")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor((2, 0))  # "three"
        await press_all(pilot, list("d"))
        await press_all(pilot, list("gg"))
        assert editor.text == "four"


@pytest.mark.asyncio
async def test_3dd_deletes_three_lines():
    app = HarnessApp("one\ntwo\nthree\nfour\nfive")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("3dd"))
        assert editor.text == "four\nfive"


@pytest.mark.asyncio
async def test_word_backward_crosses_lines():
    app = HarnessApp("first line\nsecond")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor((1, 0))  # start of "second"
        await pilot.press("b")
        assert editor.cursor_location == (0, 6)  # start of "line"
        await pilot.press("b")
        assert editor.cursor_location == (0, 0)  # start of "first"


@pytest.mark.asyncio
async def test_2dw_deletes_two_words():
    app = HarnessApp("one two three four")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("2dw"))
        assert editor.text == "three four"


@pytest.mark.asyncio
async def test_capital_p_pastes_before_cursor():
    app = HarnessApp("hello")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("x"))  # register now holds "h", text "ello"
        editor.move_cursor((0, 0))
        await pilot.press("P")
        assert editor.text == "hello"


@pytest.mark.asyncio
async def test_a_appends_after_cursor():
    app = HarnessApp("hi")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        # cursor starts on 'h' (col 0); 'a' inserts right after it
        await pilot.press("a")
        assert editor.mode is Mode.INSERT
        await press_all(pilot, list("!"))
        assert editor.text == "h!i"
        await pilot.press("escape")

        # now append at true end of line
        await pilot.press("A")
        await press_all(pilot, list("?"))
        assert editor.text == "h!i?"


@pytest.mark.asyncio
async def test_arrow_keys_work_as_motion_aliases():
    app = HarnessApp("abc\ndef")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("right")
        await pilot.press("right")
        assert editor.cursor_location == (0, 2)
        await pilot.press("down")
        assert editor.cursor_location == (1, 2)


class StatusCapturingApp(HarnessApp):
    def __init__(self, text: str = ""):
        super().__init__(text)
        self.seen_statuses: list[str] = []

    def on_vim_text_area_status_changed(self, message: VimTextArea.StatusChanged) -> None:
        self.seen_statuses.append(message.status)


@pytest.mark.asyncio
async def test_status_changed_message_fires_while_typing_a_command():
    app = StatusCapturingApp("hello")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)

        await pilot.press(":")
        await pilot.press("q")
        await pilot.pause()

        # both the ':' keystroke and the 'q' keystroke must each have
        # produced a StatusChanged with the buffer as it stood at that time
        # (this is the bug that was reported: typing after ':' silently
        # updated the internal buffer but never told the host app)
        assert ":" in app.seen_statuses
        assert ":q" in app.seen_statuses
        assert editor.status_text == ":q"


@pytest.mark.asyncio
async def test_line_numbers_can_be_enabled():
    app = HarnessApp("one\ntwo\nthree")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        assert editor.show_line_numbers is False  # off by default, like TextArea
        editor.show_line_numbers = True
        assert editor.show_line_numbers is True
