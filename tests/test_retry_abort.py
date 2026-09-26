"""「中止」要能真的把重试停下来 —— `run_with_retry` 以前完全不看中止。

实测的形状: 按下「中止」后, 当前那一家仍会把默认 2 次重试跑满, 中间还要睡
30 + 60 = 90 秒, 界面上"正在中止…"就那么挂着几分钟, 剩下的商户要等这一整轮走完
才在外层循环 break 掉。第 12 章第 14 条"中止只在边界生效"讲的是不许中途掐浏览器,
而两次尝试之间的等待正是边界 —— 该掐的是这个等待。

三件事分开钉: 决定重试前看一眼 / 长等待分段睡每片看一眼 / 两条调用路径(批量与
子商户)都把 `self._aborted` 传进去。没传 aborted 的老调用行为逐字不变。
"""
import threading
import time

import pytest

import core.main_gui as mg
from core.main_gui import LiushuiApp, run_with_retry


class _Plat:
    def __init__(self, name):
        self.name = name
        self.manual_intervention = False
        self.intervention_hint = ""
        self.guide = ""


TASKS = [("a", _Plat("平台A"), "商户A"), ("b", _Plat("平台B"), "商户B")]


class _BatchApp:
    """跑真实 `_execute_export_tasks` 的循环, 不碰浏览器也不建窗口。"""

    _execute_export_tasks = LiushuiApp._execute_export_tasks
    _aborted = LiushuiApp._aborted
    _apply_export_result = LiushuiApp._apply_export_result

    def __init__(self, results=None, abort_after=None):
        self._abort = threading.Event()
        self.logs = []
        self.export_calls = 0
        self.statuses = []
        self._results = list(results or [])
        self._abort_after = abort_after

    def _prompt_manual_leftovers(self, items, date_str):
        self.manual_items = list(items)

    def _append_log(self, line):
        self.logs.append(line)

    def _ui(self, fn):
        fn()

    def _set_status(self, text):
        pass

    def _set_progress(self, **kwargs):
        pass

    def set_platform_status(self, key, status):
        self.statuses.append((key, status))

    def _run_single_export(self, plat, merchant, start_date, end_date, step_debug=False):
        self.export_calls += 1
        if self._abort_after is not None and self.export_calls >= self._abort_after:
            self._abort.set()
        return self._results.pop(0) if self._results else "success"


class _SubApp:
    """跑真实 `_run_sub_merchants` 的循环。"""

    _run_sub_merchants = LiushuiApp._run_sub_merchants
    _aborted = LiushuiApp._aborted

    def __init__(self, abort_after=None):
        self._abort = threading.Event()
        self.logs = []
        self.calls = []
        self.switches = 0
        self._abort_after = abort_after

    def _append_log(self, line):
        self.logs.append(line)

    def _switch_sub_merchant(self, plat, merchant, sub):
        self.switches += 1
        return True

    def _export_one_merchant(self, plat, merchant, start_date, end_date, step_debug=False,
                             sub_merchant=""):
        self.calls.append(sub_merchant)
        if self._abort_after is not None and len(self.calls) >= self._abort_after:
            self._abort.set()
        return "failed"


@pytest.fixture()
def fast(monkeypatch):
    """重试次数 2、间隔 0: 不真睡, 也不把排好的返回值吃光。"""
    monkeypatch.setattr(mg, "load_settings",
                        lambda: {"retry_times": 2, "retry_interval_s": 0})
    return None


# ===== 函数本体 =====

def test_abort_before_retry_stops_immediately():
    calls = []
    logs = []
    out = run_with_retry(lambda: calls.append(1) or "failed", retry_times=3,
                         retry_interval_s=0, log=logs.append, aborted=lambda: True)
    assert out == "failed"
    assert len(calls) == 1, "已经中止了就不该再试第二次"
    assert any("不再重试" in l for l in logs), logs


def test_long_wait_is_cut_mid_sleep(monkeypatch):
    """等待要**分段睡**: 30 秒的间隔不该一睡到底。"""
    naps = []
    monkeypatch.setattr(mg.time, "sleep", lambda s: naps.append(s))
    state = {"seen": 0}

    def aborted():
        state["seen"] += 1
        return state["seen"] >= 2          # 第二片上用户按了中止

    calls = []
    logs = []
    out = run_with_retry(lambda: calls.append(1) or "failed", retry_times=2,
                         retry_interval_s=30, log=logs.append, aborted=aborted)
    assert out == "failed"
    assert len(calls) == 1
    assert len(naps) < 5, f"睡了 {len(naps)} 片, 等待没被掐断"
    assert any("等待重试期间收到中止请求" in l for l in logs), logs


def test_without_abort_flag_the_old_shape_is_kept():
    """没传 aborted 的老调用: 照旧跑满重试, 一次都不少。"""
    calls = []
    t0 = time.time()
    out = run_with_retry(lambda: calls.append(1) or "failed", retry_times=2,
                         retry_interval_s=0, log=None)
    assert out == "failed"
    assert len(calls) == 3                  # 首次 + 2 次重试
    assert time.time() - t0 < 1.0


def test_success_on_retry_is_not_retried_again():
    results = iter(["failed", "success"])
    calls = []

    def fn():
        calls.append(1)
        return next(results)
    assert run_with_retry(fn, retry_times=3, retry_interval_s=0,
                          aborted=lambda: False) == "success"
    assert len(calls) == 2


# ===== 两条调用路径的接线 =====

def test_batch_loop_stops_retrying_after_abort(fast):
    app = _BatchApp(results=["failed", "failed", "failed"], abort_after=1)
    app._execute_export_tasks(list(TASKS), "2026-09-01", "2026-09-02")
    assert app.export_calls == 1, f"第一家中止后还被重试了 {app.export_calls} 次"
    assert any("[中止] 已请求中止, 这一家不再重试" in l for l in app.logs), app.logs
    assert any("剩余 1 项未执行" in l for l in app.logs), app.logs


def test_sub_merchant_loop_stops_retrying_after_abort(fast):
    app = _SubApp(abort_after=1)
    plat = _Plat("银联")
    plat.supports_sub_merchants = True
    out = app._run_sub_merchants(plat, "主账号甲", ["8234000540", "8234000541"],
                                 "2026-09-01", "2026-09-02", False)
    assert len(app.calls) == 1, f"第一家被重试了 {len(app.calls)} 次"
    assert out == "manual"                  # 整家汇总永不是 failed(12 章第 35 条)
    assert any("这一家不再重试" in l for l in app.logs), app.logs
    assert app.switches == 1, "第二家不该再切上去"
