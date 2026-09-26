"""`check_login` 等重定向落地的采样方式。

回归的是两处一起坏掉的东西: 旧的 `browser.wait_for(timeout=3)` 不传条件, 于是
① 每家商户白等满 3 秒(预检 + inline 预检 = 每家两遍, 10 家就是 60 秒),
② 它收尾必然打一条"等待超时"的 WARN, 用户在日志里看到的是"像出事了"的一行。

假 browser 把 `sleep` 变成记账(不真睡), 所以这些用例跑完是毫秒级;
"等了多久"用累计 sleep 秒数这个确定性口径来断言, 不量墙钟。
"""
from core.platform_base import PlatformBase

BILL = "https://example.com/fund/bill"
LOGIN = "https://example.com/login?from=bill"


class _Plat(PlatformBase):
    key = "fake"
    name = "假平台"
    login_url = LOGIN
    export_url = BILL


class _Browser:
    """每次 get_page_info 返回 urls 里的下一个地址(用完后停在最后一个)。"""

    def __init__(self, urls):
        self.urls = list(urls)
        self.samples = 0
        self.slept = 0.0
        self.wait_for_calls = []
        self.navigated = []

    def navigate(self, url, **kw):
        self.navigated.append(url)

    def get_page_info(self):
        i = self.samples
        self.samples += 1
        return {"url": self.urls[i] if i < len(self.urls) else self.urls[-1],
                "title": "后台"}

    def sleep(self, seconds):
        self.slept += seconds

    def wait_for(self, *args, **kwargs):
        self.wait_for_calls.append((args, kwargs))
        return False


def _check(urls):
    b = _Browser(urls)
    return _Plat().check_login(b), b


def test_logged_in_does_not_burn_the_whole_budget():
    ok, b = _check([BILL])
    assert ok is True
    assert b.samples == _Plat.LOGIN_POLL_STABLE + 1, "地址连续静止够久就该收工"
    assert b.samples < _Plat.LOGIN_POLL_MAX, "不许耗满采样上限"
    assert round(b.slept, 2) < 3.0, f"等了 {b.slept}s, 旧写法固定 3 秒"
    assert b.navigated == [BILL]


def test_no_more_fake_timeout_warning():
    """不再借道 wait_for: 它不传条件时必然超时, 会在日志里留下一条假 WARN。"""
    _ok, b = _check([BILL])
    assert b.wait_for_calls == []


def test_expired_session_answers_at_the_first_sample():
    ok, b = _check([LOGIN])
    assert ok is False
    assert b.samples == 1, "落到登录路径应立即定论(旧写法这里照样等满 3 秒)"
    assert b.slept == 0.0


def test_redirect_a_few_samples_late_is_still_caught():
    ok, b = _check([BILL, BILL, BILL, LOGIN])
    assert ok is False
    assert b.samples == 4


def test_url_that_never_settles_uses_the_last_seen_one():
    """"地址一直在变"时按上限收尾, 结论仍取最后一次看到的地址。"""
    urls = [f"{BILL}?_t={i}" for i in range(_Plat.LOGIN_POLL_MAX)]
    ok, b = _check(urls)
    assert ok is True
    assert b.samples == _Plat.LOGIN_POLL_MAX

    b2 = _Browser([f"{BILL}?_t={i}" for i in range(_Plat.LOGIN_POLL_MAX - 1)] + [LOGIN])
    assert _Plat().check_login(b2) is False, "到点那一下正好跳登录页, 也要判成未登录"


def test_stability_window_is_a_documented_trade_off():
    """钉住唯一的语义收窄处: 重定向晚于"地址静止窗口"才发生 → 判成"已登录"。

    这是有意的方向选择 —— 判成"没登录"会把本该到手的账单挡掉, 判成"已登录"最多是
    这一家白跑一次、导不出文件转 manual。调大 LOGIN_POLL_STABLE 会撞红这条。
    """
    late = [BILL] * (_Plat.LOGIN_POLL_STABLE + 2) + [LOGIN]
    ok, b = _check(late)
    assert ok is True
    assert b.samples == _Plat.LOGIN_POLL_STABLE + 1
