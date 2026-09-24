import json
from datetime import datetime, timedelta

import pytest

from core.scheduler import (CronExpr, CronJob, TaskStore, CronScheduler,
                            pair_job_targets, unmatched_job_merchants)


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


def test_new_job_fires_at_next_cron_point_not_on_save():
    """新建任务不能"保存即触发": CronJob.new 以创建时间为起算点。"""
    now = datetime(2026, 9, 4, 18, 0, 30)
    s = CronScheduler(app=object())
    job = CronJob.new(name="每日导出", cron="0 9 * * *",
                      platforms=["youzan"], merchants=["旗舰店A"], now=now)
    assert job.job_id and job.enabled is True
    assert s.should_trigger(job, now) is False
    assert s.should_trigger(job, _dt("2026-09-05 09:00")) is True


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


def test_pair_job_targets_matches_only_own_platform_merchants():
    """任务里的商户是一整条与平台无关的文本, 不能做平台×商户笛卡尔积。"""
    merchants_by_key = {"youzan": ["旗舰店A", "旗舰店B"], "alipay": ["支付宝商户"]}
    pairs = pair_job_targets(["youzan", "alipay"], ["旗舰店A", "支付宝商户"], merchants_by_key)
    assert pairs == [("youzan", "旗舰店A"), ("alipay", "支付宝商户")]


def test_pair_job_targets_empty_or_unknown_yields_nothing():
    assert pair_job_targets(["youzan"], [], {"youzan": ["旗舰店A"]}) == []
    assert pair_job_targets(["youzan"], ["旗舰店A"], {}) == []


def test_unmatched_job_merchants_lists_what_would_never_run():
    """保存任务时用来提示"这几个商户填了也不会执行"。"""
    known = {"youzan": ["旗舰店A"], "alipay": ["支付宝商户"]}
    assert unmatched_job_merchants(["youzan"], ["旗舰店A", "拼错了的店"], known) == ["拼错了的店"]
    assert unmatched_job_merchants(["youzan", "alipay"], ["旗舰店A", "支付宝商户"], known) == []
    # 商户属于别的平台: 对所选平台而言就是不匹配, 应该提示
    assert unmatched_job_merchants(["youzan"], ["支付宝商户"], known) == ["支付宝商户"]


class _App:
    def __init__(self, accepted):
        self.accepted = accepted
        self.calls = 0

    def trigger_job(self, job):
        self.calls += 1
        return self.accepted


def _store_with_job(tmp_path):
    store = TaskStore(str(tmp_path / "t.json"))
    store.save([_job(name="每日", cron="* * * * *", last_run="2026-09-04T17:00:00")])
    return store


def test_busy_app_does_not_consume_the_scheduled_run(tmp_path):
    """到点时手动导出还在跑: 不记 last_run, 否则这次定时导出就彻底丢了。"""
    store = _store_with_job(tmp_path)
    app = _App(accepted=False)
    s = CronScheduler(app=app, store=store)
    now = datetime(2026, 9, 4, 18, 0)
    s.check_all(now)
    assert app.calls == 1
    assert store.load()[0].last_run == "2026-09-04T17:00:00"


def test_deferred_job_is_retried_on_next_poll(tmp_path):
    store = _store_with_job(tmp_path)
    app = _App(accepted=False)
    s = CronScheduler(app=app, store=store)
    now = datetime(2026, 9, 4, 18, 0)
    s.check_all(now)
    s.check_all(now + timedelta(seconds=30))
    assert app.calls == 2                       # 下个轮询周期再试
    app.accepted = True
    s.check_all(now + timedelta(minutes=1))
    assert store.load()[0].last_run == (now + timedelta(minutes=1)).isoformat()


def test_started_job_stamps_last_run(tmp_path):
    store = _store_with_job(tmp_path)
    s = CronScheduler(app=_App(accepted=True), store=store)
    now = datetime(2026, 9, 4, 18, 0)
    s.check_all(now)
    assert store.load()[0].last_run == now.isoformat()