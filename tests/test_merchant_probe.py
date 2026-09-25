"""「打开商户」手工测试窗口的占用规则。

这个窗口是给用户手动摸页面用的: 它开着的时候 running 一直为 True, 手动导出会被挡、
定时任务会被推迟, 所以不能无限期挂在那儿, 也要能被"中止"关掉。
"""
import threading

from core.main_gui import LiushuiApp


class _Plat:
    key = "fake"
    name = "假平台"
    export_url = "https://example.com/bill"


class _Page:
    def __init__(self, alive_polls):
        self.left = alive_polls

    def title(self):
        self.left -= 1
        if self.left < 0:
            raise RuntimeError("page has been closed")
        return "标题"


class _Browser:
    def __init__(self, alive_polls):
        self.page = _Page(alive_polls)
        self.sleeps = 0

    def enable_action_trace(self):
        pass

    def navigate(self, url):
        pass

    def sleep(self, seconds):
        self.sleeps += 1


class _ProbeApp:
    _open_merchant_browser = LiushuiApp._open_merchant_browser
    _aborted = LiushuiApp._aborted
    MERCHANT_PROBE_TIMEOUT_S = LiushuiApp.MERCHANT_PROBE_TIMEOUT_S

    def __init__(self, alive_polls=0, timeout_s=None):
        self._abort = threading.Event()
        self.browser = _Browser(alive_polls)
        self.logs = []
        if timeout_s is not None:
            self.MERCHANT_PROBE_TIMEOUT_S = timeout_s

    def _ensure_browser(self, plat, merchant, force_visible=False):
        pass

    def _append_log(self, line):
        self.logs.append(line)


def test_loop_ends_when_user_closes_the_window():
    app = _ProbeApp(alive_polls=2)
    app._open_merchant_browser(_Plat(), "旗舰店A")
    assert any("浏览器窗口已关闭" in line for line in app.logs)


def test_abort_closes_the_probe_window():
    """用户点「中止」后不该继续占着任务执行权。"""
    app = _ProbeApp(alive_polls=999)      # 页面一直"开着", 只有中止能结束它
    app._abort.set()
    app._open_merchant_browser(_Plat(), "旗舰店A")
    assert any("[中止]" in line for line in app.logs)
    assert any("浏览器窗口已关闭" in line for line in app.logs)


def test_probe_window_gives_up_after_timeout():
    """浏览器一直不关(例如忘了)时不能永久占用 running, 到点自动收。"""
    app = _ProbeApp(alive_polls=999, timeout_s=-1)
    app._open_merchant_browser(_Plat(), "旗舰店A")
    assert any("自动关闭以释放任务执行权" in line for line in app.logs)


def test_hint_mentions_the_abort_button():
    app = _ProbeApp(alive_polls=0)
    app._open_merchant_browser(_Plat(), "旗舰店A")
    assert any("中止本次任务" in line for line in app.logs)
