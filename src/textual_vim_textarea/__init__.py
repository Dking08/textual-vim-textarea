from .vim_textarea import VimTextArea, Mode

__all__ = ["VimTextArea", "Mode"]

# VimTextAreaPlus is intentionally NOT imported here. It requires
# textual-textarea (the 'textarea-plus' extra), and importing it
# unconditionally would force that dependency on every install of this
# package, even for people only using plain VimTextArea. Import it
# explicitly if you need it:
#
#     from textual_vim_textarea.textarea_plus import VimTextAreaPlus
