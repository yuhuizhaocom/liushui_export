"""_run_single_export 的结果落库语义: 三条出口都要进 stats, 且登录失效不执行导出。

_run_single_export 跑在工作线程上, 日期与"单步调试"开关由主线程取好后作为参数传入
(见 _action_export_all), 因此这里可以用假 self 直接调真实方法。
"""
import core.main_gui as mg
from core.main_gui import LiushuiApp


class _FakeBrowser:
    def __init__(self):
        self.export_context = None
        self.step_debug = None
        self.step_debug_calls = []
        self.user_wait_cb = "unset"

    def set_export_context(self, *args):
        self.export_context = args

    def set_step_debug(self, *args):
        self.step_debug = args
        self.step_debug_calls.append(args)

    def set_user_wait_callback(self, cb):
        self.user_wait_cb = cb


class _FakeApp:
    """只挂 _run_single_export 用到的那几个成员, 不创建 Tk 窗口。
    _self_check_selectors 用真实实现, 自检逻辑本身也被覆盖到。"""

    _self_check_selectors = LiushuiApp._self_check_selectors

    def __init__(self):
        self.browser = _FakeBrowser()
        self.logs = []

    def _append_log(self, line):
        self.logs.append(line)

    def _ensure_browser(self, plat=None, merchant="", force_visible=False):
        pass


class _FakePlat:
    key = "fake"
    name = "假平台"

    def __init__(self, login_ok=True, export_result="success", export_raises=None):
        self.login_ok = login_ok
        self.export_result = export_result
        self.export_raises = export_raises
        self.export_called = 0

    def check_login(self, browser):
        return self.login_ok

    def export(self, browser, start_date, end_date):
        self.export_called += 1
        if self.export_raises:
            raise self.export_raises
        return self.export_result


def _run(monkeypatch, plat, step_debug=False):
    recs = []
    monkeypatch.setattr(mg, "record_stat", lambda *a, **k: recs.append((a, k)))
    app = _FakeApp()
    result = LiushuiApp._run_single_export(app, plat, "旗舰店A", "2026-09-01",
                                           "2026-09-02", step_debug=step_debug)
    return result, recs, app


def test_login_expired_returns_manual_and_skips_export(monkeypatch):
    plat = _FakePlat(login_ok=False)
    result, _recs, _app = _run(monkeypatch, plat)
    assert result == "manual"
    assert plat.export_called == 0


def test_login_expired_is_recorded_in_stats(monkeypatch):
    """回归: 预检失败以前直接 return, 不落 stats.jsonl, 看板看不到最主要的失败原因。"""
    _result, recs, _app = _run(monkeypatch, _FakePlat(login_ok=False))
    assert len(recs) == 1
    args, kwargs = recs[0]
    assert args[0] == "假平台" and args[1] == "旗舰店A"
    assert args[4] == "manual"
    assert "登录已失效" in kwargs["error"]


def test_check_login_exception_still_exports(monkeypatch):
    class _Boom(_FakePlat):
        def check_login(self, browser):
            raise RuntimeError("页面还没渲染完")

    plat = _Boom(export_result="manual")
    result, recs, _app = _run(monkeypatch, plat)
    assert plat.export_called == 1
    assert result == "manual"
    assert recs[0][0][4] == "manual"
    assert recs[0][1]["error"] == ""      # 非登录失效的路径不编造原因


def test_export_exception_recorded_as_failed(monkeypatch):
    plat = _FakePlat(export_raises=RuntimeError("下载超时"))
    result, recs, _app = _run(monkeypatch, plat)
    assert result == "failed"
    assert recs[0][0][4] == "failed"
    assert "下载超时" in recs[0][1]["error"]


def test_callbacks_cleared_after_export(monkeypatch):
    _result, _recs, app = _run(monkeypatch, _FakePlat())
    assert app.browser.step_debug == (False,)
    assert app.browser.user_wait_cb is None


def test_step_debug_comes_from_argument(monkeypatch):
    """单步开关以前由工作线程直接读 Tk 变量, 现在只能是入参。
    收尾会无条件关掉单步, 所以要看调用历史而不是最后一次的值。"""
    _result, _recs, app = _run(monkeypatch, _FakePlat(), step_debug=True)
    installed = [a for a in app.browser.step_debug_calls if a and a[0] is True]
    assert len(installed) == 1
    assert callable(installed[0][1])


def test_step_debug_not_installed_by_default(monkeypatch):
    _result, _recs, app = _run(monkeypatch, _FakePlat())
    assert [a for a in app.browser.step_debug_calls if a and a[0] is True] == []


def test_browser_startup_failure_returns_failed_and_records(monkeypatch):
    """回归: 浏览器起不来以前向外抛异常, 既不落 stats, 也让 run_with_retry 拿不到
    "failed" 返回值, 整批重试被折成一次失败。"""
    class _NoBrowser(_FakeApp):
        def _ensure_browser(self, plat=None, merchant="", force_visible=False):
            raise RuntimeError("Chromium 启动失败")

    recs = []
    monkeypatch.setattr(mg, "record_stat", lambda *a, **k: recs.append((a, k)))
    app = _NoBrowser()
    app.browser = None                       # 浏览器根本没起来, 收尾不得再碰它
    plat = _FakePlat()
    result = LiushuiApp._run_single_export(app, plat, "旗舰店A",
                                           "2026-09-01", "2026-09-02")
    assert result == "failed"
    assert plat.export_called == 0
    assert recs[0][0][4] == "failed"
    assert "Chromium" in recs[0][1]["error"]


def test_startup_failure_goes_through_retry(monkeypatch):
    class _Flaky(_FakeApp):
        def __init__(self):
            super().__init__()
            self.browser = None
            self.attempts = 0

        def _ensure_browser(self, plat=None, merchant="", force_visible=False):
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("Chromium 启动失败")
            self.browser = _FakeBrowser()

    monkeypatch.setattr(mg, "record_stat", lambda *a, **k: None)
    app, plat = _Flaky(), _FakePlat()
    result = mg.run_with_retry(
        lambda: LiushuiApp._run_single_export(app, plat, "旗舰店A",
                                              "2026-09-01", "2026-09-02"),
        retry_times=2, retry_interval_s=0, log=lambda m: None)
    assert result == "success"
    assert app.attempts == 3
    assert plat.export_called == 1


class _DeclaredPlat(_FakePlat):
    """声明了 SELECTORS 的平台: 基类自检应当在导出前被跑到。"""

    SELECTORS = {"export_btn": "button.export"}

    def __init__(self, **kw):
        super().__init__(**kw)
        self.checked = 0

    def check_selectors(self, browser, timeout=2):
        self.checked += 1
        return False, ["export_btn"]


def test_declared_selectors_checked_before_export(monkeypatch):
    plat = _DeclaredPlat()
    _result, recs, app = _run(monkeypatch, plat)
    assert plat.checked == 1
    assert any("export_btn" in line for line in app.logs)
    assert recs[0][0][4] == "success"       # 自检不通过不改变本次结果


def test_selector_check_crash_does_not_break_export(monkeypatch):
    class _Boom(_DeclaredPlat):
        def check_selectors(self, browser, timeout=2):
            raise RuntimeError("page 已关闭")

    plat = _Boom()
    result, _recs, app = _run(monkeypatch, plat)
    assert result == "success"
    assert plat.export_called == 1
    assert any("自检异常" in line for line in app.logs)


def test_platform_without_selectors_skips_check(monkeypatch):
    plat = _FakePlat()                      # 没有 SELECTORS, 也没有 check_selectors
    result, _recs, app = _run(monkeypatch, plat)
    assert result == "success"
    assert not any("自检" in line for line in app.logs)


class _DialogApp:
    """只测 _prompt_manual_leftovers 的消息组织, _ui 就地执行。"""

    _prompt_manual_leftovers = LiushuiApp._prompt_manual_leftovers

    def __init__(self):
        self.shown = []

    def _ui(self, fn):
        fn()


def test_manual_platforms_summarized_in_one_dialog(monkeypatch):
    import tkinter.messagebox as mb
    shown = []
    monkeypatch.setattr(mb, "showinfo", lambda title, msg: shown.append((title, msg)))
    app = _DialogApp()
    app._prompt_manual_leftovers([("微信支付", "商户A", "账单 → 明细 → 导出"),
                                  ("有赞", "旗舰店B", "数据 → 交易明细 → 导出")],
                                 "2026-09-01 至 2026-09-02")
    assert len(shown) == 1                       # 五个要手动也只弹一个窗
    title, msg = shown[0]
    assert "2" in title
    assert "微信支付(商户A)" in msg and "有赞(旗舰店B)" in msg
    assert "账单 → 明细 → 导出" in msg            # 每个平台的指引都留着
    assert "2026-09-01 至 2026-09-02" in msg


def test_manual_summary_silent_when_nothing_left(monkeypatch):
    import tkinter.messagebox as mb
    shown = []
    monkeypatch.setattr(mb, "showinfo", lambda title, msg: shown.append((title, msg)))
    _DialogApp()._prompt_manual_leftovers([], "2026-09-01 至 2026-09-02")
    assert shown == []
