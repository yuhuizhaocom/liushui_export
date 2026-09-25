"""通用导出骨架: 用"浏览器调用序列"证明改用骨架没有改变任何一次操作。

平台脚本最终跑在真实后台页面上, 离线没法验证点击是否生效; 但调了哪些方法、
什么顺序、传了什么参数是完全可以钉死的 —— 序列一变就说明行为变了。
GOLDEN 取自迁移前 platforms/kuaishou/export.py 里逐行写死的调用。
"""
from core.platform_base import PlatformBase
from platforms.kuaishou.export import KuaishouExporter

EXPORT_URL = "https://s.kwaixiaodian.com/zone/fund/payment/bill"

GOLDEN_KUAISHOU = [
    ("navigate", (EXPORT_URL,), {}),
    ("sleep", (3,), {}),
    ("close_popup", (), {}),
    ("fill_placeholder", ("开始日期", "2026-09-01"), {}),
    ("fill_placeholder", ("结束日期", "2026-09-02"), {}),
    ("sleep", (1,), {}),
    ("click_text", ("查询",), {}),
    ("sleep", (3,), {}),
    ("click_text", ("导出",), {}),
    ("sleep", (5,), {}),
    ("click_text", ("查看导出记录",), {}),
    ("sleep", (3,), {}),
    ("begin_wait_download", (), {}),
    ("click_text", ("下载",), {}),
    ("sleep", (3,), {}),
    ("wait_download", (), {"timeout": 60}),
]


class Recorder:
    """记录每次 browser 调用; wait_download 的返回值可注入。"""

    def __init__(self, download_path="快手货款账单.csv", fill_ok=True):
        self.calls = []
        self.download_path = download_path
        self.fill_ok = fill_ok

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "wait_download":
                return self.download_path
            if name.startswith("fill_"):
                return self.fill_ok      # 真实 BrowserManager: 填成功 True / 找不到框 False
        return _record


def _run(plat=None, download_path="快手货款账单.csv"):
    browser = Recorder(download_path)
    result = (plat or KuaishouExporter()).export(browser, "2026-09-01", "2026-09-02")
    return browser, result


def test_kuaishou_call_sequence_is_unchanged_by_skeleton():
    browser, _result = _run()
    assert browser.calls == GOLDEN_KUAISHOU


def test_kuaishou_returns_success_when_file_lands():
    _browser, result = _run()
    assert result == "success"


def test_kuaishou_returns_manual_when_download_times_out():
    _browser, result = _run(download_path=None)
    assert result == "manual"


def test_base_export_default_still_uses_smart_exporter(monkeypatch):
    """没写 export 的新平台仍走 SmartExporter, 骨架是显式选用的, 不改默认行为。"""
    import core.exporters as exporters

    calls = []

    class _FakeExporter:
        def __init__(self, browser, log_callback=None):
            self.browser = browser

        def export(self, start_date, end_date):
            calls.append((start_date, end_date))
            return "manual"

    monkeypatch.setattr(exporters, "SmartExporter", _FakeExporter)

    class _NoFlow(PlatformBase):
        key = "noflow"
        name = "未写脚本"

    assert _NoFlow().export(Recorder(), "2026-09-01", "2026-09-02") == "manual"
    assert calls == [("2026-09-01", "2026-09-02")]


class _FillBrowser:
    """只模拟 fill_placeholder 的成功/失败, 用于验证 set_date_range 的返回契约。"""

    def __init__(self, missing=()):
        self.missing = tuple(missing)
        self.calls = []

    def fill_placeholder(self, placeholder, value):
        self.calls.append((placeholder, value))
        return placeholder not in self.missing

    def sleep(self, seconds):
        pass

    def _log(self, msg, level="info"):
        pass


def test_set_date_range_reports_which_boxes_missed():
    p = KuaishouExporter()
    assert p.set_date_range(_FillBrowser(), "2026-09-01", "2026-09-02") is True
    # 只填进一个框 = 另一端仍是页面默认区间, 不能算成功
    only_start = _FillBrowser(missing=("结束日期",))
    assert p.set_date_range(only_start, "2026-09-01", "2026-09-02") is False
    assert [c[0] for c in only_start.calls] == ["开始日期", "结束日期"]  # 两个都尝试过


def test_flow_stops_before_clicking_export_when_dates_missing():
    """回归: 日期没填进去照样往下点, 会得到页面默认区间的账单,
    而归档文件名用的却是请求区间 —— 从产物上完全看不出错。"""
    browser = Recorder(fill_ok=False)
    result = KuaishouExporter().export(browser, "2026-09-01", "2026-09-02")
    names = [c[0] for c in browser.calls]
    assert result == "manual"
    assert names.count("fill_placeholder") == 2       # 两个框都试过
    assert "click_text" not in names                  # 没去点查询/导出/下载
    assert "begin_wait_download" not in names
