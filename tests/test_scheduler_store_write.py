"""任务文件写不进盘: 不许演变成"每 30 秒重跑一整批", 也不许一声不响。

机理(实测复现): `trigger()` 把 last_run 交给 `TaskStore.save()`, 而旧实现的
`except Exception: pass` 让写失败完全看不见。文件被云盘同步/Excel/杀软占住时, 下一轮
`check_all` 的 load() 又读回**旧的那份** last_run → 判成"仍然到期" → 同一任务每 30 秒
重跑一遍, 浏览器反复起来, 用户只能杀进程。

补的是两件事: ① 写失败时把起算点兜在内存里(`mark_triggered` + `trigger_floor`);
② 失败必须说一次(调度日志一次 + 界面保存当场弹) —— "点了保存却没落盘"和"任务没跑"
一样会让人误判。
"""
import json
import os
import tkinter as tk
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import core.config as cfg
import core.logger as lg
import core.scheduler as sch
from core.scheduler import CronJob, CronScheduler, JobEditDialog, TaskStore


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("这台机器起不了 Tk")
    r.withdraw()
    yield r
    r.destroy()


class _Gate:
    """一个开关控制"盘能不能写", 免得测试里 monkeypatch.undo() 把别的补丁也撤掉。"""

    def __init__(self, real):
        self.real = real
        self.blocked = False

    def __call__(self, src, dst):
        if self.blocked:
            raise PermissionError(13, "另一个程序正在使用此文件")
        return self.real(src, dst)


class _App:
    def __init__(self, by_merchant=False):
        self.started = []
        self.by_merchant = by_merchant

    def trigger_job(self, job):
        if self.by_merchant and job.merchants:
            self.started.append(job.merchants[0])
        else:
            self.started.append(job.name)
        return True


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    logs = []
    monkeypatch.setattr(lg, "log", lambda msg, level="info", **kw: logs.append(f"{level}|{msg}"))
    gate = _Gate(os.replace)
    monkeypatch.setattr(cfg.os, "replace", gate)
    # 真代码对瞬时占用是有界重试的(Windows 语义); 这里要的正是"一直失败"那种结局,
    # 不嫌掉重试次数, 免得每条用例白睡一秒多。
    monkeypatch.setattr(cfg, "_REPLACE_TRIES", 1)
    path = str(tmp_path / "scheduled_tasks.json")
    store = TaskStore(path)
    assert store.save([CronJob(job_id="j1", name="每天九点", cron="0 9 * * *",
                               platforms=["youzan"], merchants=["旗舰店A"])]) is True
    app = _App()
    sched = CronScheduler(app=app, store=store, poll_interval=10)
    return SimpleNamespace(store=store, sched=sched, app=app, logs=logs,
                           path=path, gate=gate)


def _disk_job(path):
    return json.load(open(path, encoding="utf-8"))["jobs"][0]


def test_unwritable_store_does_not_retrigger_the_batch(rig):
    """主用例: 写不进盘时, 一批任务只能跑一次, 并且要说一次。"""
    rig.gate.blocked = True
    start = datetime(2026, 9, 26, 9, 0)
    for i in range(5):
        rig.sched.check_all(start + timedelta(seconds=30 * i))
    assert rig.app.started == ["每天九点"], f"被重复触发了 {len(rig.app.started)} 次"
    warns = [l for l in rig.logs if "写不进去" in l]
    assert len(warns) == 1, f"该说一次的事说了 {len(warns)} 次: {warns}"


def test_mem_floor_retires_once_the_disk_accepts_writes(rig):
    """盘腾开之后: 起算点真的落到盘上, 内存兜底退场, 下一个点位照常再跑。"""
    rig.gate.blocked = True
    rig.sched.check_all(datetime(2026, 9, 26, 9, 0))
    assert rig.store.jobs[0].mem_last_run, "写不进盘时应该在**任务对象自己身上**兜一份"

    rig.gate.blocked = False
    assert rig.store.save() is True and rig.store.last_error is None
    assert _disk_job(rig.path)["last_run"].startswith("2026-09-26T09:00")

    rig.sched.check_all(datetime(2026, 9, 27, 9, 1))          # 第二天到点
    assert rig.app.started == ["每天九点", "每天九点"]


def test_two_jobs_with_the_same_name_and_cron_both_run(tmp_path, monkeypatch):
    """回归: 兜底记号以前是 store 里的 "名字|cron" 字典 —— 界面不查任务重名, 于是两条
    同名同 cron、商户不同的任务会互相挡, 实测后一家当天永不导、盘腾开那一轮也不补。"""
    logs = []
    monkeypatch.setattr(lg, "log", lambda msg, level="info", **kw: logs.append(msg))
    gate = _Gate(os.replace)
    monkeypatch.setattr(cfg.os, "replace", gate)
    monkeypatch.setattr(cfg, "_REPLACE_TRIES", 1)
    store = TaskStore(str(tmp_path / "tasks.json"))
    a = CronJob(job_id="a", name="每天九点", cron="0 9 * * *", platforms=["youzan"], merchants=["A店"])
    b = CronJob(job_id="b", name="每天九点", cron="0 9 * * *", platforms=["youzan"], merchants=["B店"])
    assert store.save([a, b]) is True
    app = _App(by_merchant=True)          # 两条任务同名, 只能按商户分辨
    sched = CronScheduler(app=app, store=store, poll_interval=10)

    gate.blocked = True
    sched.check_all(datetime(2026, 9, 26, 9, 0))
    assert sorted(app.started) == ["A店", "B店"], f"只跑了一家: {app.started}"
    assert a.mem_last_run and b.mem_last_run, "两家各自兜一份, 不共用记号"
    gate.blocked = False
    sched.check_all(datetime(2026, 9, 26, 9, 1))              # 同一时间点不该再跑一遍
    assert sorted(app.started) == ["A店", "B店"], app.started


def test_disk_last_run_still_wins_when_it_is_newer(rig):
    """内存兜底只往"更晚"的方向兜, 不能把盘上更新的起算点顶掉(否则任务会被压住不跑)。"""
    job = rig.store.jobs[0]
    job.mem_last_run = "2026-09-20T09:00:00"
    job.last_run = "2026-09-26T09:00:00"
    assert rig.sched.should_trigger(job, datetime(2026, 9, 26, 9, 30)) is False


def test_dialog_says_when_the_file_could_not_be_written(rig, root):
    """界面保存: 没落盘就不能关窗、不能装作"已保存"。"""
    calls = []
    fake_box = SimpleNamespace(
        showwarning=lambda title, msg, **kw: calls.append((title, msg)),
        showinfo=lambda *a, **k: None,
        askyesno=lambda *a, **k: True)
    original_box = sch.messagebox
    sch.messagebox = fake_box                       # dlg._save 用的是模块属性
    try:
        dlg = JobEditDialog(root, SimpleNamespace(app=SimpleNamespace(
            platforms={"youzan": SimpleNamespace(name="有赞")},
            merchants={"youzan": ["旗舰店A"]}), store=rig.store), job=rig.store.jobs[0])
        rig.gate.blocked = True
        dlg.name_var.set("改了个名")
        dlg._save()
        assert calls and calls[0][0] == "定时任务没保存成功", calls
        assert dlg.winfo_exists(), "没写进盘就不该关窗, 得让用户腾开文件再点一次"
        assert _disk_job(rig.path)["name"] == "每天九点", "盘上仍是旧内容"
        dlg.destroy()
    finally:
        sch.messagebox = original_box
