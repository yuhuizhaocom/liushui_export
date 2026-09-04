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