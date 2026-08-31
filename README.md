# textual-vim-textarea

vim-style editing built on top of Textual's built-in `TextArea`. This is just a subclass that intercepts keys before they hit the default "every keypress inserts a character" behavior.

Built this to eventually wire vim keybindings into a TUI app, but split it out as its own module first so it could be tested properly on its own before touching real app code.

## Two flavors

- **`VimTextArea`** - for plain Textual `TextArea`. Zero extra dependencies beyond `textual` itself.
- **`VimTextAreaPlus`** - for apps built on [`textual-textarea`](https://github.com/tconbeer/textual-textarea)'s `TextEditor`/`TextAreaPlus` instead of plain `TextArea`. 

Both share the same underlying vim logic (`_modal.VimModalMixin`) - same motions, operators, counts, everything. They only differ in how they hook into their respective base widget's key handling, because the two bases genuinely work differently under the hood (see `textarea_plus.py`'s module docstring if you're curious exactly why).

## What you get

- Modes: `NORMAL`, `INSERT`, `VISUAL`, `VISUAL LINE`, `COMMAND`
- Motions: `h j k l`, `0`, `^`, `$`, `w`, `b`, `e`, `gg`, `G` (arrow keys work too)
- Insert entry: `i a I A o O`
- Editing: `x X`, `dd dw db de`, `d$ / D`, `cc cw cb ce`, `c$ / C`,
  `yy yw yb ye`, `y$`, `p P`, `u`, `ctrl+r`
- Visual mode: `v` (charwise) / `V` (linewise), then `d x c y` act on the selection
- Text objects: `iw` / `aw` and quoted strings (`i"`, `a"`, `i'`, `a'`,
  `` i` ``, `` a` ``) in Visual mode or after `d`, `c`, and `y`
- Counts: `3j`, `3dd`, `2dw`, even `2d3w` (counts multiply, like real vim)
- Command line: `:w`, `:q`, `:wq`, `:n` to jump to line `n` - anything else
  gets posted as a message so your app can handle its own commands
- Line numbers - this one's actually just `TextArea`'s built-in
  `show_line_numbers=True`, not something this module adds

**Not** currently implemented: registers beyond the default one, macros, marks, `:s///` regex substitution, folds, splits.

> If you want to more vim features, you can contribute!

## Installation

```bash
pip install textual-vim-textarea
```

## Project layout

- `src/textual_vim_textarea/_modal.py` - shared vim logic, not meant to be used directly
- `src/textual_vim_textarea/vim_textarea.py` - `VimTextArea`, built on plain `TextArea`. Primary file.
- `src/textual_vim_textarea/textarea_plus.py` - `VimTextAreaPlus`, built on `textual-textarea`'s `TextAreaPlus`.

- `examples/dummy_app.py` - standalone playground for `VimTextArea`
- `examples/dummy_app_plus.py` + `examples/vim_text_editor.py` - standalone playground for `VimTextAreaPlus`, and the `TextEditor` subclass pattern to copy into a real app

- `tests/test_vim_textarea.py`, `tests/test_textarea_plus.py` - headless tests using Textual's `Pilot`

## Try it

```bash
uv sync
python examples/dummy_app.py
```

> Bottom bar shows the mode/pending-command line, same idea as vim's own.
> `:q` quits, `:w` just fires a notification since there's no real file
> backing the dummy app.

For the `TextAreaPlus` flavor (needs the extra) - OPTIONAL:

```bash
uv sync --extra textarea-plus
python examples/dummy_app_plus.py
```

## Using it in your own TUI

It's a drop-in replacement for `TextArea`. Use it exactly like you'd use `TextArea`, and listen for a couple of extra messages to keep a status bar in sync and to hook up `:w` / `:q`:

```python
from textual_vim_textarea import VimTextArea

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

`VimTextArea` uses `_on_key` as its extension point. If you need to add your own key handling on top, don't fight the binding system - override `_on_key`, check mode, and either fall through to `super()._on_key(event)` or handle it yourself and call `event.stop()` + `event.prevent_default()`. Same pattern this module already uses internally.

`VimTextAreaPlus` is different on purpose - it hooks `on_key`, not `_on_key`, and in INSERT mode does **nothing at all** (not even calling `super()`) so the event cascades naturally through `TextAreaPlus`'s own `on_key` and then base `TextArea`'s `_on_key`. If you're subclassing `VimTextAreaPlus` further, match that: don't call `super().on_key()` manually, just decide whether to `prevent_default()`+`stop()` or leave the event alone entirely. See `textarea_plus.py`'s module docstring for the actual Textual dispatch mechanics this depends on - it's worth reading before you touch it, the ordering isn't obvious from the outside.

### Using VimTextAreaPlus

Same idea, but you also get to decide what `:w`/`:q` actually do, since `TextAreaPlus`'s real save flow lives on an ancestor `TextEditor` widget, not on the text area itself:

```python
from textual_vim_textarea.textarea_plus import VimTextAreaPlus

class MyCodeEditor(TextEditor):  # from textual_textarea
    def compose(self):
        self.text_input = VimTextAreaPlus(language="sql", text=self._initial_text)
        ...  # rest of TextEditor's own compose() body, unchanged

    def on_vim_text_area_plus_quit_requested(self, message):
        # ':q' - deliberately just a message. Closing a buffer/tab vs.
        # quitting the whole app is your call, not this widget's.
        ...
```

`:w` already works out of the box - it walks up to whatever ancestor has `action_save` (the real `TextEditor` action ctrl+s also triggers) and calls it directly, since Textual's own `run_action()` won't resolve an un-prefixed action name against an ancestor by default (verified this the hard way, see the integration guide).

## Known rough edges

- Word motions (`w b e`) use a simplified vim "word" definition (keyword run vs punctuation run vs whitespace). Covers the common case, isn't byte-for-byte identical to vim in every corner case.
- `cw` intentionally behaves like `ce` (doesn't eat trailing whitespace).
- Only one register (the unnamed one). `dd` then `yy` then `p` pastes whatever you yanked/deleted most recently, same register for everything.

## Running the tests

```bash
uv run pytest tests -v --asyncio-mode=auto
```

44 tests, all green as of this writing - 27 for `VimTextArea`, 17 for `VimTextAreaPlus`
