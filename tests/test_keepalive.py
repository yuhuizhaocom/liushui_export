import threading
from core.keepalive import KeepAliveService


class FakeBrowser:
    def __init__(self, url, title, log=None):
        self.url, self.title, self.log = url, title, log
        self.closed = False

    def start(self): pass

    def navigate(self, url): pass

    def sleep(self, s): pass

    def close_popup(self): pass

    def get_page_info(self): return {"url": self.url, "title": self.title}

    def close(self): self.closed = True


class FakeApp:
    def __init__(self, running=False, items=(), login_urls=None):
        self.running = running
        self.items = items
        self.login_urls = login_urls or {}
        self.marks = []

    def iter_selected_merchants(self):
        for it in self.items:
            yield it

    def set_platform_status(self, key, status):
        self.marks.append((key, status))


def _make_factory(created):
    def factory(key, merchant):
        b = FakeBrowser(f"https://{key}.example.com", "首页", log=list())
        created.append((key, merchant, b))
        return b
    return factory


def test_run_once_visits_each_merchant_in_order():
    app = FakeApp(items=[("youzan", "旗舰店A"), ("youzan", "旗舰店B")],
                  login_urls={"youzan": "https://youzan.example.com/login"})
    created = []
    svc = KeepAliveService(app, make_browser=_make_factory(created))
    svc.run_once()
    assert [(k, m) for k, m, _ in created] == [("youzan", "旗舰店A"), ("youzan", "旗舰店B")]
    assert all(b.closed for _, _, b in created)


def test_run_once_marks_expired_login_red():
    def factory(key, merchant):
        return FakeBrowser("https://x/login", "登录", log=list())
    app = FakeApp(items=[("youzan", "A")], login_urls={"youzan": "https://x/login"})
    svc = KeepAliveService(app, make_browser=factory)
    svc.run_once()
    assert ("youzan", "error") in app.marks


def test_run_once_marks_ok_green():
    def factory(key, merchant):
        return FakeBrowser("https://x/home", "首页", log=list())
    app = FakeApp(items=[("youzan", "A")], login_urls={"youzan": "https://x/login"})
    svc = KeepAliveService(app, make_browser=factory)
    svc.run_once()
    assert ("youzan", "ok") in app.marks


def test_title_with_login_word_alone_does_not_turn_the_light_red(monkeypatch):
    """登录着但页面标题里带"登录"字样(后台常见)以前会把灯判红, 于是出现
    "灯红着、导出却一切正常" —— 状态灯一旦不可信就没人看了。判失效只看 URL。"""
    import core.keepalive as ka
    lines = []
    monkeypatch.setattr(ka, "log", lambda msg, level="info": lines.append((level, msg)))

    def factory(key, merchant):
        return FakeBrowser("https://x/home", "登录 - 商家中心", log=list())
    app = FakeApp(items=[("youzan", "A")], login_urls={"youzan": "https://x/login"})
    KeepAliveService(app, make_browser=factory).run_once()
    assert app.marks == [("youzan", "ok")], "标题字样不该置红"
    assert any("只当提示" in m for _l, m in lines), "但要在日志里说一句为什么没判红"


def test_url_still_decides_even_when_title_looks_logged_in(monkeypatch):
    """反向也要成立: 标题正常但地址落在登录路径 → 仍然判失效。"""
    import core.keepalive as ka
    lines = []
    monkeypatch.setattr(ka, "log", lambda msg, level="info": lines.append((level, msg)))

    def factory(key, merchant):
        return FakeBrowser("https://x/login", "商家中心", log=list())
    app = FakeApp(items=[("youzan", "A")], login_urls={"youzan": "https://x/login"})
    KeepAliveService(app, make_browser=factory).run_once()
    assert app.marks == [("youzan", "error")]
    assert any(l == "warning" for l, _m in lines)


def test_gui_app_matches_keepalive_callback_names():
    """真 app 必须提供保活要用的公共方法名。
    FakeApp 只证明保活侧调用对了, 名字对不上时巡检结果会静默丢弃。"""
    from core.main_gui import LiushuiApp
    for name in ("set_platform_status", "iter_selected_merchants"):
        assert callable(getattr(LiushuiApp, name, None)), name