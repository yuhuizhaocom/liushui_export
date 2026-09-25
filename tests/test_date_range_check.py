"""日期区间校验: 未来的日期导不出东西, 别让人等满一轮重试才发现。

以前只校验格式和起止顺序, 于是"2027-12-31"这种填错的、以及一次导三年的, 都会照常开跑:
后台对未来的区间只会给一张空列表, 程序等满下载超时 → 转 manual → 再重试两次, 用户看到
的是"莫名就一直失败"。顺带把 `2026-9-2` 规范成 `2026-09-02` —— 这个字符串会进日期框、
归档目录名和汇总文件夹名, 两种写法会分成两个目录。
"""
from datetime import datetime, timedelta

import pytest

import core.main_gui as mg
from core.main_gui import DATE_MAX_SPAN_DAYS, LiushuiApp, check_date_range

TODAY = datetime(2026, 9, 25)


def _v(s, e, today=TODAY):
    return check_date_range(s, e, today=today)


def test_ordinary_range_passes():
    c = _v("2026-09-01", "2026-09-02")
    assert (c.verdict, c.title, c.message) == ("ok", "", "")


def test_today_is_not_in_the_future():
    assert _v("2026-09-25", "2026-09-25").verdict == "ok"


@pytest.mark.parametrize("s,e", [("2026-13-45", "2026-12-01"), ("", "2026-09-01"),
                                 ("abc", "2026-09-02"), ("2026-09-01", "不是日期")])
def test_format_errors_keep_the_original_wording(s, e):
    c = _v(s, e)
    assert (c.verdict, c.title) == ("bad", "日期格式")
    assert c.message == "日期格式应为 YYYY-MM-DD(如 2026-09-01),请检查后重试。"


def test_reversed_order_keeps_the_original_wording():
    c = _v("2026-09-05", "2026-09-01")
    assert (c.verdict, c.message) == ("bad", "结束日期不能早于开始日期,请检查后重试。")


def test_future_end_is_refused():
    c = _v("2026-09-20", "2026-10-01")
    assert (c.verdict, c.title) == ("bad", "日期范围")
    assert "结束日期晚于今天(2026-09-25)" in c.message and "还没产生" in c.message


def test_both_in_the_future_names_the_start_box():
    c = _v("2026-10-01", "2026-10-05")
    assert c.verdict == "bad" and "开始日期晚于今天" in c.message


def test_loose_text_is_normalised_for_the_caller():
    c = _v("2026-9-2", "2026-09-05")
    assert c.verdict == "ok" and (c.start, c.end) == ("2026-09-02", "2026-09-05")


def test_span_boundary_asks_only_above_the_limit():
    """含两端算天数: 正好等于上限放行, 多一天才问。"""
    within = (TODAY - timedelta(days=DATE_MAX_SPAN_DAYS - 1)).strftime("%Y-%m-%d")
    assert _v(within, "2026-09-25").verdict == "ok"
    over = _v((TODAY - timedelta(days=DATE_MAX_SPAN_DAYS)).strftime("%Y-%m-%d"),
              "2026-09-25")
    assert (over.verdict, over.title) == ("ask", "日期范围")
    assert "93 天" in over.message and str(DATE_MAX_SPAN_DAYS) in over.message


def test_three_year_range_is_a_question_not_a_block():
    """一次导三年是"多半拿不全", 不是"绝对不行": 只问一句, 用户坚持就照办。"""
    c = _v("2023-01-01", "2026-01-01")
    assert c.verdict == "ask" and "1097 天" in c.message


class _Entry:
    """够用的 StringVar 替身: 日期框只用 get/set。"""

    def __init__(self, text):
        self._t = text
        self.sets = []

    def get(self):
        return self._t

    def set(self, value):
        self._t = value
        self.sets.append(value)


class _App:
    _validate_dates = LiushuiApp._validate_dates

    def __init__(self, s, e):
        self.date_start = _Entry(s)
        self.date_end = _Entry(e)


class _Dialogs:
    def __init__(self):
        self.warn = []
        self.ask = []
        self.answer = True


@pytest.fixture()
def dialogs(monkeypatch):
    d = _Dialogs()
    monkeypatch.setattr(mg.messagebox, "showwarning",
                        lambda title, msg: d.warn.append((title, msg)))

    def askyesno(title, msg):
        d.ask.append((title, msg))
        return d.answer
    monkeypatch.setattr(mg.messagebox, "askyesno", askyesno)
    return d


def test_bad_date_pops_the_warning_and_stops(dialogs):
    assert _App("2026-13-01", "2026-09-02")._validate_dates() is False
    assert dialogs.warn and dialogs.warn[0][0] == "日期格式"


def test_long_range_declined_stops_the_job(dialogs):
    dialogs.answer = False
    assert _App("2023-01-01", "2026-01-01")._validate_dates() is False
    assert len(dialogs.ask) == 1


def test_long_range_accepted_continues_and_keeps_the_dates(dialogs):
    app = _App("2023-01-01", "2026-01-01")
    assert app._validate_dates() is True
    assert dialogs.warn == [] and len(dialogs.ask) == 1
    assert app.date_start.sets == []                  # 本来就是规范写法, 不该回写


def test_loose_format_is_written_back_into_the_boxes(dialogs):
    app = _App("2026-9-2", "2026-9-5")
    assert app._validate_dates() is True
    assert app.date_start.get() == "2026-09-02"
    assert app.date_end.get() == "2026-09-05"


def test_check_status_mode_stays_silent(dialogs):
    """show_warning=False(检查登录状态)时不弹窗: 未来日期仍拦下, 超长区间照放行。"""
    future = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    far = (datetime.now() + timedelta(days=9)).strftime("%Y-%m-%d")
    assert _App(future, far)._validate_dates(show_warning=False) is False
    assert _App("2023-01-01", "2026-01-01")._validate_dates(show_warning=False) is True
    assert dialogs.warn == [] and dialogs.ask == []
