"""浏览器窗口尺寸与页面视口必须一致(有头)。

实测(1.62 / chromium-1234, 1920x1080 屏):
- `viewport=None` 不是"不设视口" —— Python 绑定把 None 当"没传"丢掉
  (playwright/_impl/_helper.py: locals_to_params 跳过 None), 生效的是默认 1280x720
  固定视口。窗口拉到 1920x1080 时页面仍是 1280x720, 右边 624px、下面 265px 全空白。
- `--window-size` 会把 `--start-maximized` 顶掉: 两个都给时窗口是 normal 态
  1920x1080, 只给 `--start-maximized` 才是 maximized。
- 无头没有窗口可跟, `no_viewport=True` 会退到 800x600(预检/快照的版面会变),
  所以它必须留着默认视口。
"""
import pytest

import core.browser as bm


class _Page:
    def screenshot(self, **kw):
        pass


class _Ctx:
    def __init__(self, sink):
        self.pages = [_Page()]
        self.sink = sink

    def on(self, event, handler):
        pass

    def close(self):
        pass


class _Recorder:
    """记下真正发给 Playwright 的参数 —— 被丢掉的 None 不会出现在这里。"""

    def __init__(self):
        self.kwargs = []

    def launch_persistent_context(self, **kwargs):
        self.kwargs.append(kwargs)
        return _Ctx(self)


@pytest.fixture()
def recorder(tmp_path, monkeypatch):
    sink = _Recorder()

    class _PW:
        chromium = sink

        def stop(self):
            pass

    class _Manager:
        def start(self):
            return _PW()

    monkeypatch.setattr(bm, "BROWSER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bm, "sync_playwright", lambda: _Manager())
    return sink


def _start(recorder, headless):
    b = bm.BrowserManager(headless=headless)
    b.set_browser_profile("fake", "商户A")
    b.start()
    return recorder.kwargs[0]


def test_headful_releases_the_viewport_so_content_fills_the_window(recorder):
    kw = _start(recorder, headless=False)
    assert kw.get("no_viewport") is True
    # viewport 一旦传进来(哪怕就是那个默认值)就是固定尺寸, 窗口再大也只画左上角
    assert "viewport" not in kw


def test_headless_keeps_the_default_viewport(recorder):
    """无头没有窗口, 跟着窗口走等于退到 800x600 —— 预检和快照不能吃这个亏。"""
    kw = _start(recorder, headless=True)
    assert kw.get("no_viewport") is False


def test_window_size_flag_is_not_passed(recorder):
    """`--window-size` 实测会把 `--start-maximized` 顶掉, 不许再加回来。"""
    kw = _start(recorder, headless=False)
    assert any(a == "--start-maximized" for a in kw["args"])
    assert not any(a.startswith("--window-size") for a in kw["args"])
