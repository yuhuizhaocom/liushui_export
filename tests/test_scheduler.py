import json
from datetime import datetime, timedelta

import pytest

from core.scheduler import CronExpr, CronJob, TaskStore, CronScheduler


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