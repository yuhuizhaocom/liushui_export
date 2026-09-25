"""关窗口后要真核实一次登录状态; 没登上只提示, 不拦人也不中断。

光看"cookie 有没有变化"不足以说明登录成功(埋点也会种 cookie), 而"能不能进后台"只有
`check_login` 说得清。用户已经把这个窗口关了, 此时导航不会打扰任何人。
注意顺序: 必须先释放登录时用的那个 profile, 才能用同一 profile 后台重开浏览器 ——
Chromium 的 profile 同时只能被一个进程占用。
"""
import threading

import pytest

import core.browser as cb
import core.main_gui as mg
from core.main_gui import LiushuiApp


class _Plat:
    key = "wechatpay"
    name = "微信支付"
    guide = "交易 → 账单管理"

    def __init__(self, result=True, raises=None):
        self.result = result
        self.raises = raises
        self.checked = 0

    def check_login(self, browser):
        self.checked += 1
        if self.raises:
            raise self.raises
        return self.result


class _Browser:
    def __init__(self, label):
        self.label = label
        self.closed = 0

    def close(self):
        self.closed += 1


class _App:
    _verify_login_after_close = LiushuiApp._verify_login_after_close
    _close_browser = LiushuiApp._close_browser

    def __init__(self, browser=None):
        self.logs = []
        self.statuses = []
        self.status_text = []
        self.ui_calls = []
        self.browser = browser


    def _append_log(self, line):
        self.logs.append(line)

    def _set_status(self, text):
        self.status_text.append(text)

    def set_platform_status(self, key, status):
        self.statuses.append((key, status))

    def _ui(self, fn):
        self.ui_calls.append(fn)
        fn()          # 测试里就地执行, 方便看到投递出去的内容

    def _ensure_browser(self, plat=None, merchant="", force_visible=False,
                        force_headless=False):
        self.ensure_kwargs = {"force_visible": force_visible,
                              "force_headless": force_headless}
        self.browser = _Browser("核实用")


def _popup(monkeypatch):
    shown = []
    monkeypatch.setattr(mg.messagebox, "showwarning",
                        lambda title, msg: shown.append((title, msg)))
    return shown


def test_logged_in_turns_the_light_green_without_a_popup(monkeypatch):
    shown = _popup(monkeypatch)
    app = _App()
    assert app._verify_login_after_close(_Plat(True), "wechatpay", "旗舰店A",
                                         saved_seen=True) is True
    assert app.statuses == [("wechatpay", "ok")]
    assert shown == []
    assert app.ensure_kwargs == {"force_visible": False, "force_headless": True}


def test_not_logged_in_warns_and_explains_which_signal_failed(monkeypatch):
    shown = _popup(monkeypatch)
    app = _App()
    verdict = app._verify_login_after_close(_Plat(False), "wechatpay", "旗舰店A",
                                            saved_seen=True)
    assert verdict is False
    assert app.statuses == [("wechatpay", "error")]
    assert len(shown) == 1
    title, msg = shown[0]
    assert "未登录" in title or "没登录" in title
    assert "cookie 有写入但页面仍未登录" in msg
    assert "不影响其它平台" in msg           # 说清楚这不是把它拦下来了


def test_no_cookie_written_is_named_as_the_reason(monkeypatch):
    shown = _popup(monkeypatch)
    app = _App()
    app._verify_login_after_close(_Plat(False), "wechatpay", "旗舰店A", saved_seen=False)
    assert "没检测到任何登录写入" in shown[0][1]


def test_a_broken_check_is_not_reported_as_not_logged_in(monkeypatch):
    """核实自己跑不动(超时/页面异常)不能算"未登录": 只写日志 + 橙灯。"""
    shown = _popup(monkeypatch)
    app = _App()
    verdict = app._verify_login_after_close(
        _Plat(raises=TimeoutError("页面加载超时")), "wechatpay", "旗舰店A", True)
    assert verdict is None
    assert app.statuses == [("wechatpay", "warn")]
    assert shown == []
    assert any("没能核实" in line for line in app.logs)


def test_saved_login_without_cookie_change_says_so(monkeypatch):
    _popup(monkeypatch)
    app = _App()
    app._verify_login_after_close(_Plat(True), "wechatpay", "旗舰店A", saved_seen=False)
    assert any("没看到 cookie 变化" in line for line in app.logs)


# ===== 真实 _ensure_browser 的 headless 参数 =====

class _Recorder:
    last = None

    def __init__(self, headless=False, log_callback=None):
        type(self).last = {"headless": headless}
        self.profile = None

    def set_browser_profile(self, key, merchant):
        self.profile = (key, merchant)

    def start(self):
        return None


class _GuiApp:
    _ensure_browser = LiushuiApp._ensure_browser

    def __init__(self):
        self.browser = None

    def _append_log(self, line):
        pass


@pytest.mark.parametrize("settings,kwargs,expected", [
    # (settings.json 里的 show_browser, 调用参数, 期望的 headless)
    (True, {"force_visible": True}, False),    # 登录: 必须看得见
    (True, {"force_headless": True}, True),    # 核实: 别多弹一个窗口闪一下
    (True, {}, False),                         # 默认按设置(显示浏览器)
    (False, {}, True),                         # 默认按设置(后台运行)
])
def test_headless_choice_per_caller(monkeypatch, settings, kwargs, expected):
    monkeypatch.setattr(cb, "BrowserManager", _Recorder)
    monkeypatch.setattr(mg, "load_settings", lambda: {"show_browser": settings})
    _GuiApp()._ensure_browser(None, "", **kwargs)
    assert _Recorder.last["headless"] is expected


# ===== _do_login 的时序 =====

class _LoginApp:
    _do_login = LiushuiApp._do_login
    _aborted = LiushuiApp._aborted
    _close_browser = LiushuiApp._close_browser
    LOGIN_WAIT_TIMEOUT_S = 30 * 60

    def __init__(self, outcome="closed", verdict=True, saved=True):
        self._abort = threading.Event()
        self.trace = []
        self.outcome = outcome
        self.verdict = verdict
        self.saved = saved
        self.browser = None
        self.login_browser = None

    def _set_status(self, text):
        pass

    def _set_progress(self, **kwargs):
        pass

    def set_platform_status(self, key, status):
        pass

    def _append_log(self, line):
        self.trace.append(("log", line))

    def _ui(self, fn):
        fn()

    def _ensure_browser(self, plat, merchant, force_visible=False, force_headless=False):
        self.trace.append(("open", "visible" if force_visible else "headless"))
        self.browser = _Browser("登录窗口")
        if force_visible:
            self.login_browser = self.browser

    def _show_login_hint(self, *a):
        self.trace.append(("hint", "on"))

    def _close_login_hint(self):
        self.trace.append(("hint", "off"))

    def _wait_login_window_closed(self, browser):
        self.trace.append(("wait", self.outcome))
        return self.outcome, self.saved

    def _verify_login_after_close(self, plat, key, merchant, saved_seen):
        # 记下核实开始时登录窗口是否已经被关掉(profile 只能被一个 Chromium 占用)
        self.trace.append(("verify", self.login_browser.closed))
        return self.verdict


def _steps(app):
    return [k for k, _ in app.trace if k != "log"]


def _summary(app):
    return [v for k, v in app.trace if k == "log"][-1]


def test_login_window_is_released_before_the_headless_check():
    """profile 同时只能被一个 Chromium 占用: 关窗口 → 释放 profile → 再后台核实。"""
    app = _LoginApp()
    plat = _Plat(True)
    plat.login = lambda browser: app.trace.append(("login", None))
    app._do_login([("wechatpay", plat, "旗舰店A")])
    assert _steps(app) == ["open", "login", "hint", "wait", "hint", "verify"]
    assert ("verify", 1) in app.trace, "核实前必须先释放登录时那个 profile"


def test_abort_skips_the_verification_entirely():
    app = _LoginApp(outcome="aborted")
    plat = _Plat(True)
    plat.login = lambda browser: None
    app._do_login([("wechatpay", plat, "旗舰店A")])
    assert "verify" not in _steps(app)


def test_timeout_skips_the_verification_too():
    app = _LoginApp(outcome="timeout")
    plat = _Plat(True)
    plat.login = lambda browser: None
    app._do_login([("wechatpay", plat, "旗舰店A")])
    assert "verify" not in _steps(app)


def test_summary_counts_each_verdict():
    app = _LoginApp(verdict=True)
    plat = _Plat(True)
    plat.login = lambda browser: None
    app._do_login([("wechatpay", plat, "A店")])
    text = _summary(app)
    assert "已核实登录成功 1" in text and "未登录 0" in text


def test_broken_browser_counts_as_not_logged_in():
    app = _LoginApp()
    plat = _Plat(True)

    def _boom(browser):
        raise RuntimeError("起不来")
    plat.login = _boom
    app._do_login([("wechatpay", plat, "A店")])
    assert _summary(app).startswith("登录流程完成: 已核实登录成功 0 / 未登录 1")


def test_empty_task_list_says_there_is_nothing_selected():
    app = _LoginApp()
    app._do_login([])
    assert "未勾选任何平台商户" in _summary(app)
