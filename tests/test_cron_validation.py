"""cron 的两类"语法对、结果不对": 反向区间静默变成不限制 / 永不成立的日期静默不跑。

实测过的形状:
- `0 9 * * 5-1`(本意周五到周一): `range(5, 2)` 是空集, 而空集在 `_parse` 末尾被判成
  "这个字段不设限" → **周一到周日七天全命中**, 且 `cron_problem()` 也说没问题;
- `0 9 30 2 *`(2 月 30 日): 解析得动, `next_run` 把 52.7 万分钟的循环走满才返回 None
  (实测约 460ms) → 任务永远不跑, 调度线程每 30 秒白烧一次 CPU, 同样一声不响。

现在: 反向区间与越界步长当场 `ValueError`(界面拦住 + 调度线程说一次), 不存在的日期由
`impossible_date()` 给出人话, "一年无解"的表达式记进 `_NEVER_MATCHING` 不再重扫。
"""
import json
import time
import tkinter as tk
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import core.logger as lg
import core.scheduler as sch
from core.scheduler import CronExpr, CronJob, CronScheduler, JobEditDialog, TaskStore


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("这台机器起不了 Tk")
    r.withdraw()
    yield r
    r.destroy()


def _days(expr, week_of=2026):
    """返回该表达式在 2026-09-21(周一)那一周里命中的星期名。"""
    e = CronExpr(expr)
    out = []
    for i in range(7):
        d = datetime(2026, 9, 21) + timedelta(days=i)
        if e.match(d.replace(hour=9, minute=0)):
            out.append(d.strftime("%a"))
    return out


# ===== 反向区间 / 越界步长 =====

@pytest.mark.parametrize("expr", ["0 9 * * 5-1", "0 0 1 11-2 *", "0 9 * 12-1 *"])
def test_reversed_range_is_rejected(expr):
    with pytest.raises(ValueError) as e:
        CronExpr(expr)
    assert "区间起点不能大于终点" in str(e.value), e.value


def test_reversed_range_used_to_match_every_day():
    """这条钉住"为什么算问题": 修好之前 5-1 会被折成完全不设限(七天全命中)。"""
    wrap = CronExpr("0 9 * * 5-6,0-1")          # 想跨周末就明写成两段
    hit = [d.strftime("%a") for i in range(7)
           for d in [datetime(2026, 9, 21) + timedelta(days=i)]
           if wrap.match(d.replace(hour=9, minute=0))]
    assert hit == ["Mon", "Fri", "Sat", "Sun"], hit
    assert _days("0 9 * * 1-5") == ["Mon", "Tue", "Wed", "Thu", "Fri"], "正常区间照旧"
    # 而"什么都不筛"正是 5-1 在修复前的行为 —— 用它自己的字面量证一下反向写法确实没被
    # 悄悄放过去(抛错即等价于"不会七天全命中")。
    with pytest.raises(ValueError):
        CronExpr("0 9 * * 5-1")


@pytest.mark.parametrize("expr", ["*/0 * * * *", "70/5 * * * *", "0 9-1 * * *"])
def test_bad_step_and_start_are_rejected(expr):
    with pytest.raises(ValueError):
        CronExpr(expr)


# ===== 永不成立的日期 =====

def test_feb_30_is_reported_as_impossible():
    assert CronExpr("0 9 30 2 *").impossible_date() == "2 月 30 日"
    job = CronJob(job_id="j", name="闰2月30", cron="0 9 30 2 *",
                  platforms=["youzan"], merchants=["A"])
    assert "永远不会到点" in job.cron_problem()


def test_leap_day_and_or_semantics_are_not_false_alarms():
    """0 9 29 2 * 是"四年一次"; 0 9 30 2 1 靠"每周一"会跑(cron 的日/周是 OR)。"""
    assert CronExpr("0 9 29 2 *").impossible_date() is None
    assert CronExpr("0 9 30 2 1").impossible_date() is None
    assert CronExpr("0 9 31 4 *").impossible_date() == "4 月 31 日"   # 4 月只有 30 天
    assert CronExpr("0 9 30 2 *").impossible_date() is not None
    assert CronExpr("0 9 * * *").impossible_date() is None


def test_never_matching_expressions_are_scanned_once():
    sch._NEVER_MATCHING.discard("0 9 30 2 1-0")      # 别的用例可能塞过, 先清干净
    e = CronExpr("0 9 31 2 *")                        # 2 月 31 日
    sch._NEVER_MATCHING.discard(e.expr)
    t0 = time.time()
    assert e.next_run(datetime(2026, 9, 26, 12, 0)) is None
    first = time.time() - t0
    t0 = time.time()
    assert CronExpr("0 9 31 2 *").next_run(datetime(2026, 9, 26, 12, 0)) is None
    second = time.time() - t0
    assert first > second, (first, second)
    assert second < 0.05, f"第二遍还在重扫年历: {second:.3f}s"


def test_scheduler_says_impossible_once_and_never_triggers(tmp_path, monkeypatch):
    logs = []
    monkeypatch.setattr(lg, "log", lambda msg, level="info", **kw: logs.append(msg))
    path = str(tmp_path / "tasks.json")
    store = TaskStore(path)
    assert store.save([CronJob(job_id="j1", name="二月三十", cron="0 9 30 2 *",
                               platforms=["youzan"], merchants=["A"])])
    calls = []
    sched = CronScheduler(app=SimpleNamespace(trigger_job=lambda j: calls.append(1)),
                          store=store, poll_interval=10)
    start = datetime(2026, 9, 26, 9, 0)
    for i in range(5):
        sched.check_all(start + timedelta(seconds=30 * i))
    assert calls == [], "永不成立的表达式不许触发"
    said = [l for l in logs if "永远不会到点" in l]
    assert len(said) == 1, f"该说一次的事说了 {len(said)} 遍: {said}"


# ===== 界面保存当场拦 =====

@pytest.fixture()
def box(tmp_path, monkeypatch):
    store = TaskStore(str(tmp_path / "tasks.json"))
    store.save([CronJob(job_id="j1", name="每天九点", cron="0 9 * * *",
                        platforms=["youzan"], merchants=["旗舰店A"])])
    app = SimpleNamespace(platforms={"youzan": SimpleNamespace(name="有赞")},
                          merchants={"youzan": ["旗舰店A"]})
    calls = []
    monkeypatch.setattr(sch, "messagebox", SimpleNamespace(
        showwarning=lambda title, msg, **kw: calls.append((title, msg)),
        showinfo=lambda *a, **k: None, askyesno=lambda *a, **k: True))
    return store, SimpleNamespace(app=app, store=store), calls, str(tmp_path / "tasks.json")


@pytest.mark.parametrize("cron,title", [
    ("0 9 * * 5-1", "cron 非法"),
    ("0 9 30 2 *", "cron 不会到点"),
])
def test_dialog_refuses_the_two_bad_shapes(box, root, cron, title):
    store, sched, calls, path = box
    dlg = JobEditDialog(root, sched, job=store.jobs[0])
    dlg.cron_var.set(cron)
    dlg._save()
    assert calls and calls[0][0] == title, calls
    assert json.load(open(path, encoding="utf-8"))["jobs"][0]["cron"] == "0 9 * * *", \
        "拦下来就不许把坏表达式写进盘"
