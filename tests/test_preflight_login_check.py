"""导出前的登录预检: 要人操作的事集中在开跑前一次处理完。

预检跑在工作线程上, 界面动作一律经 `_ui`, 浏览器动作经 `_ensure_browser`, 所以可以
用假 self 调真实方法(与 test_login_verify.py 同一套路), 不需要 Tk 也不需要真浏览器。
"""
import threading

import core.main_gui as mg
from core.main_gui import LiushuiApp


class _Plat:
    """check_login 的三种结局: 已登录 / 未登录 / 抛异常(=没能核实)。"""

    def __init__(self, name, login=True):
        self.key = name
        self.name = name
        self.login = login
        self.checked = 0

    def check_login(self, browser):
        self.checked += 1
        if isinstance(self.login, Exception):
            raise self.login
        return self.login


class _Browser:
    def get_page_info(self):
        return {"title": "后台首页"}


def _tasks(*specs):
    """specs: (平台名, 商户, 登录结论) → [(key, plat, merchant)]"""
    return [(name, _Plat(name, login), m) for name, m, login in specs]


class _PreApp:
    """只挂预检用到的成员; `_probe_login` 用真实实现, 探测语义一并覆盖。"""

    _preflight_login_check = LiushuiApp._preflight_login_check
    _probe_login = LiushuiApp._probe_login
    _aborted = LiushuiApp._aborted
    _drop_by_index = staticmethod(LiushuiApp._drop_by_index)

    def __init__(self, relogin_answer=False, relogin_verdicts=()):
        self._abort = threading.Event()
        self.logs = []
        self.statuses = []
        self.probes = []            # (平台名, 商户, force_headless)
        self.asked = []             # 每次预检弹窗收到的失效清单
        self.relogin_answer = relogin_answer
        self.relogin_verdicts = list(relogin_verdicts)
        self.relogin_tasks = None

    # —— 界面侧: 记账, 不真弹窗(模态窗会把测试吊住) ——
    def _ask_preflight_login(self, expired, ok_count):
        self.asked.append([(p.name, m) for _i, p, m in expired])
        return self.relogin_answer

    def _do_login(self, tasks):
        self.relogin_tasks = list(tasks)
        return list(self.relogin_verdicts)

    def _append_log(self, line):
        self.logs.append(line)

    def _set_status(self, text):
        self.statuses.append(text)

    def _ensure_browser(self, plat=None, merchant="", force_visible=False,
                        force_headless=False):
        self.browser = _Browser()
        self.probes.append((getattr(plat, "name", ""), merchant, force_headless))


def _merchants(tasks):
    return [(key, m) for key, _p, m in tasks]


# ===== 预检的判定与提示 =====

def test_all_logged_in_exports_without_a_dialog():
    app = _PreApp()
    tasks = _tasks(("有赞", "A店", True), ("天猫", "B店", True))
    assert app._preflight_login_check(tasks) == tasks
    assert app.asked == []
    assert "2 家商户登录状态检查通过" in app.logs[-1]


def test_expired_merchants_are_asked_about_once_for_the_whole_batch():
    """三家失效只问一次: 一家一个框正是这次要去掉的动作。"""
    app = _PreApp()
    tasks = _tasks(("有赞", "A店", False), ("天猫", "B店", True),
                   ("京东", "C店", False), ("拼多多", "D店", False))
    app._preflight_login_check(tasks)
    assert len(app.asked) == 1
    assert app.asked[0] == [("有赞", "A店"), ("京东", "C店"), ("拼多多", "D店")]


def test_preflight_probe_runs_in_the_background():
    """预检连着探 N 家, 跟着"显示浏览器"设置会一个接一个闪窗口。"""
    app = _PreApp()
    app._preflight_login_check(_tasks(("有赞", "A店", True), ("天猫", "B店", True)))
    assert [p[2] for p in app.probes] == [True, True]


def test_unverifiable_merchant_is_still_exported():
    """浏览器起不来/平台脚本报错 ≠ 没登录: 不能因附加检查把用户的账单挡掉。"""
    app = _PreApp()
    tasks = _tasks(("有赞", "A店", RuntimeError("Chromium 启动失败")),
                   ("天猫", "B店", True))
    assert _merchants(app._preflight_login_check(tasks)) == [("有赞", "A店"), ("天猫", "B店")]
    assert app.asked == []
    assert any("没能核实登录状态" in line and "照常导出" in line for line in app.logs)
    assert any("Chromium 启动失败" in line for line in app.logs)


def test_probe_login_never_raises_outwards():
    app = _PreApp()

    def _boom(*a, **k):
        raise RuntimeError("profile 被占用")

    app._ensure_browser = _boom
    assert app._probe_login(_Plat("有赞"), "A店") == (None, "profile 被占用")


def test_abort_during_preflight_exports_nothing():
    app = _PreApp()
    app._abort.set()
    assert app._preflight_login_check(_tasks(("有赞", "A店", True))) == []
    assert app.probes == [] and app.asked == []
    assert any("登录预检中止" in line for line in app.logs)


# ===== 用户在预检框里的两个选择 =====

def test_saying_no_skips_only_the_expired_ones():
    app = _PreApp(relogin_answer=False)
    tasks = _tasks(("有赞", "A店", False), ("天猫", "B店", True), ("京东", "C店", False))
    kept = app._preflight_login_check(tasks)
    assert _merchants(kept) == [("天猫", "B店")]
    assert app.relogin_tasks is None            # 没选重登就不该拉起登录窗
    assert any("本次跳过这 2 家" in line for line in app.logs)


def test_saying_yes_relogs_the_expired_and_drops_the_ones_still_bad():
    app = _PreApp(relogin_answer=True, relogin_verdicts=["ok", "bad"])
    tasks = _tasks(("有赞", "A店", False), ("天猫", "B店", True), ("京东", "C店", False))
    kept = app._preflight_login_check(tasks)
    assert _merchants(app.relogin_tasks) == [("有赞", "A店"), ("京东", "C店")]
    assert _merchants(kept) == [("有赞", "A店"), ("天猫", "B店")]   # 顺序不变, 登回来的照常导
    assert any("重登后仍未登录 1 家" in line for line in app.logs)


def test_unfinished_login_rounds_stay_in_the_batch():
    """'skip'/'unknown'(用户没关窗口、核实没做成)不剔除: 导出时的 inline 预检再兜一次。"""
    app = _PreApp(relogin_answer=True, relogin_verdicts=["skip", "unknown"])
    kept = app._preflight_login_check(_tasks(("有赞", "A店", False)))
    assert _merchants(kept) == [("有赞", "A店")]
    assert not any("重登后仍未登录" in line for line in app.logs)


def test_dedup_keeps_original_order_when_nothing_dropped():
    tasks = _tasks(("有赞", "A店", True), ("天猫", "B店", True))
    assert LiushuiApp._drop_by_index(tasks, set()) == tasks
    assert LiushuiApp._drop_by_index(tasks, {0}) == [tasks[1]]


# ===== 预检那个框本身(真实现) =====

class _AskApp:
    """只测消息组织与选择回传: `_ui` 就地执行, 弹窗由 monkeypatch 顶掉。"""

    _ask_preflight_login = LiushuiApp._ask_preflight_login

    def __init__(self, answer=True):
        self.answer = answer
        self.shown = []
        self.logs = []

    def _ui(self, fn):
        fn()

    def _append_log(self, line):
        self.logs.append(line)

    def _ask(self, title, msg):
        self.shown.append((title, msg))
        if self.answer is None:
            raise RuntimeError("没有 Tk 主窗口")
        return self.answer


def _expired(n):
    return [(i, _Plat(f"平台{i}"), f"{i}店") for i in range(n)]


def test_one_dialog_lists_every_expired_merchant(monkeypatch):
    import tkinter.messagebox as mb
    app = _AskApp(answer=True)
    monkeypatch.setattr(mb, "askretrycancel", app._ask)
    assert app._ask_preflight_login(_expired(3), 5) is True
    assert len(app.shown) == 1
    title, msg = app.shown[0]
    assert "3" in title
    assert "平台0(0店)" in msg and "平台2(2店)" in msg
    assert "重试" in msg and "取消" in msg        # 按钮上就写着各自对应什么
    assert "5" in msg                             # 说清其余几家照常被导


def test_no_answer_means_skip(monkeypatch):
    """判不出/没 Tk 时按"跳过这些"处理: 不能把整批吊在一个没人看的框上。"""
    import tkinter.messagebox as mb
    app = _AskApp(answer=None)
    monkeypatch.setattr(mb, "askretrycancel", app._ask)
    assert app._ask_preflight_login(_expired(1), 2) is False


def test_dialog_overflows_gracefully_past_twelve(monkeypatch):
    import tkinter.messagebox as mb
    app = _AskApp(answer=False)
    monkeypatch.setattr(mb, "askretrycancel", app._ask)
    app._ask_preflight_login(_expired(15), 1)
    _title, msg = app.shown[0]
    assert "…… 等共 15 家" in msg
    assert "平台12(12店)" not in msg              # 不再往下塞第 13 行


# ===== 预检接入导出入口(定时任务必须不被弹窗吊住) =====

class _ExportApp:
    _do_export = LiushuiApp._do_export

    def __init__(self, kept):
        self._kept = kept
        self.exported_with = None
        self.recorded = 0
        self.statuses = []

    def _preflight_login_check(self, tasks):
        return self._kept

    def _execute_export_tasks(self, tasks, start, end, label="导出", step_debug=False):
        self.exported_with = tasks

    def _record_job_done(self, label, start, end):
        self.recorded += 1

    def _set_status(self, text):
        self.statuses.append(text)


def test_do_export_runs_preflight_first():
    tasks = _tasks(("有赞", "A店", True))
    app = _ExportApp(tasks)
    app._do_export(tasks, "2026-09-01", "2026-09-02")
    assert app.exported_with == tasks and app.recorded == 1


def test_do_export_stops_when_preflight_leaves_nothing():
    tasks = _tasks(("有赞", "A店", True))
    app = _ExportApp([])
    app._do_export(tasks, "2026-09-01", "2026-09-02")
    assert app.exported_with is None
    assert app.recorded == 0                      # 没导过东西, 不该报"导出完成"
    assert any("没有可导出的商户" in s for s in app.statuses)


def test_do_export_can_skip_preflight():
    """定时任务等无人值守的调用方: 弹窗会把整批吊在那里。"""
    tasks = _tasks(("有赞", "A店", True))
    app = _ExportApp([])
    app._do_export(tasks, "2026-09-01", "2026-09-02", preflight=False)
    assert app.exported_with is tasks


class _JobApp:
    """trigger_job 的返回值会被 CronScheduler 用来决定要不要写 last_run, 一并守住。"""

    trigger_job = LiushuiApp.trigger_job

    def __init__(self, plat):
        self.platforms = {"youzan": plat}
        self.merchants = {"youzan": ["A店"]}
        self.logs = []
        self.ran = []

    def _preflight_login_check(self, tasks):
        raise AssertionError("定时任务不该弹预检框")

    def _execute_export_tasks(self, tasks, start, end, label="导出", step_debug=False):
        self.ran.append([(k, m) for k, _p, m in tasks])
        return (len(tasks), 0, 0)

    def _run_async(self, target, notify_busy=True):
        target()
        return True

    def _append_log(self, line):
        self.logs.append(line)


class _Job:
    name = "每天导昨天"
    platforms = ["youzan"]
    merchants = ["A店"]


def test_scheduled_job_exports_without_preflight(monkeypatch):
    monkeypatch.setattr(mg, "log", lambda *a, **k: None)
    app = _JobApp(_Plat("有赞"))
    assert app.trigger_job(_Job()) is True
    assert app.ran == [[("youzan", "A店")]]


# ===== 「检查登录状态」按钮: 换了探测实现, 日志字串和灯不能变 =====

class _CheckApp:
    _do_check = LiushuiApp._do_check
    _probe_login = LiushuiApp._probe_login
    _aborted = LiushuiApp._aborted

    def __init__(self):
        self._abort = threading.Event()
        self.logs = []
        self.lights = []

    def _ensure_browser(self, plat=None, merchant="", force_visible=False,
                        force_headless=False):
        self.browser = _Browser()
        assert force_headless is False, "手动点「检查登录状态」仍按设置的窗口可见性"

    def _append_log(self, line):
        self.logs.append(line)

    def _set_status(self, text):
        pass

    def _set_progress(self, **kwargs):
        pass

    def set_platform_status(self, key, status):
        self.lights.append((key, status))


def test_check_status_button_logs_as_before():
    app = _CheckApp()
    plat = _Plat("有赞", login=False)
    app._do_check([("youzan", plat, "A店"), ("youzan", _Plat("有赞", True), "B店")])
    assert "  有赞(A店): 未登录" in app.logs
    assert "  有赞(B店): 已登录 (后台首页)" in app.logs
    assert app.lights == [("youzan", "error"), ("youzan", "ok")]


def test_check_status_reports_probe_failure(monkeypatch):
    app = _CheckApp()
    boom = _Plat("有赞", login=RuntimeError("页面还没渲染完"))
    app._do_check([("youzan", boom, "A店")])
    assert "  有赞(A店): 检查失败 - 页面还没渲染完" in app.logs
    assert app.lights == [("youzan", "error")]
    assert boom.checked == 1


# ===== 首次登录现在把逐家结论交回调用方 =====

class _LoginApp:
    _do_login = LiushuiApp._do_login
    _aborted = LiushuiApp._aborted
    LOGIN_RETRY_LIMIT = 3

    def __init__(self, verdicts):
        self._abort = threading.Event()
        self.verdicts = list(verdicts)

    def _set_status(self, text):
        pass

    def _set_progress(self, **kwargs):
        pass

    def set_platform_status(self, key, status):
        pass

    def _append_log(self, line):
        pass

    def _login_one_round(self, plat, key, merchant):
        return self.verdicts.pop(0)

    def _ask_login_retry(self, plat, merchant, attempt):
        return False


def test_do_login_returns_verdicts_aligned_with_tasks():
    got = _LoginApp([True, False, None])._do_login(
        _tasks(("有赞", "A店", True), ("天猫", "B店", True), ("京东", "C店", True)))
    assert got == ["ok", "bad", "unknown"]
