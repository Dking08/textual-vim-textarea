"""
dummy_app_plus.py

Standalone playground for VimTextAreaPlus -- the variant for apps built
on textual-textarea's TextEditor/TextAreaPlus rather
than plain Textual TextArea. Requires the 'textarea-plus' extra:

    pip install textual-vim-textarea[textarea-plus]
    python3 dummy_app_plus.py

Uses vim_text_editor.VimTextEditor (in this same examples/ folder) --
a TextEditor subclass that swaps in VimTextAreaPlus. That's the exact
pattern to copy into a real app's own CodeEditor-equivalent class;
"""

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static

from textual_vim_textarea.textarea_plus import VimTextAreaPlus
from vim_text_editor import VimTextEditor

SAMPLE_TEXT = """\
select
    customer_id,
    sum(amount) as total_amount
from orders
where status = 'completed'
group by customer_id
order by total_amount desc
"""


class StatusBar(Static):
    pass


class DummyVimPlusApp(App):
    CSS = """
    Screen {
        background: #0d1117;
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
        self.editor = VimTextEditor(language="sql", text=SAMPLE_TEXT)
        yield self.editor
        yield StatusBar("NORMAL", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.editor.text_input.focus()
        self._refresh_status()

    def _refresh_status(self) -> None:
        self.query_one("#status", StatusBar).update(self.editor.text_input.status_text)

    def on_vim_text_area_plus_mode_changed(
        self, message: VimTextAreaPlus.ModeChanged
    ) -> None:
        self._refresh_status()

    def on_vim_text_area_plus_status_changed(
        self, message: VimTextAreaPlus.StatusChanged
    ) -> None:
        self.query_one("#status", StatusBar).update(message.status)

    def on_vim_text_area_plus_quit_requested(
        self, message: VimTextAreaPlus.QuitRequested
    ) -> None:
        self.exit()


if __name__ == "__main__":
    DummyVimPlusApp().run()
