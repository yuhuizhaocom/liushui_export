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
import tkinter as tk
import uuid
from datetime import datetime, timedelta
from tkinter import ttk, messagebox


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
        if self.week:  # cron 0/7=周日 -> Python weekday()==6; 其余 1-6 对应 Python 0-5
            self.week = {6 if v == 0 else (v - 1) % 7 for v in self.week}

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


def pair_job_targets(job_platforms, job_merchants, merchants_by_key):
    """把定时任务里的平台/商户配成实际可跑的 (平台key, 商户名) 列表。

    任务编辑框中商户是一整条与平台无关的逗号文本, 若直接做平台×商户笛卡尔积,
    会给不属于该平台的商户拉起一个没有登录态的 profile。这里只保留
    browser_data/<平台key>/<商户> 下确实建档的配对。
    """
    pairs = []
    for key in job_platforms:
        known = set(merchants_by_key.get(key, []))
        for m in job_merchants:
            if m in known:
                pairs.append((key, m))
    return pairs


def unmatched_job_merchants(job_platforms, job_merchants, merchants_by_key):
    """填了但在所选任何一个平台下都没建档的商户名(用于编辑任务时提示)。"""
    known_any = set()
    for key in job_platforms:
        known_any.update(merchants_by_key.get(key, []))
    return [m for m in job_merchants if m not in known_any]


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

    def apply_edit(self, name, cron, platforms, merchants, now=None):
        """界面"编辑任务"的唯一入口, 返回 cron 是否变了。

        改 cron 时必须把起算点挪到本次编辑时刻: should_trigger 是拿 last_run 算
        next_run 的, 老 last_run 配新 cron 往往早就"到期", 于是保存后 30 秒内会
        立刻执行一次 —— 而用户改时间点的意图是"从下一个新时间点开始"。
        """
        cron_changed = self.cron != cron
        self.name, self.cron = name, cron
        self.platforms, self.merchants = list(platforms), list(merchants)
        if cron_changed:
            self.last_run = (now or datetime.now()).isoformat()
        return cron_changed

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
        """由 scheduled_tasks.json 的一条记录建任务。

        cron 缺失/为空时**不再**兜成 "* * * * *": 那是"每分钟跑一次", 而每一次都要
        起一个浏览器去点导出 —— 手改 json 少写一个键就把人坑成这样。现在把空值原样
        留着, 由 should_trigger 判成"这个任务不跑", 调度器再补一条日志说清是哪个任务、
        为什么没跑(静默不跑和静默乱跑一样糟)。显式写 "* * * * *" 的照旧生效。
        """
        return cls(job_id=d.get("id") or uuid.uuid4().hex[:8],
                   name=d.get("name") or "",
                   cron=(d.get("cron") or "").strip(),
                   platforms=d.get("platforms") or [],
                   merchants=d.get("merchants") or [],
                   enabled=d.get("enabled", True),
                   last_run=d.get("last_run"))

    def cron_problem(self):
        """cron 配置上的毛病(给用户看的一句话); 没问题返回 None。"""
        if not self.cron:
            return "没有填 cron"
        try:
            CronExpr(self.cron)
        except ValueError as e:
            return f"cron 表达式无效: {e}"
        return None

    @classmethod
    def new(cls, name, cron, platforms, merchants, now=None):
        """新建任务专用入口: 把 last_run 记为创建时间, 调度因此从下一个 cron 点起算。
        直接用 __init__ 建任务时 last_run 为 None, should_trigger 按"从未运行=已到期"
        处理, 会让刚保存的任务在下一个轮询周期(默认 30s)内立刻执行一次。"""
        return cls(job_id=uuid.uuid4().hex[:8], name=name, cron=cron,
                   platforms=platforms, merchants=merchants,
                   last_run=(now or datetime.now()).isoformat())


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
            from .config import write_json_atomic
            write_json_atomic(self.path, {"version": 1,
                                          "jobs": [j.to_dict() for j in self.jobs]})
        except Exception:
            pass


def _default_tasks_path():
    from core.config import SCHEDULED_TASKS_FILE
    return SCHEDULED_TASKS_FILE


class CronScheduler:
    """后台线程轮询已启用任务, 到点通过 app.trigger_job(job) 触发。
    触发后更新 last_run 并保存; 错过的触发在下次轮询自动跳过(不补跑)。"""

    def __init__(self, app, store=None, poll_interval=30):
        self.app = app
        self.store = store or TaskStore()
        self.poll_interval = max(10, int(poll_interval))
        self._stop = threading.Event()
        self._thread = None
        self._warned_jobs = set()      # cron 有毛病的任务: 每个只说一次, 别刷屏

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
            if job.enabled:
                problem = job.cron_problem()
                if problem:
                    # 曾经这里会被 from_dict 兜成每分钟, 静默地把浏览器一遍遍拉起来
                    self._warn_once(job, problem)
                    continue
            if self.should_trigger(job, now):
                self.trigger(job, now)

    def _warn_once(self, job, problem):
        """把"这个任务为什么不会跑"写进日志一次; 提示本身绝不许弄死调度线程。"""
        tag = job.job_id or job.name or problem
        if tag in self._warned_jobs:
            return
        self._warned_jobs.add(tag)
        try:
            from core.logger import log
            log(f"定时任务「{job.name or tag}」不会被执行: {problem}"
                "(请在「定时任务」里填对 cron 表达式, 或把它停用)", "warning")
        except Exception:
            pass

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
        if last is None:
            return True  # 从未运行过,视为到期
        nxt = expr.next_run(last)
        return nxt is not None and nxt <= now

    def trigger(self, job, now):
        """执行一个到期任务; 只有 app 真的开始跑了才记 last_run。

        app 正忙时(手动导出还在跑) trigger_job 会返回 False, 以前仍然无条件写
        last_run —— 这一次定时导出就彻底丢了, 要等下一个点位。不记则下个轮询
        周期自动再试一次。老 app 没返回值时按"已开始"处理, 行为不变。
        """
        started = self.app.trigger_job(job)
        if started is False:
            return False
        job.last_run = now.isoformat()
        self.store.save()
        return True


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
            # 商户是按平台建档的, "3平台 × 2商户" 那种写法会让人以为跑出 6 项,
            # 实际执行的是 pair_job_targets 配出来的项。
            known = getattr(self.scheduler.app, "merchants", {}) or {}
            pairs = pair_job_targets(job.platforms, job.merchants, known)
            objs = f"{len(pairs)} 项" if pairs else "0 项(商户与平台不匹配)"
            self.tree.insert("", "end", iid=job.job_id,
                             values=(job.name, job.cron or "(未填 cron)", objs,
                                     "✓" if job.enabled else "✗"))

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
        tk.Label(self, text="商户(逗号分隔,须是所选平台已建档的):", font=("Microsoft YaHei", 9)).grid(
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
        # 商户/平台不匹配或没填商户时, 任务到点什么都不会跑; 以前是保存成功、
        # 无声无息, 现在当场提示(仍可坚持保存)。
        known = getattr(self.scheduler.app, "merchants", {}) or {}
        warning = ""
        if not merchants:
            warning = "该任务没有填商户, 到点不会导出任何文件。"
        else:
            unknown = unmatched_job_merchants(plats, merchants, known)
            if unknown:
                warning = ("以下商户在所选平台下没有建档, 这些组合不会执行:\n"
                           + "、".join(unknown))
        if warning and not messagebox.askyesno(
                "定时任务设置可能有误", warning + "\n\n仍要保存吗?", parent=self):
            return
        if self.job is None:
            job = CronJob.new(name=name, cron=cron, platforms=plats, merchants=merchants)
            self.scheduler.store.jobs.append(job)
        else:
            self.job.apply_edit(name, cron, plats, merchants)
        self.scheduler.store.save()
        if self.on_saved:
            self.on_saved()
        self.destroy()