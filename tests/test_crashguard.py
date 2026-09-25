"""启动期兜底: 崩溃必须留下能看的证据, 且兜底自己绝不能把启动带坏。

.vbs 用 pythonw + 隐藏窗口拉起 GUI, 没有控制台。没有 sys.excepthook 之前, 导入阶段
抛错(缺 tkinter、logs 建不出来)的现象就是"图标闪一下, 什么也没有"。这里锁住三件事:
崩溃一定落盘、弹窗起不来也不影响落盘、install() 自己绝不抛错。
"""
import os
import subprocess
import sys
import threading
from tkinter import messagebox

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from core import crashguard


@pytest.fixture()
def crash_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(crashguard, "CRASH_DIR", str(tmp_path))
    monkeypatch.setattr(crashguard, "ROOT_DIR", str(tmp_path / "root-fallback"))
    monkeypatch.setattr(crashguard, "_dialog_shown", False)
    return str(tmp_path)


def _no_dialog(monkeypatch):
    """把真弹窗换成记账的哑函数: 测试机上有显示器, 不该弹出窗口。"""
    shown = []
    monkeypatch.setattr(crashguard, "show_crash_dialog",
                        lambda text, path: (shown.append(path), True)[1])
    return shown


def _raise_and_catch():
    try:
        raise ValueError("chromium 没装")
    except ValueError:
        return sys.exc_info()


def test_crash_is_written_to_a_file(crash_dir, monkeypatch):
    notes = _no_dialog(monkeypatch)
    etype, exc, tb = _raise_and_catch()
    path = crashguard.report_uncaught(etype, exc, tb)
    assert path and os.path.basename(path).startswith("crash_")
    content = open(path, encoding="utf-8").read()
    assert "chromium 没装" in content and "Traceback" in content
    assert notes == [path]


def test_two_crashes_in_the_same_second_keep_both_files(crash_dir, monkeypatch):
    _no_dialog(monkeypatch)
    paths = {crashguard.write_crash_file("第一条"), crashguard.write_crash_file("第二条")}
    assert None not in paths and len(paths) == 2
    assert len([f for f in os.listdir(crash_dir) if f.startswith("crash_")]) == 2


def test_falls_back_to_project_root_when_logs_is_unwritable(crash_dir, monkeypatch):
    _no_dialog(monkeypatch)
    real_makedirs = os.makedirs

    def makedirs(path, *a, **k):
        if os.path.abspath(path) == os.path.abspath(crash_dir):
            raise PermissionError("logs 建不出来")
        return real_makedirs(path, *a, **k)

    monkeypatch.setattr(crashguard.os, "makedirs", makedirs)
    path = crashguard.write_crash_file("x")
    assert path and os.path.dirname(path) == os.path.join(crash_dir, "root-fallback")


def test_unwritable_everywhere_returns_none_instead_of_raising(monkeypatch):
    def makedirs(*a, **k):
        raise PermissionError("整盘只读")
    monkeypatch.setattr(crashguard.os, "makedirs", makedirs)
    assert crashguard.write_crash_file("x") is None


def test_dialog_failure_still_leaves_the_crash_file(crash_dir, monkeypatch):
    def _boom(text, path):
        raise RuntimeError("没有显示器")
    monkeypatch.setattr(crashguard, "show_crash_dialog", _boom)
    path = crashguard.report_uncaught(ValueError, ValueError("缺 tkinter"), None)
    assert path and "缺 tkinter" in open(path, encoding="utf-8").read()


def test_thread_hook_writes_files_but_never_pops_a_window(crash_dir, monkeypatch):
    shown = []
    monkeypatch.setattr(crashguard, "show_crash_dialog",
                        lambda text, path: shown.append(path))
    etype, exc, tb = _raise_and_catch()

    class _Args:
        exc_type, exc_value, exc_traceback = etype, exc, tb

    crashguard.thread_excepthook(_Args())
    assert shown == []                      # 非主线程拉 Tk 可能把界面吊住
    files = [f for f in os.listdir(crash_dir) if f.startswith("crash_")]
    assert len(files) == 1 and "chromium 没装" in open(
        os.path.join(crash_dir, files[0]), encoding="utf-8").read()


def test_only_one_dialog_per_process(crash_dir, monkeypatch):
    calls = []

    class _Root:
        def withdraw(self):
            pass

        def destroy(self):
            pass

    import tkinter
    monkeypatch.setattr(tkinter, "Tk", lambda: _Root())
    monkeypatch.setattr(messagebox, "showerror", lambda title, msg: calls.append(msg))
    assert crashguard.show_crash_dialog("x", "logs/crash_1.txt") is True
    assert crashguard.show_crash_dialog("x", "logs/crash_2.txt") is False
    assert len(calls) == 1 and "crash_1.txt" in calls[0]


def test_dialog_shows_the_text_when_nothing_is_writable(crash_dir, monkeypatch):
    calls = []

    class _Root:
        def withdraw(self):
            pass

        def destroy(self):
            pass

    import tkinter
    monkeypatch.setattr(tkinter, "Tk", lambda: _Root())
    monkeypatch.setattr(messagebox, "showerror", lambda title, msg: calls.append(msg))
    monkeypatch.setattr(crashguard, "write_crash_file", lambda text: None)
    assert crashguard.show_crash_dialog("详情写不出去的崩溃", None) is True
    assert "请把下面这段截图" in calls[0] and "详情写不出去的崩溃" in calls[0]


def test_install_swaps_both_hooks(monkeypatch):
    old_main, old_thread = sys.excepthook, threading.excepthook
    try:
        monkeypatch.setattr(crashguard, "report_uncaught", lambda *a: None)
        assert crashguard.install() is True
        assert sys.excepthook is crashguard.report_uncaught
        assert threading.excepthook is crashguard.thread_excepthook
    finally:
        sys.excepthook, threading.excepthook = old_main, old_thread


def test_install_reports_failure_instead_of_raising(monkeypatch):
    old_main = sys.excepthook
    try:
        monkeypatch.setitem(sys.modules, "threading", None)   # import threading 直接炸
        assert crashguard.install() is False
    finally:
        sys.excepthook = old_main


def test_real_interpreter_crash_produces_a_file(crash_dir):
    """真起一个解释器: 钩子得是装上就生效的, 而不是只被单元测试直接调过。"""
    child = ";".join([
        "import sys, os",
        f"sys.path.insert(0, {REPO_ROOT!r})",
        "from core import crashguard",
        f"crashguard.CRASH_DIR = {crash_dir!r}",
        f"crashguard.ROOT_DIR = {os.path.join(crash_dir, 'fallback')!r}",
        "crashguard.show_crash_dialog = lambda text, path: True",
        "crashguard.install()",
        "raise RuntimeError('启动期崩溃')",
    ])
    r = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True)
    assert r.returncode != 0
    files = [f for f in os.listdir(crash_dir) if f.startswith("crash_")]
    assert len(files) == 1
    assert "启动期崩溃" in open(os.path.join(crash_dir, files[0]), encoding="utf-8").read()
