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

    def __init__(self, download_path="快手货款账单.csv"):
        self.calls = []
        self.download_path = download_path

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "wait_download":
                return self.download_path
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
