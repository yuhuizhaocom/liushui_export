"""崩溃兜底自己必须是好的: 弹窗说"已记录到 logs", 日志就得真的落盘。

回归的是 main() 的 except 分支写死 `from logger import log` —— 仓库根下没有
logger.py, 这句必然 ModuleNotFoundError, 又被同一层的 except 吞掉, 于是程序出
错时 logs 里什么都没有, 用户却被告知"详细信息已记录到 logs 文件夹", 截图发过来
也查不到原因。
"""
import re

import pytest

from core import main_gui


@pytest.fixture()
def popup(monkeypatch):
    """拦掉真弹窗和真 Tk, 只留下 messagebox 收到的文案。"""
    shown = {}

    class _Root:
        def withdraw(self):
            pass

        def destroy(self):
            pass

    monkeypatch.setattr(main_gui.tk, "Tk", lambda: _Root())
    monkeypatch.setattr(main_gui.messagebox, "showerror",
                        lambda title, msg: shown.update(title=title, msg=msg))
    return shown


def test_crash_detail_reaches_the_log(monkeypatch, popup):
    written = []
    monkeypatch.setattr(main_gui, "log",
                        lambda msg, level="info", callback=None: written.append((level, msg)))
    assert main_gui.report_crash("找不到 chromium", "Traceback: xxx") is True
    assert written == [("error", "程序运行异常: 找不到 chromium\nTraceback: xxx")]
    assert "详细信息已记录到 logs 文件夹" in popup["msg"]
    assert "找不到 chromium" in popup["msg"]


def test_popup_stops_claiming_when_the_logger_is_broken(monkeypatch, popup):
    def _boom(*a, **kw):
        raise OSError("logs 目录写不了")
    monkeypatch.setattr(main_gui, "log", _boom)
    assert main_gui.report_crash("盘满了") is False
    assert "日志没能写成功" in popup["msg"]
    assert "已记录到 logs" not in popup["msg"]


def test_crash_report_never_raises_when_the_popup_is_gone(monkeypatch):
    monkeypatch.setattr(main_gui, "log", lambda msg, level="info": None)

    def _no_tk():
        raise RuntimeError("没有显示器")
    monkeypatch.setattr(main_gui.tk, "Tk", _no_tk)
    assert main_gui.report_crash("任意异常") is True


def test_old_broken_import_path_is_gone():
    # 只查可执行语句: 说明这段历史的注释里本来就写着这句
    with open(main_gui.__file__, encoding="utf-8") as f:
        src = f.read()
    assert not re.search(r"^\s*from logger import", src, re.M), \
        "根目录下没有 logger.py, 这句必然抛错"
