"""查看类对话框的构造冒烟测试。

这两个窗口(历史日志、稳定性看板)以前长在 LiushuiApp 里, 任何测试都碰不到; 搬到
core/dialogs.py 后至少能确认"点开不会炸"——配色常量的名字打错、logger 少了导出
这类问题都会在这里暴露。环境起不了 Tk 时跳过, 不算失败。
"""
import tkinter as tk
from tkinter import ttk

import pytest

from core.dialogs import show_log_history, show_stats_dashboard


@pytest.fixture()
def root():
    try:
        r = tk.Tk()
    except Exception as e:                                   # 无显示环境
        pytest.skip(f"无法创建 Tk 根窗口: {e}")
    r.withdraw()                                             # 不在桌面上闪窗口
    try:
        yield r
    finally:
        r.destroy()


class _App:
    """show_stats_dashboard 只用到 root 与 _set_status。"""

    def __init__(self, root):
        self.root = root
        self.status = []

    def _set_status(self, text):
        self.status.append(text)


def _tops(root):
    return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]


def test_log_history_window_constructs(root):
    show_log_history(root)
    wins = _tops(root)
    assert len(wins) == 1
    assert wins[0].title() == "历史日志"


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def test_stats_dashboard_constructs_with_tables(root):
    app = _App(root)
    show_stats_dashboard(app)
    win = _tops(root)[0]
    assert win.title() == "稳定性看板"
    # 汇总表 + 明细表两个 Treeview 都建起来了(它们在各自的面板 Frame 里)
    assert len([w for w in _descendants(win) if isinstance(w, ttk.Treeview)]) == 2


def test_stats_dashboard_has_copy_and_refresh_buttons(root):
    show_stats_dashboard(_App(root))
    win = _tops(root)[0]
    labels = {b.cget("text") for b in _descendants(win) if isinstance(b, tk.Button)}
    assert {"刷新", "复制汇总"} <= labels
