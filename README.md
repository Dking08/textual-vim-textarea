# textual-vim-textarea

vim-style editing built on top of Textual's built-in `TextArea`. This is just a subclass that intercepts keys before they hit the default "every keypress inserts a character" behavior.

Built this to eventually wire vim keybindings into a TUI app, but split it out as its own module first so it could be tested properly on its own before touching real app code.

## What you get

- Modes: `NORMAL`, `INSERT`, `VISUAL`, `VISUAL LINE`, `COMMAND`
- Motions: `h j k l`, `0`, `^`, `$`, `w`, `b`, `e`, `gg`, `G` (arrow keys work too)
- Insert entry: `i a I A o O`
- Editing: `x X`, `dd dw db de`, `d$ / D`, `cc cw cb ce`, `c$ / C`,
  `yy yw yb ye`, `y$`, `p P`, `u`, `ctrl+r`
- Visual mode: `v` (charwise) / `V` (linewise), then `d x c y` act on the selection
- Counts: `3j`, `3dd`, `2dw`, even `2d3w` (counts multiply, like real vim)
- Command line: `:w`, `:q`, `:wq`, `:n` to jump to line `n` - anything else
  gets posted as a message so your app can handle its own commands
- Line numbers - this one's actually just `TextArea`'s built-in
  `show_line_numbers=True`, not something this module adds

Deliberately **not** implemented: registers beyond the default one, macros, marks, `:s///` regex substitution, folds, splits.

> If you want to more vim features, you can contribute!

## Files

- `vim_textarea.py` - the whole widget itself
- `dummy_app.py` - standalone playground app, run it and mess around
- `test_vim_textarea.py` - 25 headless tests using Textual's own `Pilot`

## Try it

```bash
pip install textual
python3 dummy_app.py
```

> Bottom bar shows the mode/pending-command line, same idea as vim's own.
> `:q` quits, `:w` just fires a notification since there's no real file
> backing the dummy app.

## Using it in your own TUI

It's a drop-in replacement for `TextArea`. Use it exactly like you'd use `TextArea`, and listen for a couple of extra messages to keep a status bar in sync and to hook up `:w` / `:q`:

```python
from vim_textarea import VimTextArea

class MyApp(App):
    def compose(self):
        yield VimTextArea("some text", language="python", show_line_numbers=True, id="editor")

    def on_vim_text_area_status_changed(self, message: VimTextArea.StatusChanged):
        # fires on every NORMAL/VISUAL/COMMAND keystroke it handles -
        # pending counts, pending operators, the command buffer, etc.
        self.query_one("#status").update(message.status)

    def on_vim_text_area_mode_changed(self, message: VimTextArea.ModeChanged):
        # fires only when the mode itself actually changes
        ...

    def on_vim_text_area_save_requested(self, message: VimTextArea.SaveRequested):
        # ':w' or ':wq' - do your actual file save here
        ...

    def on_vim_text_area_quit_requested(self, message: VimTextArea.QuitRequested):
        # ':q' or ':wq'
        self.exit()

    def on_vim_text_area_command_entered(self, message: VimTextArea.CommandEntered):
        # any ':' command that isn't w/q/wq/n - bring your own command palette
        ...
```

**Everything else - `.text`, `.document`, `.selection`, themes, syntax highlighting, `language=`, etc. - is untouched `TextArea` API, works exactly as it does normally. This class only takes over key handling while you're not in INSERT mode.**

### One gotcha if you're subclassing further

`_on_key` is the extension point. If you need to add your own key handling on top, don't fight the binding system - override `_on_key`, check mode, and either fall through to `super()._on_key(event)` or handle it yourself and call `event.stop()` + `event.prevent_default()`. Same pattern this module already uses internally.

## Known rough edges

- Word motions (`w b e`) use a simplified vim "word" definition (keyword run vs punctuation run vs whitespace). Covers the common case, isn't byte-for-byte identical to vim in every corner case.
- `cw` intentionally behaves like `ce` (doesn't eat trailing whitespace) - that's not a bug, it's a genuine vim quirk, kept on purpose.
- Only one register (the unnamed one). `dd` then `yy` then `p` pastes whatever you yanked/deleted most recently, same register for everything.

## Running the tests

```bash
pip install textual pytest pytest-asyncio
python3 -m pytest test_vim_textarea.py -v --asyncio-mode=auto
```

25 tests, all green as of this writing. 
