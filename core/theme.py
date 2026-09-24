"""界面配色。

单独一个模块是为了让 core/dialogs.py 这类"只画窗口"的代码能用同一套颜色, 而不必
反过来 import core.main_gui(那会成环)。数值与原来在 main_gui 里时完全一致。
"""

BG_MAIN = "#f5f6fa"
BG_PANEL = "#ffffff"
BG_BUTTON = "#4a90d9"
BG_BUTTON_HOVER = "#3a7bc8"
BG_SUCCESS = "#27ae60"
BG_WARN = "#e67e22"
BG_ERROR = "#e74c3c"
BG_LOG = "#1e1e2e"
FG_LOG = "#cdd6f4"
FG_MAIN = "#2d3436"
FG_MUTED = "#636e72"
