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