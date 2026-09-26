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


# ===== cron 缺失/非法: 不兜成每分钟, 但要说清为什么不跑 =====

class _NoTriggerApp:
    def __init__(self):
        self.calls = []

    def trigger_job(self, job):
        self.calls.append(job.name)


def _write_jobs(path, raw_jobs):
    with open(str(path), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "jobs": raw_jobs}, f)


def test_missing_cron_is_not_becoming_every_minute(tmp_path):
    """手改 scheduled_tasks.json 漏写 cron, 以前会兜成 '* * * * *' = 每分钟起一次浏览器。"""
    path = str(tmp_path / "t.json")
    _write_jobs(path, [{"id": "j9", "name": "缺 cron", "platforms": ["youzan"],
                        "merchants": ["m1"], "enabled": True}])
    job = TaskStore(path).load()[0]
    assert job.cron == ""
    s = CronScheduler(app=_NoTriggerApp(), store=TaskStore(path))
    every_minute = _dt("2026-09-04 18:00")
    assert s.should_trigger(job, every_minute) is False
    s.check_all(every_minute)
    assert s.app.calls == []


def test_explicit_every_minute_cron_still_fires(tmp_path):
    """用户显式写了每分钟, 那是他的选择, 不该被这次改动一起停掉。"""
    path = str(tmp_path / "t.json")
    _write_jobs(path, [{"id": "j8", "name": "每分钟", "cron": "* * * * *",
                        "platforms": ["youzan"], "merchants": ["m1"], "enabled": True}])
    s = CronScheduler(app=_NoTriggerApp(), store=TaskStore(path))
    s.check_all(_dt("2026-09-04 18:00"))
    assert s.app.calls == ["每分钟"]


def test_scheduler_says_why_a_job_never_runs(tmp_path, monkeypatch):
    """静默不跑和静默乱跑一样糟: 要说一句, 但 30 秒一次的轮询不能刷屏。"""
    import core.logger as lg
    import core.scheduler as sc
    lines = []
    monkeypatch.setattr(lg, "log", lambda msg, level="info": lines.append((level, msg)))
    path = str(tmp_path / "t.json")
    _write_jobs(path, [{"id": "j7", "name": "写错的", "cron": "70 * * * *",
                        "platforms": ["youzan"], "merchants": ["m1"], "enabled": True}])
    s = CronScheduler(app=_NoTriggerApp(), store=TaskStore(path))
    now = _dt("2026-09-04 18:00")
    s.check_all(now)
    s.check_all(now)
    s.check_all(now)
    hits = [m for _l, m in lines if "不会被执行" in m]
    assert len(hits) == 1, "同一个坏任务只说一次"
    assert "写错的" in hits[0] and "cron" in hits[0]


def test_warn_once_survives_regenerated_job_ids(tmp_path, monkeypatch):
    """json 里没写 id 的条目, from_dict 每轮 load 都会新生成一个随机 id ——
    "只说一次"要是按 job_id 记, 就变成每 30 秒说一次。"""
    import core.logger as lg
    lines = []
    monkeypatch.setattr(lg, "log", lambda msg, level="info": lines.append((level, msg)))
    path = str(tmp_path / "t.json")
    _write_jobs(path, [{"name": "没有 id", "platforms": ["youzan"],
                        "merchants": ["m1"], "enabled": True}])
    s = CronScheduler(app=_NoTriggerApp(), store=TaskStore(path))
    now = _dt("2026-09-04 18:00")
    ids = set()
    for _ in range(4):
        s.check_all(now)
        ids.update(j.job_id for j in s.store.jobs)
    assert len(ids) == 4, "前提: 每轮 load 出来的 id 确实不一样"
    assert len([m for _l, m in lines if "不会被执行" in m]) == 1


def test_cron_problem_covers_blank_and_invalid():
    def _j(cron):
        return CronJob(job_id="a", name="n", cron=cron, platforms=[], merchants=[])
    assert _j("0 9 * * *").cron_problem() is None
    assert "没有填" in _j("").cron_problem()
    assert "无效" in _j("70 * * * *").cron_problem()


def test_disabled_job_with_bad_cron_is_not_nagged(tmp_path, monkeypatch):
    """停用的任务本来就不该跑, 不必为它的 cron 反复提醒。"""
    import core.logger as lg
    monkeypatch.setattr(lg, "log", lambda msg, level="info": None)
    path = str(tmp_path / "t.json")
    _write_jobs(path, [{"id": "j6", "name": "停用的", "platforms": ["youzan"],
                        "merchants": ["m1"], "enabled": False}])
    s = CronScheduler(app=_NoTriggerApp(), store=TaskStore(path))
    s.check_all(_dt("2026-09-04 18:00"))
    assert s._warned_jobs == set()


def test_editing_cron_rebaselines_so_it_does_not_fire_now():
    """改 cron 前是每分钟跑, last_run 停在昨天; 不挪起算点的话保存后立刻触发一次。"""
    s = CronScheduler(app=object())
    job = _job(cron="* * * * *", last_run="2026-09-03T09:00:00")
    now = datetime(2026, 9, 4, 18, 0)
    assert s.should_trigger(job, now) is True          # 改之前: 早已到期

    changed = job.apply_edit("每日导出", "0 9 * * *", ["youzan"], ["旗舰店A"], now=now)
    assert changed is True
    assert job.last_run == now.isoformat()
    assert s.should_trigger(job, now) is False         # 改之后: 等明早 9 点
    assert s.should_trigger(job, _dt("2026-09-05 09:00")) is True


def test_editing_without_cron_change_keeps_baseline():
    job = _job(cron="0 9 * * *", last_run="2026-09-04T09:00:00")
    changed = job.apply_edit("改名了", "0 9 * * *", ["youzan", "alipay"], ["旗舰店A"],
                             now=datetime(2026, 9, 4, 18, 0))
    assert changed is False
    assert job.last_run == "2026-09-04T09:00:00"       # 只改名/平台不该被当成重新计时
    assert job.name == "改名了" and job.platforms == ["youzan", "alipay"]


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