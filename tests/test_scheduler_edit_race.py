"""定时任务对话框 vs 调度线程 30 秒重载: 编辑不许静默丢失。

回归的机理: `CronScheduler.check_all` 每 30 秒 `store.load()` 一次, 而旧实现每次都
把 `store.jobs` 换成**全新对象**。点「编辑」时对话框抓住的是旧对象, 用户改商户名、
挑平台很容易超过 30 秒 → 保存时 `apply_edit` 改的是孤儿对象, `save()` 序列化的是新
对象 → 改动丢失、界面显示回旧值、日志零提示, 到点仍按老商户导出(账单发错人)。

两道一起补: ① `TaskStore.load()` 对同一 job_id 复用内存里已有的对象(只把盘上字段
搬进去); ② 保存这一刻按 job_id 回查, 查不到就说明任务已被删/被外部改过, 明确拒绝
而不是把它复活。
"""
import json
import tkinter as tk
from types import SimpleNamespace

import pytest

import core.scheduler as sch
from core.scheduler import CronJob, JobEditDialog, TaskStore


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("这台机器起不了 Tk")
    r.withdraw()
    yield r
    r.destroy()


class _Recorder:
    """替掉 messagebox: 记下来问了什么, 一律"仍要保存"。"""

    def __init__(self):
        self.calls = []

    def askyesno(self, title, msg, **kw):
        self.calls.append(("askyesno", title))
        return True

    def showwarning(self, title, msg, **kw):
        self.calls.append(("showwarning", title))

    def showinfo(self, title, msg, **kw):
        self.calls.append(("showinfo", title))


@pytest.fixture()
def box(tmp_path, monkeypatch):
    store = TaskStore(str(tmp_path / "tasks.json"))
    job = CronJob.new(name="每天九点", cron="0 9 * * *", platforms=["youzan"],
                      merchants=["旗舰店A"])
    store.jobs = [job]
    store.save()
    app = SimpleNamespace(platforms={"youzan": SimpleNamespace(name="有赞")},
                          merchants={"youzan": ["旗舰店A", "新店B"]})
    sched = SimpleNamespace(app=app, store=store)
    rec = _Recorder()
    monkeypatch.setattr(sch, "messagebox", rec)
    return store, sched, rec, str(tmp_path / "tasks.json")


def _disk(path):
    return json.load(open(path, encoding="utf-8"))["jobs"]


def test_load_reuses_the_same_object_for_the_same_id(box):
    store, _, _, _ = box
    held = store.jobs[0]
    store.load()
    assert store.jobs[0] is held, "同一 job_id 必须复用对象, 否则外部持有的引用会变孤儿"


def test_disk_content_still_wins_on_reload(box):
    """保住引用不能变成"吞掉盘上改动": 外部改过 json, 内存对象要跟着变。"""
    store, _, _, path = box
    held = store.jobs[0]
    data = _disk(path)
    data[0]["cron"] = "0 18 * * *"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "jobs": data}, f, ensure_ascii=False)
    store.load()
    assert store.jobs[0] is held and held.cron == "0 18 * * *"


def test_edit_saved_after_a_reload_is_not_lost(box, root):
    """主用例: 对话框开着的时候调度线程 load() 了一轮, 保存必须真落盘。"""
    store, sched, _, path = box
    dlg = JobEditDialog(root, sched, job=store.jobs[0])
    store.load()                             # = 调度线程这 30 秒里跑了一轮 check_all
    dlg.name_var.set("每天九点")
    dlg.merch_var.set("新店B")
    dlg._save()
    on_disk = _disk(path)[0]
    assert on_disk["merchants"] == ["新店B"], f"改动丢了: 盘上还是 {on_disk['merchants']}"


def test_edit_of_a_deleted_task_is_refused_not_resurrected(box, root):
    """任务在编辑期间被删掉 → 保存要问清楚并放弃, 不许把它写回盘上复活。"""
    store, sched, rec, path = box
    held = store.jobs[0]
    dlg = JobEditDialog(root, sched, job=held)
    store.jobs = []                          # 另一处删掉了它(并保存)
    store.save()
    dlg.name_var.set("改名了")
    dlg._save()
    assert ("showwarning", "任务已变化") in rec.calls, rec.calls
    assert _disk(path) == [], "被删的任务不许被这次保存复活"
    dlg.destroy()
