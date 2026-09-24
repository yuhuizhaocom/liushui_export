"""_run_single_export 的结果落库语义: 三条出口都要进 stats, 且登录失效不执行导出。"""
import core.main_gui as mg
from core.main_gui import LiushuiApp


class _FakeBrowser:
    def __init__(self):
        self.export_context = None
        self.step_debug = None
        self.user_wait_cb = "unset"

    def set_export_context(self, *args):
        self.export_context = args

    def set_step_debug(self, *args):
        self.step_debug = args

    def set_user_wait_callback(self, cb):
        self.user_wait_cb = cb


class _Var:
    def get(self):
        return False


class _FakeApp:
    """只挂 _run_single_export 用到的那几个成员, 不创建 Tk 窗口。"""

    def __init__(self):
        self.browser = _FakeBrowser()
        self.step_debug_var = _Var()
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


def _run(monkeypatch, plat):
    recs = []
    monkeypatch.setattr(mg, "record_stat",
                        lambda *a, **k: recs.append((a, k)))
    app = _FakeApp()
    result = LiushuiApp._run_single_export(app, plat, "旗舰店A", "2026-09-01", "2026-09-02")
    return result, recs, app


def test_login_expired_returns_manual_and_skips_export(monkeypatch):
    plat = _FakePlat(login_ok=False)
    result, recs, _ = _run(monkeypatch, plat)
    assert result == "manual"
    assert plat.export_called == 0


def test_login_expired_is_recorded_in_stats(monkeypatch):
    """回归: 预检失败以前直接 return, 不落 stats.jsonl, 看板看不到最主要的失败原因。"""
    _result, recs, _ = _run(monkeypatch, _FakePlat(login_ok=False))
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
    result, recs, _ = _run(monkeypatch, plat)
    assert plat.export_called == 1
    assert result == "manual"
    assert recs[0][0][4] == "manual"
    assert recs[0][1]["error"] == ""


def test_export_exception_recorded_as_failed(monkeypatch):
    plat = _FakePlat(export_raises=RuntimeError("下载超时"))
    result, recs, _ = _run(monkeypatch, plat)
    assert result == "failed"
    assert recs[0][0][4] == "failed"
    assert "下载超时" in recs[0][1]["error"]


def test_callbacks_cleared_after_export(monkeypatch):
    _result, _recs, app = _run(monkeypatch, _FakePlat())
    assert app.browser.step_debug == (False,)
    assert app.browser.user_wait_cb is None
