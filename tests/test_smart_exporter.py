"""SmartExporter(未写 export() 的平台走的默认导出器)的安全边界。"""
import pytest

from core.exporters import SmartExporter


class _Dialog:
    def __init__(self, message):
        self.message = message
        self.action = None

    def accept(self):
        self.action = "accept"

    def dismiss(self):
        self.action = "dismiss"


class _Page:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def locator(self, sel):
        raise RuntimeError("本页无 input")

    def get_by_text(self, text, exact=False):
        raise RuntimeError("不可用")


class _Browser:
    def __init__(self):
        self.page = _Page()

    def begin_wait_download(self):
        pass

    def wait_download(self, timeout=120):
        return None

    def screenshot(self, name):
        return name


def _exporter():
    logs = []
    ex = SmartExporter(_Browser(), log_callback=logs.append)
    return ex, logs


def test_only_export_related_dialogs_are_auto_accepted():
    """回归: 以前是所有 JS 弹窗无脑 accept, 后台弹"确定要作废这张发票吗"也会被点掉。"""
    ex, logs = _exporter()
    ok = _Dialog("确认导出 2026-09-01 至 2026-09-02 的账单吗?")
    bad = _Dialog("确定要作废发票 NO.202609001 吗? 该操作不可撤销")
    ex._handle_dialog(ok)
    ex._handle_dialog(bad)
    assert ok.action == "accept"
    assert bad.action == "dismiss"
    assert any("已取消" in line for line in logs)


def test_dialog_handler_registration_uses_the_safe_handler():
    ex, _logs = _exporter()
    ex._setup_dialog_handler()
    handler = ex.page.handlers["dialog"][0]
    d = _Dialog("要删除这条记录吗?")
    handler(d)
    assert d.action == "dismiss"


def test_broken_dialog_does_not_break_export():
    """弹窗层出错不能把整次导出带崩。"""
    ex, logs = _exporter()

    class _Nasty(_Dialog):
        def accept(self):
            raise RuntimeError("dialog already handled")

    ex._handle_dialog(_Nasty("请导出账单"))          # 不应抛出
    assert any("弹窗处理失败" in line for line in logs)
