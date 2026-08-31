"""
test_vim_textarea.py

Headless behavioral tests for VimTextArea, driven through Textual's own
Pilot (simulated keypresses against a real running App -- not a hand
rolled mock). Run with:

    python3 -m pytest test_vim_textarea.py -v
"""

import pytest
from textual.app import App, ComposeResult

from textual_vim_textarea import VimTextArea, Mode

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
async def test_visual_inner_word_stays_in_visual_mode():
    app = HarnessApp("one two")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("l")
        await pilot.press("v")
        await pilot.press("i")
        assert editor.mode is Mode.VISUAL
        assert editor.status_text == "-- VISUAL -- i"

        await pilot.press("w")
        assert editor.mode is Mode.VISUAL
        await pilot.press("d")
        assert editor.text == " two"


@pytest.mark.asyncio
async def test_visual_around_word_includes_adjacent_space():
    app = HarnessApp("one two")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("vaw"))
        assert editor.mode is Mode.VISUAL
        await pilot.press("d")
        assert editor.text == "two"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keys", "expected_text", "expected_register", "expected_mode"),
    [
        ("diw", " two", "one", Mode.NORMAL),
        ("daw", "two", "one ", Mode.NORMAL),
        ("ciw", " two", "one", Mode.INSERT),
        ("caw", "two", "one ", Mode.INSERT),
        ("yiw", "one two", "one", Mode.NORMAL),
        ("yaw", "one two", "one ", Mode.NORMAL),
    ],
)
async def test_word_text_objects_work_with_operators(
    keys, expected_text, expected_register, expected_mode
):
    app = HarnessApp("one two")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list(keys))
        assert editor.text == expected_text
        assert editor._register == expected_register
        assert editor.mode is expected_mode


@pytest.mark.asyncio
async def test_inner_word_on_whitespace_selects_only_whitespace():
    app = HarnessApp("one   two")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor((0, 4))
        await press_all(pilot, list("viwd"))
        assert editor.text == "onetwo"


@pytest.mark.asyncio
async def test_word_text_objects_honor_counts():
    app = HarnessApp("one two three")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("d2iw"))
        assert editor.text == "two three"

        editor.text = "one two three"
        editor.move_cursor((0, 0))
        await press_all(pilot, list("v2awd"))
        assert editor.text == "three"


@pytest.mark.asyncio
async def test_inner_word_count_crosses_lines():
    app = HarnessApp("one\ntwo")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("d2iw"))
        assert editor.text == ""


@pytest.mark.asyncio
async def test_inner_word_operator_is_noop_on_blank_line():
    app = HarnessApp("\ntwo")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("diw"))
        assert editor.text == "\ntwo"


@pytest.mark.asyncio
async def test_visual_inner_word_can_delete_blank_line():
    app = HarnessApp("\ntwo")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("viwd"))
        assert editor.text == "two"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keys", "expected_register"),
    [
        ("vawawy", "one two "),
        ("viwiwy", "one "),
        ("vllliwy", "one two"),
    ],
)
async def test_word_text_objects_extend_visual_selection(keys, expected_register):
    app = HarnessApp("one two three")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list(keys))
        assert editor._register == expected_register


@pytest.mark.asyncio
async def test_count_after_text_object_prefix_is_invalid():
    app = HarnessApp("one two")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("di2w"))
        assert editor.text == "one two"
        assert editor.mode is Mode.NORMAL


@pytest.mark.asyncio
async def test_escape_clears_pending_count():
    app = HarnessApp("one\ntwo\nthree\nfour")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, ["3", "escape", "j"])
        assert editor.cursor_location == (1, 0)
        assert editor.status_text == "NORMAL"


@pytest.mark.asyncio
async def test_around_word_uses_leading_space_for_last_word():
    app = HarnessApp("one two")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor((0, 4))
        await press_all(pilot, list("daw"))
        assert editor.text == "one"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cursor", "expected_text"),
    [
        ((0, 0), "\ntwo"),
        ((1, 0), "one\n"),
    ],
)
async def test_around_word_does_not_consume_newline(cursor, expected_text):
    app = HarnessApp("one\ntwo")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor(cursor)
        await press_all(pilot, list("daw"))
        assert editor.text == expected_text


@pytest.mark.asyncio
async def test_around_word_consumes_horizontal_space_before_newline():
    app = HarnessApp("one   \ntwo")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, list("daw"))
        assert editor.text == "\ntwo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keys", "expected_text", "expected_register", "expected_mode"),
    [
        ('di"', 'select "" from tbl', "alpha beta", Mode.NORMAL),
        ('da"', "select from tbl", '"alpha beta" ', Mode.NORMAL),
        ('ci"', 'select "" from tbl', "alpha beta", Mode.INSERT),
        ('yi"', 'select "alpha beta" from tbl', "alpha beta", Mode.NORMAL),
    ],
)
async def test_quoted_text_objects_work_with_operators(
    keys, expected_text, expected_register, expected_mode
):
    app = HarnessApp('select "alpha beta" from tbl')
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor((0, 10))
        await press_all(pilot, list(keys))
        assert editor.text == expected_text
        assert editor._register == expected_register
        assert editor.mode is expected_mode


@pytest.mark.asyncio
@pytest.mark.parametrize("quote", ['"', "'", "`"])
async def test_visual_inner_quote_supports_all_quote_types(quote):
    app = HarnessApp(f"select {quote}alpha beta{quote} from tbl")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor((0, 10))
        await press_all(pilot, ["v", "i", quote])
        assert editor.mode is Mode.VISUAL
        await pilot.press("d")
        assert editor.text == f"select {quote}{quote} from tbl"


@pytest.mark.asyncio
async def test_inner_quote_ignores_escaped_delimiters():
    app = HarnessApp(r'select "alpha \"beta\" gamma" from tbl')
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor((0, 10))
        await press_all(pilot, ['d', 'i', '"'])
        assert editor.text == 'select "" from tbl'
        assert editor._register == r'alpha \"beta\" gamma'


@pytest.mark.asyncio
async def test_inner_quote_finds_the_next_quoted_string():
    app = HarnessApp('select before "alpha beta" after')
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await press_all(pilot, ['v', 'i', '"', 'y'])
        assert editor._register == "alpha beta"


@pytest.mark.asyncio
async def test_inner_quote_on_closing_delimiter_uses_its_string():
    app = HarnessApp('select "one" and "two"')
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor((0, 11))
        await press_all(pilot, ['v', 'i', '"', 'y'])
        assert editor._register == "one"


@pytest.mark.asyncio
async def test_inner_quote_between_strings_uses_surrounding_delimiters():
    app = HarnessApp('select "one" and "two"')
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor((0, 14))
        await press_all(pilot, ['d', 'i', '"'])
        assert editor.text == 'select "one""two"'
        assert editor._register == " and "


@pytest.mark.asyncio
@pytest.mark.parametrize("count", ["2", "3"])
async def test_counted_inner_quote_includes_quotes_without_whitespace(count):
    app = HarnessApp('select "alpha beta" from tbl')
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        editor.move_cursor((0, 10))
        await press_all(pilot, ['v', count, 'i', '"', 'y'])
        assert editor._register == '"alpha beta"'


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
async def test_colon_n_navigates_to_line():
    app = HarnessApp("one\ntwo\nthree\nfour\nfive")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press(":")
        await press_all(pilot, list("3"))
        await pilot.press("enter")
        assert editor.cursor_location == (2, 0)  # line 3, 0-indexed row 2
        assert editor.mode is Mode.NORMAL

        # out-of-range clamps to the last line rather than erroring
        await pilot.press(":")
        await press_all(pilot, list("999"))
        await pilot.press("enter")
        assert editor.cursor_location == (4, 0)


@pytest.mark.asyncio
async def test_line_numbers_can_be_enabled():
    app = HarnessApp("one\ntwo\nthree")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        assert editor.show_line_numbers is False  # off by default, like TextArea
        editor.show_line_numbers = True
        assert editor.show_line_numbers is True


@pytest.mark.asyncio
async def test_unmapped_non_printable_key_falls_through_to_host_bindings():
    """Same fix as VimTextAreaPlus: any host app that binds its own
    shortcuts (F2, ctrl+b, etc.) directly onto this widget must still
    have them fire while in NORMAL mode. Only keys _handle_normal_key
    actually recognizes as vim commands should be swallowed."""

    class EditorWithBinding(VimTextArea):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.f2_fired = False

        def action_test_f2(self) -> None:
            self.f2_fired = True

    class App2(App):
        def compose(self) -> ComposeResult:
            yield EditorWithBinding("hello", id="editor")

        def on_mount(self) -> None:
            editor = self.query_one("#editor", EditorWithBinding)
            editor.focus()
            editor._bindings.bind(keys="f2", action="test_f2")

    app = App2()
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", EditorWithBinding)
        assert editor.mode is Mode.NORMAL
        await pilot.press("f2")
        assert editor.f2_fired is True
        await pilot.press("x")
        assert editor.text == "ello"
        assert editor.mode is Mode.NORMAL


@pytest.mark.asyncio
async def test_unmapped_printable_letter_is_still_swallowed_not_inserted():
    app = HarnessApp("hello")
    async with app.run_test() as pilot:
        editor = app.query_one("#editor", VimTextArea)
        await pilot.press("z")
        assert editor.text == "hello"
        assert editor.mode is Mode.NORMAL
