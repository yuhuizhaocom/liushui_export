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