# 批3：cron 定时任务 + 失败自动重试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持用户配置 cron 表达式的定时导出任务（后台触发、持久化到 `scheduled_tasks.json`），并在导出失败时按设定次数/间隔自动重试。

**Architecture:** 新增 `core/scheduler.py`：`CronExpr`（轻量 5 字段解析+匹配+next 计算）、`CronJob`/`TaskStore`（JSON 持久化）、`CronScheduler`（后台线程轮询触发，`app.trigger_job` 回调）、`SchedulerDialog`（Tkinter UI）。重试逻辑通过把 `_do_export` 的任务循环提炼为 `_execute_export_tasks` 实现，单任务失败按 `retry_times`/`retry_interval_s`（settings）重试。

**Tech Stack:** Python 3.10+、Tkinter、pytest（dev）、Playwright（现有）。

**说明：** 仓库已有批1/批2 提交，工作树干净。每 Task 独立提交；pytest 前设 `$env:PYTHONDONTWRITEBYTECODE=1` 规避 pyc 沙箱噪音；「并行 Edit 偶发未落盘」——每个 Edit 后 Read/rg 复核落盘。定时任务仅程序运行期间生效，错过的时间戳在下次启动/轮询时跳过（通过 `should_trigger` 基于 last_run 的 next_run 判断）。

**参考 spec：** `docs/superpowers/specs/2026-09-04-liushui-export-optimization-design.md` §5 与附录 A。

---

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `core/scheduler.py` | Create | `CronExpr` / `CronJob` / `TaskStore` / `CronScheduler` / `SchedulerDialog` |
| `core/config.py` | Modify | `SCHEDULED_TASKS_FILE` 常量；`DEFAULT_SETTINGS` 增 `retry_times`/`retry_interval_s` |
| `core/main_gui.py` | Modify | `_execute_export_tasks` 提炼（含重试）；`trigger_job`；`_open_scheduler_dialog`；重试设置 UI 行；CronScheduler 生命周期挂接 |
| `tests/test_scheduler.py` | Create | CronExpr / TaskStore / CronScheduler TDD |
| `.gitignore` | Modify | 增 `/scheduled_tasks.json` |
| `CODE_WIKI.md` | Modify | 批3 文档同步 |

---

### Task 1: CronExpr（解析 + match + next_run）TDD

**Files:**
- Create: `core/scheduler.py`（本 Task 只含 CronExpr；后续 Task 追加其余类）
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_scheduler.py
from datetime import datetime

import pytest

from core.scheduler import CronExpr


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def test_every_minute_matches_always():
    e = CronExpr("* * * * *")
    assert e.match(_dt("2026-09-04 18:00")) is True
    assert e.match(_dt("2026-09-04 23:59")) is True


def test_hourly_field():
    e = CronExpr("0 9 * * *")
    assert e.match(_dt("2026-09-04 09:00")) is True
    assert e.match(_dt("2026-09-04 09:01")) is False


def test_step_field():
    e = CronExpr("*/30 * * * *")
    assert e.match(_dt("2026-09-04 18:00")) is True
    assert e.match(_dt("2026-09-04 18:30")) is True
    assert e.match(_dt("2026-09-04 18:15")) is False


def test_weekday_field_uses_or_weekday():
    e = CronExpr("0 9 * * 1-5")  # 周一~五 9 点
    assert e.match(_dt("2026-09-03 09:00")) is True   # 周四
    assert e.match(_dt("2026-09-05 09:00")) is False  # 周六
    assert e.match(_dt("2026-09-06 09:00")) is False  # 周日


def test_day_of_month_field():
    e = CronExpr("0 9 1 * *")
    assert e.match(_dt("2026-09-01 09:00")) is True
    assert e.match(_dt("2026-09-02 09:00")) is False


def test_next_run():
    assert CronExpr("0 9 * * *").next_run(_dt("2026-09-04 18:00")) == _dt("2026-09-05 09:00")
    assert CronExpr("*/30 * * * *").next_run(_dt("2026-09-04 18:05")) == _dt("2026-09-04 18:30")
    assert CronExpr("0 9 1 * *").next_run(_dt("2026-09-04 18:00")) == _dt("2026-10-01 09:00")


def test_invalid_expr_raises():
    with pytest.raises(ValueError):
        CronExpr("0 9 * *")          # 只有 4 字段
    with pytest.raises(ValueError):
        CronExpr("70 * * * *")       # 分钟越界
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_scheduler.py -v`
Expected: `ModuleNotFoundError: core.scheduler` → collection error，全部 FAIL。

- [ ] **Step 3: 实现 CronExpr**

```python
# core/scheduler.py (Task 1 部分)
"""
定时任务模块 - cron 表达式解析与调度
- CronExpr: 轻量 5 字段 cron 解析(支持 *、*/n、a-b、a,b、? ),不引入第三方依赖
- CronJob / TaskStore: 任务模型与 scheduled_tasks.json 持久化
- CronScheduler: 后台线程轮询触发; SchedulerDialog: 定时任务管理界面
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta


class CronExpr:
    """5 字段 cron 表达式: 分 时 日 月 周(0=周日)
    支持 *  */n  a-b  a,b  ?(日/周字段中视为 *)
    day-of-month 与 day-of-week 同时受限时为 OR 语义(标准 cron)。
    """

    def __init__(self, expr):
        parts = str(expr).split()
        if len(parts) != 5:
            raise ValueError("cron 表达式必须为 5 个字段(分 时 日 月 周)")
        self.minute = self._parse(parts[0], 0, 59)
        self.hour = self._parse(parts[1], 0, 23)
        self.day = self._parse(parts[2], 1, 31)
        self.month = self._parse(parts[3], 1, 12)
        self.week = self._parse(parts[4], 0, 6)
        if self.week:  # cron 0/7=周日 -> Python weekday()==6
            self.week = {6 if v in (0, 7) else v for v in self.week}

    @staticmethod
    def _parse(token, lo, hi):
        if token in ("*", "?"):
            return None
        values = set()
        for part in str(token).split(","):
            if "/" in part:
                base, step = part.split("/")
                start = lo if base in ("*", "?") else int(base)
                values.update(range(start, hi + 1, int(step)))
            elif "-" in part:
                a, b = map(int, part.split("-"))
                if a > hi or b > hi or a < lo or b < lo:
                    raise ValueError(f"取值范围越界: {part}")
                values.update(range(a, b + 1))
            else:
                v = int(part)
                if v < lo or v > hi:
                    raise ValueError(f"取值范围越界: {part}")
                values.add(v)
        return values or None

    def match(self, dt):
        if self.minute is not None and dt.minute not in self.minute:
            return False
        if self.hour is not None and dt.hour not in self.hour:
            return False
        if self.month is not None and dt.month not in self.month:
            return False
        dom_ok = self.day is None or dt.day in self.day
        dow_ok = self.week is None or dt.weekday() in self.week
        if self.day is not None and self.week is not None:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def next_run(self, after=None):
        """返回 after(默认当前时间)之后的下一个匹配时间(分钟级); 一年内无则返回 None。"""
        base = (after or datetime.now()).replace(second=0, microsecond=0)
        nxt = base + timedelta(minutes=1)
        for _ in range(527040):  # 一年(365天 有闰年余量)
            if self.match(nxt):
                return nxt
            nxt += timedelta(minutes=1)
        return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_scheduler.py -v`
Expected: 7 个用例全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): cron expression parse/match/next-run"
```

---

### Task 2: CronJob + TaskStore（JSON 持久化）TDD

**Files:**
- Modify: `core/scheduler.py`（追加 CronJob / TaskStore）
- Test: `tests/test_scheduler.py`（追加用例）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_scheduler.py` 末尾追加：

```python
import json

from core.scheduler import CronJob, TaskStore


def test_cronjob_roundtrip():
    job = CronJob(job_id="abc", name="每日导出", cron="0 9 * * *",
                  platforms=["youzan"], merchants=["旗舰店A"],
                  enabled=True, last_run="2026-09-01T09:00:00")
    d = job.to_dict()
    assert d["name"] == "每日导出"
    assert d["platforms"] == ["youzan"]
    job2 = CronJob.from_dict(d)
    assert job2.cron == "0 9 * * *"
    assert job2.last_run == "2026-09-01T09:00:00"


def test_taskstore_save_load(tmp_path):
    path = str(tmp_path / "tasks.json")
    store = TaskStore(path)
    store.jobs = [CronJob(job_id="x1", name="A", cron="* * * * *",
                          platforms=["p"], merchants=["m"])]
    store.save()
    store2 = TaskStore(path)
    assert len(store2.load()) == 1
    assert store2.load()[0].name == "A"


def test_taskstore_ignores_corrupt_file(tmp_path):
    path = str(tmp_path / "tasks.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{broken json")
    store = TaskStore(path)
    assert store.load() == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_scheduler.py -v`
Expected: 新增 3 用例因 `NameError: CronJob` FAIL，原 7 用例 PASS。

- [ ] **Step 3: 实现 CronJob / TaskStore**

追加到 `core/scheduler.py`：

```python
class CronJob:
    """定时导出任务。"""

    def __init__(self, job_id, name, cron, platforms, merchants,
                 enabled=True, last_run=None):
        self.job_id = job_id
        self.name = name
        self.cron = cron
        self.platforms = list(platforms)
        self.merchants = list(merchants)
        self.enabled = bool(enabled)
        self.last_run = last_run  # ISO 字符串或 None

    def to_dict(self):
        return {
            "id": self.job_id,
            "name": self.name,
            "cron": self.cron,
            "platforms": self.platforms,
            "merchants": self.merchants,
            "enabled": self.enabled,
            "last_run": self.last_run,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(job_id=d.get("id") or uuid.uuid4().hex[:8],
                   name=d.get("name") or "",
                   cron=d.get("cron") or "* * * * *",
                   platforms=d.get("platforms") or [],
                   merchants=d.get("merchants") or [],
                   enabled=d.get("enabled", True),
                   last_run=d.get("last_run"))


class TaskStore:
    """scheduled_tasks.json 持久化。"""

    def __init__(self, path=None):
        self.path = path or _default_tasks_path()
        self.jobs = []

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            jobs = []
            for item in data.get("jobs", []):
                try:
                    jobs.append(CronJob.from_dict(item))
                except Exception:
                    continue  # 跳过损坏条目
            self.jobs = jobs
        except Exception:
            self.jobs = []
        return self.jobs

    def save(self, jobs=None):
        if jobs is not None:
            self.jobs = jobs
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "jobs": [j.to_dict() for j in self.jobs]},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def _default_tasks_path():
    from core.config import SCHEDULED_TASKS_FILE
    return SCHEDULED_TASKS_FILE
```

- [ ] **Step 4: 运行测试确认通过**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_scheduler.py -v`
Expected: 10 个用例全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): cron job model and json task store"
```

---

### Task 3: CronScheduler 触发逻辑 TDD

**Files:**
- Modify: `core/scheduler.py`（追加 CronScheduler）
- Test: `tests/test_scheduler.py`（追加用例）

- [ ] **Step 1: 追加失败测试**

追加：

```python
from datetime import datetime, timedelta

from core.scheduler import CronScheduler


def _job(name="t", cron="* * * * *", last_run=None, enabled=True):
    return CronJob(job_id="j1", name=name, cron=cron,
                   platforms=["youzan"], merchants=["m1"],
                   enabled=enabled, last_run=last_run)


def test_should_trigger_when_next_run_passed():
    now = datetime(2026, 9, 4, 18, 0)
    s = CronScheduler(app=object())
    job = _job(cron="*/5 * * * *", last_run="2026-09-04T17:50:00")
    assert s.should_trigger(job, now) is True


def test_should_not_trigger_when_not_due():
    now = datetime(2026, 9, 4, 18, 0)
    s = CronScheduler(app=object())
    job = _job(cron="*/5 * * * *", last_run="2026-09-04T18:00:00")
    assert s.should_trigger(job, now) is False


def test_should_not_trigger_when_disabled():
    now = datetime(2026, 9, 4, 18, 0)
    s = CronScheduler(app=object())
    job = _job(enabled=False, last_run=None)
    assert s.should_trigger(job, now) is False


def test_check_all_triggers_due_job_and_updates_last_run(tmp_path):
    calls = []

    class FakeApp:
        def trigger_job(self, job):
            calls.append(job.name)

    store = TaskStore(str(tmp_path / "t.json"))
    now = datetime(2026, 9, 4, 18, 0)
    job = _job(name="due", cron="* * * * *", last_run=None)
    store.save([job])
    s = CronScheduler(app=FakeApp(), store=store)
    s.check_all(now)
    assert calls == ["due"]
    assert store.load()[0].last_run == now.isoformat()


def test_check_all_skips_undue_job(tmp_path):
    calls = []

    class FakeApp:
        def trigger_job(self, job):
            calls.append(job.name)

    store = TaskStore(str(tmp_path / "t.json"))
    job = _job(name="ok", cron="0 9 * * *", last_run="2026-09-04T09:00:00")
    store.save([job])
    s = CronScheduler(app=FakeApp(), store=store)
    now = datetime(2026, 9, 4, 18, 0)
    s.check_all(now)
    assert calls == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_scheduler.py -v`
Expected: 新增 5 用例因 `NameError: CronScheduler` FAIL，原 10 用例 PASS。

- [ ] **Step 3: 实现 CronScheduler**

追加到 `core/scheduler.py`：

```python
class CronScheduler:
    """后台线程轮询已启用任务, 到点通过 app.trigger_job(job) 触发。
    触发后更新 last_run 并保存; 错过的触发在下次轮询自动跳过(不补跑)。"""

    def __init__(self, app, store=None, poll_interval=30):
        self.app = app
        self.store = store or TaskStore()
        self.poll_interval = max(10, int(poll_interval))
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.wait(self.poll_interval):
            try:
                self.check_all(datetime.now())
            except Exception:
                continue

    def check_all(self, now):
        """轮询一轮: 触发所有到期任务。now 可注入便于测试。"""
        self.store.load()
        for job in self.store.jobs:
            if self.should_trigger(job, now):
                self.trigger(job, now)

    def should_trigger(self, job, now):
        if not job.enabled:
            return False
        try:
            expr = CronExpr(job.cron)
        except ValueError:
            return False
        last = None
        if job.last_run:
            try:
                last = datetime.fromisoformat(job.last_run)
            except ValueError:
                last = None
        nxt = expr.next_run(last)
        return nxt is not None and nxt <= now

    def trigger(self, job, now):
        try:
            self.app.trigger_job(job)
        finally:
            job.last_run = now.isoformat()
            self.store.save()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_scheduler.py -v`
Expected: 15 个用例全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): background trigger scheduler"
```

---

### Task 4: 失败自动重试（导出执行循环提炼 + settings）

**Files:**
- Modify: `core/config.py`（DEFAULT_SETTINGS 增 retry 项）
- Modify: `core/main_gui.py`（提炼 `_execute_export_tasks` / `_run_single_export`，`_do_export` 改用；含重试）
- Test: `tests/test_retry.py`

- [ ] **Step 1: 写失败测试（重试辅助函数）**

```python
# tests/test_retry.py
from core.main_gui import run_with_retry


def test_retry_until_success():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            return "failed"
        return "success"

    result = run_with_retry(flaky, retry_times=3, retry_interval_s=0,
                            log=lambda m: None)
    assert result == "success"
    assert len(calls) == 3


def test_retry_gives_up_after_limit():
    calls = []

    def always_fail():
        calls.append(1)
        return "failed"

    result = run_with_retry(always_fail, retry_times=2, retry_interval_s=0,
                            log=lambda m: None)
    assert result == "failed"
    assert len(calls) == 3  # 1 次原始 + 2 次重试


def test_no_retry_when_zero():
    calls = []

    def once():
        calls.append(1)
        return "failed"

    result = run_with_retry(once, retry_times=0, retry_interval_s=0,
                            log=lambda m: None)
    assert result == "failed"
    assert len(calls) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_retry.py -v`
Expected: `ImportError: cannot import name 'run_with_retry'` → FAIL。

- [ ] **Step 3: 实现 run_with_retry + 接入导出循环**

3a. 在 `core/main_gui.py` 模块级（`discover_merchants` 附近）新增：

```python
def run_with_retry(fn, retry_times=0, retry_interval_s=30, log=None):
    """通用重试: fn 返回非 "failed" 视为成功; 失败按 retry_times 重试, 间隔递增(×2)。
    retry_times=0 表示失败一次即返回, 不重试。"""
    interval = max(0, int(retry_interval_s))
    result = fn()
    attempt = 0
    while result == "failed" and attempt < retry_times:
        attempt += 1
        wait = interval * (2 ** (attempt - 1))
        if log:
            log(f"  [重试] 第 {attempt}/{retry_times} 次重试({wait}s 后)")
        time.sleep(wait)
        result = fn()
    return result
```

> 注意：`main_gui.py` 顶部已有 `import time`。若没有，先补。

3b. 重构 `_do_export`：把 for 循环体拆为两个方法（保持对外行为一致），并接入重试。

读取当前 `_do_export`（tasks 组装后 for 循环：`_ensure_browser` → `set_export_context` → `plat.export` → 结果分类统计 exported/manual/failed 与状态灯）。将其改造成：

```python
def _do_export(self, selected):
    date_str = f"{self.date_start.get()} 至 {self.date_end.get()}"
    start_date = self.date_start.get()
    end_date = self.date_end.get()
    tasks = []
    for key in selected:
        plat = self.platforms[key]
        for m in self._get_selected_merchants(key):
            tasks.append((key, plat, m))
    self._execute_export_tasks(tasks, start_date, end_date, label="导出")
    self._record_job_done("导出", start_date, end_date)

def _execute_export_tasks(self, tasks, start_date, end_date, label="导出"):
    """对 tasks((key,plat,merchant)) 依次执行导出(含失败重试), 日志/状态由这里统一驱动。"""
    if not tasks:
        self._append_log("[提示] 未勾选任何平台商户,请先添加/勾选商户。")
        return
    from core.config import load_settings
    st = load_settings()
    retry_times = int(st.get("retry_times", DEFAULT_SETTINGS.get("retry_times", 2)))
    retry_interval_s = int(st.get("retry_interval_s", DEFAULT_SETTINGS.get("retry_interval_s", 30)))
    date_str = f"{start_date} 至 {end_date}"
    self._set_status(f"正在导出({date_str})...")
    self.progress.config(maximum=len(tasks), value=0)
    exported = manual = failed = 0
    for i, (key, plat, merchant) in enumerate(tasks):
        self._append_log(f">>> 正在导出 {plat.name}({merchant}) 流水...")
        self._set_platform_status(key, "warn")
        try:
            result = run_with_retry(
                lambda k=key, p=plat, m=merchant: self._run_single_export(p, m, start_date, end_date),
                retry_times=retry_times,
                retry_interval_s=retry_interval_s,
                log=self._append_log,
            )
        except Exception as e:
            result = "failed"
            self._append_log(f"[失败] {plat.name} 导出异常: {e}")
        self._apply_export_result(key, plat, result, date_str)
        if result == "success":
            exported += 1
        elif result == "manual":
            manual += 1
        else:
            failed += 1
        self.progress.config(value=i + 1)
    self._append_log(f"导出流程完成: 成功{exported} / 手动{manual} / 失败{failed}")
    self._set_status(f"导出完成(成功{exported}/手动{manual}/失败{failed})")
    return exported, manual, failed

def _run_single_export(self, plat, merchant, start_date, end_date):
    """单个商户导出(给 run_with_retry 调用): 返回 "success"/"manual"/"failed"。"""
    from core.config import load_settings
    key = plat.key
    self._ensure_browser(plat, merchant)
    self.browser.set_export_context(plat.name, start_date, end_date, merchant)
    result = plat.export(self.browser, start_date, end_date)
    if result == "success":
        self._append_log(f"[完成] {plat.name} 流水导出成功")
    elif result == "manual":
        self._append_log(f"[提示] {plat.name} 需要手动完成导出")
    else:
        self._append_log(f"[失败] {plat.name} 导出失败")
    return result

def _apply_export_result(self, key, plat, result, date_str):
    """结果状态灯与弹窗(manual 时弹窗)。"""
    if result == "success":
        self._set_platform_status(key, "ok")
    elif result == "manual":
        self._set_platform_status(key, "warn")
        self.root.after(0, lambda n=plat.name, d=date_str, g=plat.guide: messagebox.showinfo(
            "请手动导出",
            f"【{n}】自动导出未完全成功\n\n日期范围: {d}\n操作指引: {g}\n\n请在浏览器中手动完成导出,完成后点击确定继续。"))
    else:
        self._set_platform_status(key, "error")

def _record_job_done(self, label, start_date, end_date):
    """导出任务收尾: 汇总文件与操作日志。"""
    log(f"{label}完成", callback=self._append_log)
    if self.progress["value"] >= 0:
        try:
            ts_dir = self._copy_export_outputs(start_date, end_date)
            if ts_dir:
                try:
                    os.startfile(ts_dir)
                    self._append_log(f"[汇总] 已自动打开文件夹: {ts_dir}")
                except Exception:
                    pass
        except Exception:
            pass
```

> 实施说明：替换 `_do_export` 的循环部分时，请先读取当前 `_do_export` 的准确实现，将原循环体思路映射到上述拆分（原「手动弹窗」逻辑移到 `_apply_export_result`，原文件汇总逻辑移到 `_record_job_done`）。若 `DEFAULT_SETTINGS` 尚未含 retry 键，先按 Step 3c 加上再引用 `DEFAULT_SETTINGS.get(...)`。

3c. `core/config.py` 的 `DEFAULT_SETTINGS` 追加：

```python
DEFAULT_SETTINGS = {
    "show_browser": True,
    "download_name_mode": "unified",
    "enable_keepalive": True,
    "keepalive_interval_min": 30,
    "retry_times": 2,          # 失败自动重试次数(0=不重试)
    "retry_interval_s": 30,    # 首次重试间隔秒(后续递增×2)
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests/test_retry.py -v`
Expected: 3 个用例 PASS。并运行 `python -m pytest tests -q` 确认原有用例（11 个）不受影响。

- [ ] **Step 5: 提交**

```bash
git add core/main_gui.py core/config.py tests/test_retry.py
git commit -m "feat(export): automatic retry on failed export"
```

---

### Task 5: UI 集成（定时任务面板 + 重试设置 + trigger_job）

**Files:**
- Modify: `core/scheduler.py`（追加 SchedulerDialog）
- Modify: `core/main_gui.py`（入口按钮、trigger_job、CronScheduler 挂接、重试设置行）
- Modify: `.gitignore`（增 scheduled_tasks.json）

- [ ] **Step 1: 追加 SchedulerDialog**

追加到 `core/scheduler.py`（顶部 import 区补 `import tkinter as tk`、`from tkinter import ttk, messagebox`）：

```python
class SchedulerDialog(tk.Toplevel):
    """定时任务管理: 列表 + 新增/编辑/删除/启停。"""

    def __init__(self, master, scheduler, rebuild_cb):
        super().__init__(master)
        self.scheduler = scheduler
        self.rebuild_cb = rebuild_cb  # 配置变化后刷新(保存 store)
        self.title("定时任务")
        self.geometry("560x400")
        self.transient(master)
        self.grab_set()
        btns = tk.Frame(self)
        btns.pack(fill=tk.X, padx=8, pady=6)
        for t, cmd in [("+ 新增", self._add), ("编辑", self._edit),
                       ("删除", self._delete), ("启用/停用", self._toggle)]:
            tk.Button(btns, text=t, command=cmd, font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=3)
        self.tree = ttk.Treeview(self, columns=("name", "cron", "objects", "enabled"),
                                 show="headings", height=12)
        for cid, txt, w in [("name", "任务名", 120), ("cron", "cron", 100),
                            ("objects", "对象", 180), ("enabled", "启用", 60)]:
            self.tree.heading(cid, text=txt)
            self.tree.column(cid, width=w, anchor="center")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8)
        self.tree.bind("<Double-1>", lambda e: self._edit())
        self._reload()

    def _reload(self):
        self.tree.delete(*self.tree.get_children())
        for job in self.scheduler.store.jobs:
            objs = f"{len(job.platforms)}平台 × {len(job.merchants)}商户"
            self.tree.insert("", "end", iid=job.job_id,
                             values=(job.name, job.cron, objs, "✓" if job.enabled else "✗"))

    def _save(self):
        self.scheduler.store.save()
        self.rebuild_cb()

    def _add(self):
        JobEditDialog(self, self.scheduler, job=None, on_saved=self._after)

    def _edit(self):
        sel = self.tree.selection()
        if not sel:
            return
        job = self._find(sel[0])
        JobEditDialog(self, self.scheduler, job=job, on_saved=self._after)

    def _find(self, job_id):
        for j in self.scheduler.store.jobs:
            if j.job_id == job_id:
                return j
        return None

    def _delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        self.scheduler.store.jobs = [j for j in self.scheduler.store.jobs
                                     if j.job_id != sel[0]]
        self._after()

    def _toggle(self):
        sel = self.tree.selection()
        if not sel:
            return
        job = self._find(sel[0])
        if job:
            job.enabled = not job.enabled
            self._after()

    def _after(self):
        self._save()
        self._reload()


class JobEditDialog(tk.Toplevel):
    """新建/编辑定时任务: 名称、cron、平台、商户。"""

    def __init__(self, master, scheduler, job=None, on_saved=None):
        super().__init__(master)
        self.scheduler = scheduler
        self.job = job
        self.on_saved = on_saved
        self.title("定时任务编辑" if job else "新增定时任务")
        self.geometry("460x300")
        self.transient(master)
        self.grab_set()
        self.name_var = tk.StringVar(value=job.name if job else "")
        self.cron_var = tk.StringVar(value=job.cron if job else "0 9 * * *")
        platforms = {}
        for key, plat in getattr(scheduler.app, "platforms", {}).items():
            platforms[key] = plat.name
        rows = [
            ("任务名称", self.name_var, None),
            ("cron 表达式", self.cron_var,
             {"values": ["0 9 * * *", "0 18 * * 1-5", "0 */2 * * *", "0 9 * * 1"],
              "width": 22}),
        ]
        for i, (label, var, extra) in enumerate(rows):
            tk.Label(self, text=label, font=("Microsoft YaHei", 9)).grid(
                row=i, column=0, sticky="w", padx=8, pady=6)
            if extra:
                tk.ttk.Combobox(self, textvariable=var, state="normal",
                                values=extra["values"], width=extra["width"]).grid(
                    row=i, column=1, sticky="w", padx=8, pady=6)
            else:
                tk.Entry(self, textvariable=var, width=24,
                         font=("Microsoft YaHei", 9)).grid(
                    row=i, column=1, sticky="w", padx=8, pady=6)
        # 平台多选(Checkbutton, 横向)
        plat_row = rows and len(rows) or 0
        self.plat_vars = {}
        tk.Label(self, text="平台:", font=("Microsoft YaHei", 9)).grid(
            row=plat_row, column=0, sticky="nw", padx=8, pady=6)
        pf = tk.Frame(self)
        pf.grid(row=plat_row, column=1, sticky="w", padx=8, pady=6)
        sel_plats = job.platforms if job else []
        for key in sorted(platforms):
            v = tk.BooleanVar(value=key in sel_plats)
            self.plat_vars[key] = v
            tk.Checkbutton(pf, text=platforms[key], variable=v,
                           font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
        # 商户(逗号分隔文本)
        tk.Label(self, text="商户(逗号分隔):", font=("Microsoft YaHei", 9)).grid(
            row=plat_row + 1, column=0, sticky="w", padx=8, pady=6)
        self.merch_var = tk.StringVar(value=",".join(job.merchants) if job else "")
        tk.Entry(self, textvariable=self.merch_var, width=24,
                 font=("Microsoft YaHei", 9)).grid(
            row=plat_row + 1, column=1, sticky="w", padx=8, pady=6)
        tk.Button(self, text="保存", command=self._save,
                  font=("Microsoft YaHei", 9)).grid(row=plat_row + 2, column=0, pady=8)
        tk.Button(self, text="取消", command=self.destroy,
                  font=("Microsoft YaHei", 9)).grid(row=plat_row + 2, column=1, pady=8)

    def _save(self):
        name = self.name_var.get().strip()
        cron = self.cron_var.get().strip()
        plats = [k for k, v in self.plat_vars.items() if v.get()]
        merchants = [m.strip() for m in self.merch_var.get().split(",") if m.strip()]
        if not name or not plats:
            messagebox.showwarning("提示", "任务名称与至少一个平台必填", parent=self)
            return
        try:
            CronExpr(cron)
        except ValueError as e:
            messagebox.showwarning("cron 非法", str(e), parent=self)
            return
        if self.job is None:
            job = CronJob(job_id=uuid.uuid4().hex[:8], name=name, cron=cron,
                          platforms=plats, merchants=merchants)
            self.scheduler.store.jobs.append(job)
        else:
            self.job.name, self.job.cron = name, cron
            self.job.platforms, self.job.merchants = plats, merchants
        self.scheduler.store.save()
        if self.on_saved:
            self.on_saved()
        self.destroy()
```

- [ ] **Step 2: main_gui 挂接**

在 `core/main_gui.py` 中：
0. 先在 `core/config.py` 的模块级常量区（`SETTINGS_FILE` 附近）新增：

```python
# 定时任务持久化文件
SCHEDULED_TASKS_FILE = os.path.join(ROOT_DIR, "scheduled_tasks.json")
```

1. `__init__` 末尾（keepalive 之后）创建调度器并启动（`__init__` 内局部导入）：

```python
from core.scheduler import CronScheduler
self.scheduler = CronScheduler(self, poll_interval=30)
self.scheduler.start()
```

2. 新增方法：

```python
def _open_scheduler_dialog(self):
    """定时任务管理弹窗。"""
    from core.scheduler import SchedulerDialog
    SchedulerDialog(self.root, self.scheduler, rebuild_cb=lambda: None)

def trigger_job(self, job):
    """CronScheduler 回调: 按任务对象执行导出(昨天~今天)。"""
    tasks = []
    for key in job.platforms:
        plat = self.platforms.get(key)
        if not plat:
            continue
        for m in job.merchants:
            tasks.append((key, plat, m))
    if not tasks:
        return
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    log(f"定时任务触发: {job.name} ({len(tasks)} 项)", callback=self._append_log)
    self._run_async(lambda: self._execute_export_tasks(tasks, start, end, label=f"定时任务[{job.name}]"))
```

3. 按钮网格第 3 行新增「定时任务」（`bar.grid_rowconfigure(3, weight=1)` 后放 `row=3, column=0`；颜色 `#8e44ad` 系区分用 `#2c3e50`），命令 `self._open_scheduler_dialog`。
4. 在「登录保活」设置行下方新增「失败重试」设置行：勾选/次数 Spinbox（0-5，默认取 settings.retry_times），即时 `save_settings`（参考 `_on_ka_setting` 写法；方法 `_on_retry_setting`）。
5. 窗口关闭钩子 `_on_close` 中在 `app.keepalive.stop()` 前加 `app.scheduler.stop()`。
6. `.gitignore` 追加一行 `/scheduled_tasks.json`。

- [ ] **Step 3: 静态检查与冒烟**

Run:
- `python -m py_compile core/scheduler.py core/main_gui.py core/config.py`
- `python -c "import core.main_gui as m; import core.scheduler as s; print('OK', hasattr(m.LiushuiApp,'trigger_job'), hasattr(m.LiushuiApp,'_open_scheduler_dialog'), hasattr(s,'SchedulerDialog'), hasattr(s,'CronScheduler'))"` → `OK True True True True`
- `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests -q` → 全部 PASS（14 个：原 11 + retry 3；scheduler 15 另计——实际总数以运行结果为准，报告数字）。

- [ ] **Step 4: 提交**

```bash
git add core/scheduler.py core/main_gui.py core/config.py .gitignore
git commit -m "feat(gui): scheduler dialog and retry settings"
```

---

### Task 6: 批3 回归与文档同步

**Files:**
- Modify: `CODE_WIKI.md`

- [ ] **Step 1: 回归**

Run:
- `python -m py_compile core/scheduler.py core/main_gui.py core/browser.py core/config.py core/exporters.py core/loader.py core/logger.py core/keepalive.py core/platform_admin.py core/platform_base.py platforms/youzan/export.py`
- `$env:PYTHONDONTWRITEBYTECODE=1; python -m pytest tests -v` → 全部 PASS（预期 14 + 15 = 29? 以实际为准：test_logger 3 + test_keepalive 3 + test_platform_admin 5 + test_retry 3 + test_scheduler 15 = 29，报告实际数字）
- `python -c "from core.loader import discover_platforms; print(sorted(discover_platforms().keys()))"` → `['youzan']`

- [ ] **Step 2: 更新 CODE_WIKI.md**

1. 第 4 章模块职责表新增：`| \`core/scheduler.py\` | **定时任务**。\`CronExpr\`（5 字段 cron 轻量解析/匹配/next-run）、\`CronJob\`/\`TaskStore\`（\`scheduled_tasks.json\` 持久化）、\`CronScheduler\`（后台线程到期触发）、\`SchedulerDialog\`/\`JobEditDialog\`（管理界面）。 |`
2. 第 5 章新增 `### 5.8 CronScheduler（core/scheduler.py）— 定时任务`（在模块级关键函数小节之前插入并顺延编号），内容：
   - `CronExpr(expr)`：支持 `*` / `*/n` / `a-b` / `a,b` / `?`；`match(dt)` 按分时日月周匹配（dom 与 dow 同时受限为 OR）；`next_run(after)` 返回下一个匹配分钟。
   - `CronJob` / `TaskStore`：任务模型与 `scheduled_tasks.json`（version 1）读写，损坏条目跳过。
   - `CronScheduler(app, store, poll_interval)`：后台线程每 30s 轮询；`should_trigger(job, now)` 用「上次运行后的 next_run <= now」判断；触发经 `app.trigger_job(job)`，随后更新 `last_run` 并保存；错过不补跑。
   - `SchedulerDialog` / `JobEditDialog`：任务列表 + 新增/编辑/删除/启停；cron 输入带常用模板下拉与合法性校验。
   - 失败重试：`run_with_retry(fn, retry_times, retry_interval_s, log)` 与 `_execute_export_tasks`（提炼自 `_do_export`）说明，settings 项 `retry_times`（默认 2）、`retry_interval_s`（默认 30，递增 ×2）。
3. 第 10 章数据目录表新增：`| \`scheduled_tasks.json\` | 是 | 定时任务持久化（新增/编辑后自动保存） |`
4. 附录文件清单新增 `core/scheduler.py`（约 260 行）与 `tests/` 行描述追加 test_scheduler、test_retry。

- [ ] **Step 3: 提交**

```bash
git add CODE_WIKI.md
git commit -m "docs: batch3 (scheduler + retry) sync"
```

---

## 验收总纲（批 3）

- [ ] pytest 29 个用例全绿（logger/keepalive/platform_admin/retry/scheduler）
- [ ] 主界面出现「定时任务」按钮与「失败重试」设置
- [ ] 用短周期 cron（如 `*/1 * * * *`）新建任务，能看到周期触发且 `last_run` 更新（可用仅日志观测）
- [ ] 失败场景出现「[重试] 第 i/N 次重试」日志；`retry_times=0` 时不重试
- [ ] `python -m core.main_gui` 启动正常（含保活、平台管理、调试、定时入口共存）