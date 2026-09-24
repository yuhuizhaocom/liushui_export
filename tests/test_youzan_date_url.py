"""有赞流水页日期参数: 必须覆盖掉 URL 里录制当时的固定日期, 坏日期不得静默退回。"""
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from platforms.youzan.export import YouzanExporter

CST = timezone(timedelta(hours=8))


def _ms(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
               .replace(tzinfo=CST).timestamp() * 1000)


def _query(url):
    return parse_qs(urlparse(url).query)


def test_start_and_end_replace_recorded_values():
    url = ("https://www.youzan.com/v4/assets/record?accountType=&bizType=7"
           "&dateType=CREATE_TIME&endTime=1788364799999&page=1&startTime=1787760000000")
    q = _query(YouzanExporter()._build_youzan_url(url, "2026-09-01", "2026-09-03"))
    assert int(q["startTime"][0]) == _ms("2026-09-01 00:00:00")
    # 结束时间取结束日 23:59:59 当天末尾(毫秒级 999 由脚本决定, 不断言具体值)
    assert _ms("2026-09-03 23:59:59") <= int(q["endTime"][0]) < _ms("2026-09-03 23:59:59") + 1000
    assert q["dateType"] == ["SETTLE_TIME"]
    assert q["page"] == ["1"] and q["bizType"] == ["7"]   # 其余参数原样保留


@pytest.mark.parametrize("bad", ["", "09-01-2026", None])
def test_bad_date_raises_instead_of_reusing_recorded_url(bad):
    """退回原 URL = 静默导出录制时的死区间, 账单看起来完全正常, 所以必须抛。"""
    url = "https://www.youzan.com/v4/assets/record?startTime=1787760000000"
    with pytest.raises(Exception):
        YouzanExporter()._build_youzan_url(url, bad, "2026-09-03")


def test_unpadded_date_is_accepted_by_strptime():
    """strptime 的 %m/%d 不要求补零, "2026-9-1" 能正常解析, 不会被当成坏日期。"""
    url = "https://www.youzan.com/v4/assets/record?startTime=1787760000000"
    q = _query(YouzanExporter()._build_youzan_url(url, "2026-9-1", "2026-9-3"))
    assert int(q["startTime"][0]) == _ms("2026-09-01 00:00:00")
    assert int(q["endTime"][0]) >= _ms("2026-09-03 23:59:59")
