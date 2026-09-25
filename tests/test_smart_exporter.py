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


# ===== 日期框只填进一半的情形 =====

class _El:
    def __init__(self, placeholder, filled):
        self.placeholder = placeholder
        self.filled = filled
        self.value = None

    def get_attribute(self, name):
        return self.placeholder if name == "placeholder" else None

    def click(self, timeout=None):
        pass

    def fill(self, value):
        self.value = value
        self.filled.append((self.placeholder, value))


class _Inputs:
    def __init__(self, els):
        self.els = els

    def count(self):
        return len(self.els)

    def nth(self, i):
        return self.els[i]


class _DatePage(_Page):
    def __init__(self, placeholders):
        super().__init__()
        self.filled = []
        self.els = [_El(p, self.filled) for p in placeholders]

    def locator(self, sel):
        return _Inputs(self.els)


class _DateBrowser(_Browser):
    def __init__(self, placeholders):
        super().__init__()
        self.page = _DatePage(placeholders)
        self.download_attempts = 0
        self.shots = []

    def begin_wait_download(self):
        self.download_attempts += 1

    def screenshot(self, name):
        self.shots.append(name)
        return name


def _date_exporter(placeholders):
    logs = []
    browser = _DateBrowser(placeholders)
    ex = SmartExporter(browser, log_callback=logs.append)
    return ex, logs, browser


def test_both_date_boxes_filled_is_reported_as_filled():
    ex, logs, browser = _date_exporter(["开始日期", "结束日期"])
    ex.export("2026-09-01", "2026-09-02")
    assert any("已填入日期范围: 2026-09-01 ~ 2026-09-02" in l for l in logs)
    assert browser.download_attempts == 1          # 正常走下去


def test_half_filled_range_goes_to_manual_without_downloading():
    """只填进一个框 = 另一端仍是页面默认区间, 报 success 就是交出一份错区间的账单。"""
    ex, logs, browser = _date_exporter(["开始日期"])
    assert ex.export("2026-09-01", "2026-09-02") == "manual"
    assert any("只成功填入了 1 个输入框" in l for l in logs)
    assert not any("已填入日期范围" in l for l in logs)
    assert browser.download_attempts == 0          # 根本没去点导出
    assert browser.shots                            # 留了截图便于排查


def test_no_date_box_still_attempts_export():
    """一个都找不到时维持原有行为: 提示后继续尝试(很多页面用日期选择器组件)。"""
    ex, logs, browser = _date_exporter(["关键字搜索"])
    ex.export("2026-09-01", "2026-09-02")
    assert any("未找到日期输入框" in l for l in logs)
    assert browser.download_attempts == 1


# ===== 导出按钮的标签匹配 =====

class _Clickable:
    def __init__(self, text, sink):
        self.text = text
        self.sink = sink

    def click(self, timeout=None):
        self.sink.append(self.text)


class _MatchList:
    def __init__(self, items, sink):
        self.items = items
        self.sink = sink

    @property
    def first(self):
        return _Clickable(self.items[0], self.sink)

    def count(self):
        return len(self.items)


class _LabelPage(_Page):
    """按 Playwright 的语义模拟 get_by_text: exact 要求整段文字相等(允许首尾空白),
    非 exact 是子串匹配; 返回顺序即 DOM 顺序。"""

    def __init__(self, texts):
        super().__init__()
        self.texts = texts
        self.clicked = []

    def get_by_text(self, text, exact=False):
        if exact:
            hit = [t for t in self.texts if t.strip() == text]
        else:
            hit = [t for t in self.texts if text in t]
        return _MatchList(hit, self.clicked)


class _LabelBrowser(_Browser):
    def __init__(self, texts):
        super().__init__()
        self.page = _LabelPage(texts)

    def begin_wait_download(self):
        pass

    def wait_download(self, timeout=120):
        return None


def _click_export_for(texts):
    ex = SmartExporter(_LabelBrowser(texts), log_callback=lambda m: None)
    ok = ex._click_export()
    return ok, ex.page.clicked[0] if ex.page.clicked else None


def test_specific_export_label_wins_over_static_text():
    """旧实现: "导出" 排在列表前面且用子串匹配, 会先点中导航文字"导出记录"。"""
    ok, clicked = _click_export_for(["导出记录", "导出报表"])
    assert ok is True
    assert clicked == "导出报表"


def test_exact_label_still_matched_first():
    ok, clicked = _click_export_for(["导出"])
    assert ok is True and clicked == "导出"


def test_falls_back_to_contains_match():
    """按钮文字带图标/前后缀时精确匹配不到, 仍要能降级子串匹配。"""
    ok, clicked = _click_export_for(["前往导出中心查看"])
    assert ok is True and clicked == "前往导出中心查看"


def test_no_export_button_returns_false():
    ok, clicked = _click_export_for(["查询", "首页"])
    assert ok is False and clicked is None


# ===== 默认导出必须把日志接回 BrowserManager 通道 =====

def test_platform_default_export_wires_the_log_callback(monkeypatch):
    """回归: PlatformBase.export() 以前写的是 SmartExporter(browser) —— log_callback
    为空, 于是它的 _log() 全是空操作: 用户走默认导出时, 日志里既看不到认出了哪个日期框、
    也看不到为什么转 manual, 出问题只能猜。
    """
    import core.exporters as ex
    from core.platform_base import PlatformBase

    seen = {}

    class _Spy(ex.SmartExporter):
        def __init__(self, browser, log_callback=None):
            super().__init__(browser, log_callback=log_callback)
            seen["callback"] = log_callback

        def export(self, start_date, end_date, timeout=120):
            return "manual"

    monkeypatch.setattr(ex, "SmartExporter", _Spy)

    class _LoggingBrowser(_Browser):
        def __init__(self):
            super().__init__()
            self.lines = []

        def _log(self, msg, level="info"):
            self.lines.append(msg)

    browser = _LoggingBrowser()
    assert PlatformBase().export(browser, "2026-09-01", "2026-09-02") == "manual"
    callback = seen["callback"]
    # 绑 bound method 每次取都是新对象, 只能比相等(或比 __self__/__func__)
    assert callback == browser._log
    callback("[自动] 找到 2 个日期框")           # 签名要兼容(单参调用)
    assert browser.lines == ["[自动] 找到 2 个日期框"]
