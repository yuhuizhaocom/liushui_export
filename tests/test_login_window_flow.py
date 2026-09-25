"""首次登录改成"关掉浏览器窗口就算登好"。

旧流程是"浏览器打开登录页 → 界面弹模态框 → 用户点确定"。业务用户常常还没登完(短信
还没到、二维码还没扫)就先点了确定, 于是程序拿到的是一份没登录的 profile, 之后每次
导出都失败。现在唯一的信号就是**用户自己关掉浏览器窗口**。

这里钉住四件事: ① 等待期间绝不调用 `check_login`(它会把页面导航走, 用户正扫码就被
拽走); ② cookie 一有写入就顺手导出登录态(Chromium 退出时不保留 session cookie, 等
关完窗口再存已经来不及); ③ 没检测到任何登录写入时不许亮绿灯; ④ 中止/超时都能退出
等待, 不会把 `running` 永久占住。
"""
import threading

import pytest

from core.browser import BrowserManager
from core.main_gui import LiushuiApp


class _Ctx:
    """假 Playwright context: 只需要 pages / cookies()。"""

    def __init__(self, pages=(object(),), cookies=None, raise_on_pages=False,
                 raise_on_cookies=False):
        self._pages = list(pages)
        self._cookies = cookies or []
        self.raise_on_pages = raise_on_pages
        self.raise_on_cookies = raise_on_cookies

    @property
    def pages(self):
        if self.raise_on_pages:
            raise Exception("Target closed")
        return self._pages

    def cookies(self):
        if self.raise_on_cookies:
            raise Exception("Target closed")
        return self._cookies


def _mgr(ctx):
    m = BrowserManager(headless=True)          # 不 start(), 不碰浏览器
    m.context = ctx
    return m


def test_window_closed_signals_are_all_honoured():
    assert _mgr(None).window_closed() is True                       # 没 context
    assert _mgr(_Ctx(pages=[])).window_closed() is True             # 页面全关
    assert _mgr(_Ctx(raise_on_pages=True)).window_closed() is True  # 通讯断了
    alive = _mgr(_Ctx())
    assert alive.window_closed() is False
    alive._window_closed.set()                                      # close 事件
    assert alive.window_closed() is True


def test_login_signature_changes_only_when_cookies_change():
    m = _mgr(_Ctx(cookies=[{"name": "a", "value": "1"}]))
    first = m.login_signature()
    assert first == m.login_signature()
    m.context._cookies.append({"name": "b", "value": "2"})
    assert m.login_signature() != first
    same_count = _mgr(_Ctx(cookies=[{"name": "a", "value": "9"}]))
    assert same_count.login_signature() != first       # 条数没变但值变了也算
    assert _mgr(None).login_signature() is None
    assert _mgr(_Ctx(raise_on_cookies=True)).login_signature() is None


class _Browser:
    """脚本化假浏览器, 并记录被调用过什么。"""

    def __init__(self, close_on_tick=None, sigs=None, save_ok=True):
        self.calls = []
        self.close_on_tick = close_on_tick      # 第几轮起算"窗口已关"
        self._sigs = list(sigs or [])
        self._ticks = 0
        self.saved_quiet = []
        self._save_ok = save_ok

    def window_closed(self):
        self._ticks += 1
        self.calls.append("window_closed")
        return self.close_on_tick is not None and self._ticks >= self.close_on_tick

    def login_signature(self):
        self.calls.append("login_signature")
        return self._sigs.pop(0) if self._sigs else (3, 111)

    def save_login_state(self, quiet=False):
        self.calls.append("save_login_state")
        self.saved_quiet.append(quiet)
        return self._save_ok

    def check_login(self, *a, **k):            # 真浏览器没有这个方法, 被调到这里就是不对
        self.calls.append("check_login")
        raise AssertionError("等待期间不能碰页面(会把用户拽离登录页)")


class _App:
    _wait_login_window_closed = LiushuiApp._wait_login_window_closed
    _aborted = LiushuiApp._aborted
    LOGIN_WAIT_TIMEOUT_S = 1.0
    LOGIN_POLL_S = 0.01

    def __init__(self):
        self.logs = []
        self._abort = threading.Event()

    def _append_log(self, line):
        self.logs.append(line)


def test_cookie_write_is_saved_immediately_and_quietly():
    b = _Browser(close_on_tick=4, sigs=[(3, 111), (3, 111), (4, 222), (4, 222)])
    app = _App()
    outcome, saved = app._wait_login_window_closed(b)
    assert (outcome, saved) == ("closed", True)
    assert b.saved_quiet == [True], "轮询期间的保存不能刷日志"
    assert any("检测到登录写入" in line for line in app.logs)
    assert "check_login" not in b.calls


def test_first_reading_is_a_baseline_not_a_login():
    """老 profile 本来就带 cookie, 第一轮的指纹不该被当成"刚登录成功"。"""
    b = _Browser(close_on_tick=2, sigs=[(3, 111), (3, 111)])
    outcome, saved = _App()._wait_login_window_closed(b)
    assert (outcome, saved) == ("closed", False)
    assert b.saved_quiet == []


def test_no_cookie_change_means_no_saved_flag(monkeypatch):
    b = _Browser(close_on_tick=3, sigs=[(3, 111), (3, 111), (3, 111)])
    outcome, saved = _App()._wait_login_window_closed(b)
    assert (outcome, saved) == ("closed", False), "没登录写入就不能说已保存"


def test_abort_leaves_the_wait_immediately():
    app = _App()
    app._abort.set()
    b = _Browser()
    assert app._wait_login_window_closed(b) == ("aborted", False)
    assert b.calls == [], "中止时一轮都不用跑"


def test_timeout_does_not_hang_the_task():
    b = _Browser(close_on_tick=None)         # 窗口一直不关
    app = _App()
    app.LOGIN_WAIT_TIMEOUT_S = 0.05
    outcome, _saved = app._wait_login_window_closed(b)
    assert outcome == "timeout"


def test_failed_save_is_not_reported_as_saved():
    b = _Browser(close_on_tick=4, sigs=[(3, 1), (4, 2), (4, 2)], save_ok=False)
    outcome, saved = _App()._wait_login_window_closed(b)
    assert (outcome, saved) == ("closed", False)


def test_production_timeouts_are_generous():
    """等人扫码/企业认证要时间: 30 分钟上限、2 秒一轮。"""
    assert LiushuiApp.LOGIN_WAIT_TIMEOUT_S == 30 * 60
    assert 0.5 <= LiushuiApp.LOGIN_POLL_S <= 5
