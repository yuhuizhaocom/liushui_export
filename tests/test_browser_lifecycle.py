"""浏览器启动失败路径的资源清理。

start() 里 launch_persistent_context 之后的步骤(挂下载监听、恢复登录态)也可能抛错,
那时 context 是一个活着的 Chromium 进程; 只把 self.context 置 None 而不 close,
profile 目录会被它一直锁着, 之后每次启动都失败在同一个商户上。
"""
import pytest

import core.browser as bm


class _Ctx:
    def __init__(self, log):
        self.pages = []
        self.log = log

    def on(self, event, handler):
        pass

    def new_page(self):
        return object()

    def close(self):
        self.log.append("closed")


class _PW:
    """对应 sync_playwright().start() 返回的 playwright 对象。"""

    def __init__(self, log):
        self.log = log

        class _Chromium:
            def launch_persistent_context(inner, **kwargs):
                log.append("launched")
                return _Ctx(log)
        self.chromium = _Chromium()

    def stop(self):
        self.log.append("stopped")


class _PWManager:
    """对应 sync_playwright() 返回的 manager: 它的 .start() 才给出 playwright。"""

    def __init__(self, log):
        self.log = log

    def start(self):
        return _PW(self.log)


@pytest.fixture()
def mgr(tmp_path, monkeypatch):
    log = []
    monkeypatch.setattr(bm, "BROWSER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bm, "sync_playwright", lambda: _PWManager(log))
    monkeypatch.setattr(bm.time, "sleep", lambda s: None)   # 别真等 3 次重试
    b = bm.BrowserManager(headless=True)
    b.set_browser_profile("fake", "商户A")
    return b, log


def test_start_closes_context_when_post_launch_step_fails(mgr, monkeypatch):
    b, log = mgr

    def boom():
        raise RuntimeError("恢复登录态失败")
    monkeypatch.setattr(b, "_restore_login_state", boom)

    with pytest.raises(RuntimeError):
        b.start()
    assert log.count("launched") == 3                 # 三次尝试
    assert log.count("closed") == 3                   # 每次都关掉了活的 context
    assert log.count("stopped") == 3                  # playwright 也没泄漏
    assert b.context is None and b.page is None


def test_start_success_leaves_context_open(mgr):
    b, log = mgr
    b.start()
    assert log.count("launched") == 1
    assert "closed" not in log
    assert b.context is not None
