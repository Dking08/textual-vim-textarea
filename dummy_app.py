"""
dummy_app.py

A minimal standalone Textual app for interactively trying out VimTextArea
before it gets wired into a real TUI. Run it with:

    python3 dummy_app.py

Bottom status bar mirrors vim's own mode/command line: shows NORMAL,
-- INSERT --, -- VISUAL --, -- VISUAL LINE --, pending counts/operators,
or ":<command>" while typing a command.

:w   -> just shows a notification (no real file I/O in this dummy)
:q   -> quits the app
:wq  -> both
"""

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Static

from vim_textarea import VimTextArea

SAMPLE_TEXT = """\
def greet(name):
    print("hello", name)


def main():
    greet("world")
    numbers = [1, 2, 3, 4, 5]
    total = sum(numbers)
    print(total)


if __name__ == "__main__":
    main()
"""


class StatusBar(Static):
    pass


class DummyVimApp(App):
    CSS = """
    Screen {
        background: #0d1117;
    }
    VimTextArea {
        border: solid #30363d;
        height: 1fr;
    }
    StatusBar {
        dock: bottom;
        height: 1;
        background: #161b22;
        color: #58a6ff;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield VimTextArea(SAMPLE_TEXT, language="python", id="editor")
            yield StatusBar("NORMAL", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#editor", VimTextArea).focus()
        self._refresh_status()

    def _refresh_status(self) -> None:
        editor = self.query_one("#editor", VimTextArea)
        self.query_one("#status", StatusBar).update(editor.status_text)

    def on_vim_text_area_mode_changed(self, message: VimTextArea.ModeChanged) -> None:
        self._refresh_status()

    def on_key(self, event) -> None:
        # cheap way to keep the status bar live while counts/operators
        # accumulate within NORMAL mode (mode itself doesn't change)
        self.call_after_refresh(self._refresh_status)

    def on_vim_text_area_save_requested(self, message: VimTextArea.SaveRequested) -> None:
        self.notify("Saved (dummy -- no real file I/O here)")

    def on_vim_text_area_quit_requested(self, message: VimTextArea.QuitRequested) -> None:
        self.exit()

    def on_vim_text_area_command_entered(self, message: VimTextArea.CommandEntered) -> None:
        self.notify(f"Unhandled command: :{message.command}")


if __name__ == "__main__":
    DummyVimApp().run()
