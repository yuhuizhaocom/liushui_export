"""关窗口后: 保存→后台核实→已登录就关掉, 未登录让用户在"回去登录/放弃"之间选。

为什么这么绕一圈: Chromium 一退出, session cookie 和 context 都没了 —— 想"关窗前保
存"就得一直存着(等待期间按 cookie 变化当场导出 + close() 前再存一次), 想"关窗前检查"
也不可能(检查要活的浏览器)。所以顺序只能是: 释放这个 profile → 后台重开一次核实。

未登录时不能由程序替用户决定放弃: 微信支付那类"扫码页与后台同域"的平台只能看页面文案,
存在"其实登上了但被判未登录"的可能, 所以弹「重试/取消」让他选。
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
        self.watch = None

    def login(self, browser):
        pass

    def check_login(self, browser):
        self.checked += 1
        if self.watch is not None:
            self.watch()                 # 让测试看一眼此刻登录窗口是否已释放
        if self.raises:
            raise self.raises
        return self.result


class _Browser:
    def __init__(self, label="b"):
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
        self.ensure_kwargs = None

    def _append_log(self, line):
        self.logs.append(line)

    def _set_status(self, text):
        self.status_text.append(text)

    def set_platform_status(self, key, status):
        self.statuses.append((key, status))

    def _ui(self, fn):
        self.ui_calls.append(fn)
        fn()

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


def test_logged_in_turns_the_light_green_without_any_popup(monkeypatch):
    _popup(monkeypatch)
    app = _App()
    assert app._verify_login_after_close(_Plat(True), "wechatpay", "旗舰店A",
                                         saved_seen=True) is True
    assert app.statuses == [("wechatpay", "ok")]
    assert app.ensure_kwargs == {"force_visible": False, "force_headless": True}


def test_not_logged_in_says_which_signal_failed(monkeypatch):
    _popup(monkeypatch)
    app = _App()
    assert app._verify_login_after_close(_Plat(False), "wechatpay", "旗舰店A",
                                         saved_seen=True) is False
    assert app.statuses == [("wechatpay", "error")]
    assert app._last_verify_reason == "cookie 有写入但页面仍未登录(可能登录被服务端拒绝或还没走完)"
    assert any("未登录" in line for line in app.logs)
    assert app.logs[-1].startswith("  [警告]")


def test_no_cookie_written_is_named_as_the_reason(monkeypatch):
    _popup(monkeypatch)
    app = _App()
    app._verify_login_after_close(_Plat(False), "wechatpay", "旗舰店A", saved_seen=False)
    assert "没检测到任何登录写入" in app._last_verify_reason


def test_the_popup_is_not_the_verifier_job_anymore(monkeypatch):
    """弹窗交给 _do_login 决定(要给用户"回去登录"的选择), 核实本身只写日志。"""
    shown = _popup(monkeypatch)
    _App()._verify_login_after_close(_Plat(False), "wechatpay", "旗舰店A", True)
    assert shown == []


def test_a_broken_check_is_not_reported_as_not_logged_in(monkeypatch):
    """核实自己跑不动(超时/页面异常)只算"没能核实", 不冒充"未登录"、也不弹窗。"""
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


# ===== 一轮登录: 必须先释放 profile 再后台核实 =====

class _RoundApp(_App):
    _login_one_round = LiushuiApp._login_one_round
    _aborted = LiushuiApp._aborted
    LOGIN_WAIT_TIMEOUT_S = 30 * 60

    def __init__(self, outcome="closed", saved=True):
        super().__init__()
        self.opened = []
        self.browsers = []
        self.outcome = outcome
        self.saved = saved
        self._abort = threading.Event()

    def _append_log(self, line):
        super()._append_log(line)

    def _ensure_browser(self, plat=None, merchant="", force_visible=False,
                        force_headless=False):
        self.opened.append("visible" if force_visible else "headless")
        self.browser = _Browser(self.opened[-1])
        self.browsers.append(self.browser)

    def _show_login_hint(self, *a):
        pass

    def _close_login_hint(self):
        pass

    def _wait_login_window_closed(self, browser):
        return self.outcome, self.saved


def test_headless_check_starts_only_after_the_login_profile_is_released():
    app = _RoundApp()
    plat = _Plat(True)
    visible = []
    plat.watch = lambda: visible.append(app.browsers[0].closed)
    assert app._login_one_round(plat, "wechatpay", "旗舰店A") is True
    assert app.opened == ["visible", "headless"]
    assert visible == [1], "核实开始时, 登录那个浏览器必须已经关掉"
    assert app.browsers[-1].closed == 1, "核实用的浏览器也要收掉"


def test_aborted_round_does_not_verify():
    app = _RoundApp(outcome="aborted")
    assert app._login_one_round(_Plat(True), "wechatpay", "旗舰店A") == "skip"
    assert app.opened == ["visible"]


def test_timeout_round_does_not_verify():
    app = _RoundApp(outcome="timeout")
    plat = _Plat(True)
    assert app._login_one_round(plat, "wechatpay", "旗舰店A") == "skip"
    assert plat.checked == 0
    assert app.opened == ["visible"]


def test_a_failing_login_page_still_closes_the_browser():
    app = _RoundApp()

    def _boom(browser):
        raise RuntimeError("登录页打不开")
    plat = _Plat(True)
    plat.login = _boom
    assert app._login_one_round(plat, "wechatpay", "旗舰店A") == "skip"
    assert app.browsers[0].closed == 1
    assert app.statuses == [("wechatpay", "warn"), ("wechatpay", "error")]


# ===== _ask_login_retry: 用户二选一 =====

class _AskApp(_App):
    _ask_login_retry = LiushuiApp._ask_login_retry

    def __init__(self):
        super().__init__()
        self._abort = threading.Event()


@pytest.mark.parametrize("answer,expected", [(True, True), (False, False)])
def test_retry_choice_comes_back_from_the_dialog(monkeypatch, answer, expected):
    asked = []
    monkeypatch.setattr(mg.messagebox, "askretrycancel",
                        lambda title, msg: asked.append((title, msg)) or answer)
    app = _AskApp()
    app._last_verify_reason = "关窗口时没检测到任何登录写入"
    got = app._ask_login_retry(_Plat(), "旗舰店A", 1)
    assert got is expected
    title, msg = asked[0]
    assert "重试" in msg and "取消" in msg
    assert "没检测到任何登录写入" in msg        # 把原因带进选择框
    assert "稍后可单独对它做一次「首次登录」" in msg


def test_a_broken_dialog_defaults_to_giving_up(monkeypatch):
    """弹窗起不来时不能把整批任务卡在半路 —— 按"放弃"处理。"""
    def _boom(title, msg):
        raise RuntimeError("没有显示器")
    monkeypatch.setattr(mg.messagebox, "askretrycancel", _boom)
    assert _AskApp()._ask_login_retry(_Plat(), "旗舰店A", 1) is False


def test_no_answer_within_the_cap_is_giving_up(monkeypatch):
    app = _AskApp()
    monkeypatch.setattr(mg.messagebox, "askretrycancel", lambda t, m: True)
    monkeypatch.setattr(mg.threading.Event, "wait", lambda self, timeout=None: False)
    assert app._ask_login_retry(_Plat(), "旗舰店A", 1) is False
    assert any("无人应答" in line for line in app.logs)


# ===== _do_login 的重试循环 =====

class _LoginApp:
    _do_login = LiushuiApp._do_login
    _aborted = LiushuiApp._aborted
    LOGIN_RETRY_LIMIT = 3

    def __init__(self, verdicts, retry_answer=False):
        self._abort = threading.Event()
        self.trace = []
        self.verdicts = list(verdicts)
        self.retry_answer = retry_answer
        self.asked = 0
        self.progress = []

    def _set_status(self, text):
        pass

    def _set_progress(self, **kwargs):
        if "value" in kwargs:
            self.progress.append(kwargs["value"])

    def set_platform_status(self, key, status):
        pass

    def _append_log(self, line):
        self.trace.append(line)

    def _login_one_round(self, plat, key, merchant):
        return self.verdicts.pop(0)

    def _ask_login_retry(self, plat, merchant, attempt):
        self.asked += 1
        return self.retry_answer

    def _verify_login_after_close(self, *a, **k):
        raise AssertionError("不该被 _do_login 直接调")


def _summary(app):
    return app.trace[-1]


def test_retry_then_success_counts_as_logged_in():
    app = _LoginApp([False, True], retry_answer=True)
    app._do_login([("wechatpay", _Plat(), "旗舰店A")])
    assert app.asked == 1
    assert "已核实登录成功 1" in _summary(app)
    assert app.progress == [0, 1], "进度只在商户边界推进一次"


def test_giving_up_moves_on_without_another_round():
    app = _LoginApp([False], retry_answer=False)
    app._do_login([("wechatpay", _Plat(), "旗舰店A")])
    assert app.asked == 1
    assert app.verdicts == []              # 没跑第二轮
    assert any("按您的选择先放弃这家" in line for line in app.trace)
    assert "未登录 1" in _summary(app)


def test_retry_loop_has_a_cap_and_stops_asking_at_it():
    app = _LoginApp([False, False, False], retry_answer=True)
    app._do_login([("wechatpay", _Plat(), "旗舰店A")])
    assert app.asked == 2                  # 第 3 次失败后不再问
    assert any("已试过 3 次" in line for line in app.trace)
    assert "未登录 1" in _summary(app)


def test_unverifiable_and_skipped_rounds_never_ask():
    app = _LoginApp([None], retry_answer=True)
    app._do_login([("wechatpay", _Plat(), "A店")])
    assert app.asked == 0 and "没能核实 1" in _summary(app)

    app2 = _LoginApp(["skip"], retry_answer=True)
    app2._do_login([("wechatpay", _Plat(), "A店")])
    assert app2.asked == 0 and "未完成 1" in _summary(app2)


def test_abort_before_a_retry_ends_the_round():
    app = _LoginApp([False, True], retry_answer=True)
    app._abort.set()                        # 第一家就已经被中止 → 整批不再跑
    app._do_login([("wechatpay", _Plat(), "A店")])
    assert app.asked == 0 and app.verdicts == [False, True]


def test_several_merchants_each_get_their_own_choice():
    app = _LoginApp([False, True], retry_answer=False)
    app._do_login([("wechatpay", _Plat(), "A店"), ("wechatpay", _Plat(), "B店")])
    assert app.asked == 1
    assert app.progress == [0, 1, 2]
    assert "已核实登录成功 1" in _summary(app) and "未登录 1" in _summary(app)


# ===== 真实 _ensure_browser 的 headless 参数 =====

class _Recorder:
    last = None

    def __init__(self, headless=False, log_callback=None):
        type(self).last = {"headless": headless}

    def set_browser_profile(self, key, merchant):
        pass

    def start(self):
        return None


class _GuiApp:
    _ensure_browser = LiushuiApp._ensure_browser

    def __init__(self):
        self.browser = None

    def _append_log(self, line):
        pass


@pytest.mark.parametrize("settings,kwargs,expected", [
    (True, {"force_visible": True}, False),     # 登录: 必须看得见
    (True, {"force_headless": True}, True),     # 核实: 别多弹一个窗口闪一下
    (True, {}, False),                          # 默认按设置(显示浏览器)
    (False, {}, True),                          # 默认按设置(后台运行)
])
def test_headless_choice_per_caller(monkeypatch, settings, kwargs, expected):
    monkeypatch.setattr(cb, "BrowserManager", _Recorder)
    monkeypatch.setattr(mg, "load_settings", lambda: {"show_browser": settings})
    _GuiApp()._ensure_browser(None, "", **kwargs)
    assert _Recorder.last["headless"] is expected


def test_production_login_limits_are_sane():
    assert LiushuiApp.LOGIN_WAIT_TIMEOUT_S == 30 * 60
    assert 0.5 <= LiushuiApp.LOGIN_POLL_S <= 5
    assert 2 <= LiushuiApp.LOGIN_RETRY_LIMIT <= 5
