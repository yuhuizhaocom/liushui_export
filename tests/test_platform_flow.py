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


# ===== 视频号 / 微信支付自己实现日期设置, 停手这条也必须生效 =====

class _Chain:
    """页面元素哑对象: 随便怎么链式访问都返回自己; `count()` 决定脚本走哪条分支
    (找不到输入框 / 拿到足够的框)。与 tests/test_platform_sequences.py 的 `_Any` 同构。
    """

    def __init__(self, count=2):
        self._count = count

    def count(self):
        return self._count

    def __call__(self, *args, **kwargs):
        return self

    def __bool__(self):
        return True

    def __getattr__(self, name):
        return self


class _Guard:
    """记录 browser 调用; 页面元素数量与 fill_* 的返回值都可注入。"""

    def __init__(self, elements, fill_ok=True):
        self.page = elements
        self.fill_ok = fill_ok
        self.calls = []
        self.logs = []

    def _log(self, msg, level="info"):
        self.logs.append(msg)

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a, k))
            if name.startswith("fill_"):
                return self.fill_ok
            if name == "wait_download":
                return "资金流水.csv"
            # 其余一律"成功"返回: 脚本里 `if not browser.click_text(...)` 这类分支
            # 不该被哑对象的 None 骗进回退路径(与序列快照那套 harness 同一口径)
            return _Chain()
        return rec

    def clicked(self, text):
        return any(c[0] == "click_text" and c[1] and c[1][0] == text for c in self.calls)


def test_shipinhao_stops_when_time_range_is_not_set():
    """回归: `_set_time` 的返回值以前没人看 —— 找不到"动账开始/结束时间"输入框也照样
    点查询、点全部导出, 得到的是页面默认区间的账单, 文件名却写着本次请求的区间。"""
    from platforms.shipinhao.export import ShipinhaoExporter

    browser = _Guard(_Chain(0))
    assert ShipinhaoExporter().export(browser, "2026-09-01", "2026-09-02") == "manual"
    assert any("动账时间未能设置" in line for line in browser.logs), browser.logs
    assert not browser.clicked("全部导出"), "日期没设成就不该点导出"
    assert ("snapshot", ("日期未填入",), {}) in browser.calls


def test_shipinhao_still_exports_when_the_range_is_set():
    from platforms.shipinhao.export import ShipinhaoExporter

    browser = _Guard(_Chain(2))
    assert ShipinhaoExporter().export(browser, "2026-09-01", "2026-09-02") == "success"
    assert browser.clicked("全部导出")
    assert not any("动账时间未能设置" in line for line in browser.logs)


def test_wechatpay_stops_when_neither_date_method_worked():
    """el-range-input 不够两个、placeholder 又填不进去 = 日期根本没设上。"""
    from platforms.wechatpay.export import WechatpayExporter

    browser = _Guard(_Chain(1), fill_ok=False)
    assert WechatpayExporter().export(browser, "2026-09-01", "2026-09-02") == "manual"
    assert any("起止日期两种填法都没成功" in line for line in browser.logs), browser.logs
    assert not browser.clicked("查询"), "日期没设成就不该点查询"


def test_wechatpay_keyboard_path_counts_as_set():
    from platforms.wechatpay.export import WechatpayExporter

    browser = _Guard(_Chain(2))
    assert WechatpayExporter().export(browser, "2026-09-01", "2026-09-02") == "success"
    assert browser.clicked("查询")
    assert not any("两种填法都没成功" in line for line in browser.logs)
