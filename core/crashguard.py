"""启动期崩溃兜底: 让"双击没反应"变成看得见的错误。

工具是用 .vbs 以 pythonw + 隐藏窗口拉起来的, 没有控制台。此前全仓没有
sys.excepthook, 所以导入阶段一旦抛错(缺 tkinter、logs 目录建不出来、core.* 导入
失败), 现象就是图标闪一下什么也没有; 子线程里未捕获的异常同样只往 stderr 打印,
而 stderr 根本不存在。

这个模块必须在 main_gui 其余导入之前装好钩子, 因此它自己只依赖标准库, 弹窗用的
tkinter 到调用时才惰性导入 —— 连 tkinter 都导不出时, 至少崩溃文件还在。
"""
import os
import sys
import traceback
from datetime import datetime

# 程序根(本文件在 core/ 下)。崩溃兜底必须能在 `core.config` 自己导入失败时也工作,
# 所以这里不 import config, 只用标准库自己找落点。
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POINTER_FILE = os.path.join(ROOT_DIR, "workspace.json")     # 工作空间书签(与 config 同一份)
CRASH_DIR = os.path.join(ROOT_DIR, "logs")                  # 兜底落点(测试会 monkeypatch)


def _crash_dirs():
    """崩溃文件的候选落点: 工作空间/logs → CRASH_DIR → 临时目录。"""
    dirs = []
    try:
        import json
        with open(POINTER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ws = str(data.get("data_root") or "").strip() if isinstance(data, dict) else ""
        if ws:
            dirs.append(os.path.join(ws, "logs"))
    except Exception:
        pass                                   # 没有书签就是没换过工作空间, 正常
    dirs.append(CRASH_DIR)
    try:
        import tempfile
        dirs.append(os.path.join(tempfile.gettempdir(), "liushui_export"))
    except Exception:
        pass
    return dirs

_dialog_shown = False        # 一个进程只弹一次, 免得子线程连环崩溃刷屏


def write_crash_file(text):
    """把崩溃信息写成 logs/crash_*.txt; 返回路径, 一个地方都写不了则返回 None。

    文件名精确到微秒: 同一秒内崩两次(用户连点两下)不该把第一条覆盖掉。
    """
    name = "crash_%s.txt" % datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    for target in _crash_dirs():
        try:
            os.makedirs(target, exist_ok=True)
            path = os.path.join(target, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            return path
        except Exception:
            continue
    return None


def show_crash_dialog(text, path):
    """告诉业务用户"程序没起来"以及去哪里找详情; 窗口都起不来时只能作罢。"""
    global _dialog_shown
    if _dialog_shown:
        return False
    _dialog_shown = True
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        body = (f"详细信息已保存到:\n{path}" if path else
                "详情没能写进文件, 请把下面这段截图发给维护人员:\n\n" + text[-600:])
        messagebox.showerror("流水导出工具没能启动",
                             "程序在启动过程中出错, 已退出。\n\n" + body)
        root.destroy()
        return True
    except Exception:
        return False


def report_uncaught(exc_type, exc, tb, dialog=True):
    """未捕获异常的兜底入口(钩子的本体): 落盘, 并按需弹窗。

    dialog=False 用于子线程 —— 在非主线程里拉 Tk 窗口可能把界面吊住, 对业务用户来说
    比"看不见崩溃"更糟。
    """
    try:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
    except Exception:
        text = f"{exc_type}: {exc}"
    path = write_crash_file(text)
    if dialog:
        try:
            show_crash_dialog(text, path)
        except Exception:
            pass      # 弹窗自身坏了不能盖住真实崩溃, 文件已经落了
    return path


def thread_excepthook(args):
    """子线程的未捕获异常: 导出跑在 worker 线程里, 崩了同样没人看得见 —— 只落文件。"""
    report_uncaught(args.exc_type, args.exc_value, args.exc_traceback, dialog=False)


def install():
    """装上进程级与线程级的兜底; 失败也绝不影响正常启动。"""
    try:
        sys.excepthook = report_uncaught
        import threading
        threading.excepthook = thread_excepthook
    except Exception:
        return False
    return True
