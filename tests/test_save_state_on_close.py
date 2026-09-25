"""关浏览器前先把登录态存下来, 但不拿更空的一份盖掉上一次的。

Chromium 退出时不保留 session cookie(微信支付靠它), 而 `close()` 的调用点比首次登录
多得多: 每次导出收尾、保活巡检、商户「打开」的手工测试窗口、程序退出。以前只有首次
登录会 `save_login_state`, 于是这些路径上被平台刷新/续期的 cookie 一关浏览器就没了。

风险也要一起挡: 有平台的页面会清 cookie(登出跳转、风控), 无脑保存就会把上次存好的
登录态换成一份空的 —— 所以"当前比已存的少"时不覆盖。
"""
import json
import os

import pytest

from core.browser import BrowserManager


class _Ctx:
    def __init__(self, cookies=None, state=None, raise_on_cookies=False):
        self._cookies = cookies if cookies is not None else []
        self._state = state
        self.raise_on_cookies = raise_on_cookies
        self.closed = False

    def cookies(self):
        if self.raise_on_cookies:
            raise Exception("Target closed")
        return self._cookies

    def storage_state(self):
        if self._state is not None:
            return self._state
        return {"cookies": self._cookies, "origins": []}

    def close(self):
        self.closed = True


class _Playwright:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


@pytest.fixture()
def mgr(tmp_path):
    m = BrowserManager(headless=True)          # 不 start(), 不碰浏览器
    m._profile_dir = str(tmp_path)
    m.logs = []
    m._log = lambda msg, level="info": m.logs.append((level, msg))
    return m


def _state_file(mgr):
    return os.path.join(mgr._profile_dir, BrowserManager.LOGIN_STATE_FILE)


def _write_state(mgr, cookies):
    with open(_state_file(mgr), "w", encoding="utf-8") as f:
        json.dump({"cookies": cookies, "origins": []}, f)


def test_close_saves_before_the_context_goes_away(mgr):
    session = {"name": "SESSION_KEY", "value": "abc", "expires": -1}
    mgr.context = _Ctx(cookies=[session])
    mgr.playwright = _Playwright()
    order = []
    mgr.context.close = lambda: order.append("context.close")
    mgr.save_login_state = lambda quiet=False: (order.append("save"), True)[1]
    mgr.close()
    assert order == ["save", "context.close"], "必须趁 context 还活着先存"
    assert mgr.playwright.stopped is True


def test_state_file_gets_the_session_cookie(mgr):
    session = {"name": "SESSION_KEY", "value": "abc", "expires": -1}
    mgr.context = _Ctx(cookies=[session])
    mgr.playwright = _Playwright()
    mgr.close()
    saved = json.load(open(_state_file(mgr), encoding="utf-8"))
    assert saved["cookies"] == [session], "session cookie(expires=-1)要完整落盘"


def test_emptier_state_never_overwrites_a_fuller_one(mgr):
    old = [{"name": "k%d" % i, "value": "v", "expires": 1} for i in range(5)]
    _write_state(mgr, old)
    mgr.context = _Ctx(cookies=[{"name": "only", "value": "1", "expires": 1}])
    mgr.playwright = _Playwright()
    assert mgr.save_login_state_on_close() is False
    assert len(json.load(open(_state_file(mgr), encoding="utf-8"))["cookies"]) == 5
    assert any("不覆盖" in msg for _, msg in mgr.logs)
    mgr.context.close()          # 不覆盖也得照常关掉


def test_more_or_equal_cookies_do_overwrite(mgr):
    _write_state(mgr, [{"name": "a", "value": "1", "expires": 1}])
    mgr.context = _Ctx(cookies=[{"name": "a", "value": "2", "expires": 1},
                                {"name": "b", "value": "3", "expires": 1}])
    assert mgr.save_login_state_on_close() is True
    assert len(json.load(open(_state_file(mgr), encoding="utf-8"))["cookies"]) == 2


def test_equal_count_still_saves_so_refreshed_values_are_kept(mgr):
    """条数一样但值变了(平台续期/换 token)也要存, 否则存的是过期那一版。"""
    _write_state(mgr, [{"name": "token", "value": "old", "expires": 1}])
    mgr.context = _Ctx(cookies=[{"name": "token", "value": "new", "expires": 1}])
    assert mgr.save_login_state_on_close() is True
    saved = json.load(open(_state_file(mgr), encoding="utf-8"))["cookies"]
    assert saved[0]["value"] == "new"


def test_no_context_is_a_quiet_noop(mgr):
    mgr.context = None
    mgr.playwright = _Playwright()
    mgr.close()                    # 不抛错
    assert mgr.playwright.stopped is True
    assert not os.path.exists(_state_file(mgr))


def test_user_closed_window_does_not_make_close_blow_up(mgr):
    """用户自己把窗口关了: 任何调用都抛 Target closed, 关闭流程照样走完。"""
    mgr.context = _Ctx(raise_on_cookies=True)
    mgr.playwright = _Playwright()
    mgr.close()
    assert mgr.playwright.stopped is True
    assert not os.path.exists(_state_file(mgr))
    assert mgr.logs == [], "存不上是常态, 不该为此刷日志"
