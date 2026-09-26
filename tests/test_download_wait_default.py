""""下载要等多久"必须只有一个来源。

以前这个数散在七处: `BrowserManager.wait_download` 默认 120、骨架
`PlatformBase.DOWNLOAD_TIMEOUT_S=60`、`platform_base` 的示例注释教 120、
`SmartExporter.export/quick_export` 各写 120、《脚本编写指南》里 4 处 120 又夹一处 60,
再加平台脚本里各家自己显式传的 60/90/120。13.5 挂着"60 够不够要真机量" —— 而真要调,
照着模板抄的新平台和 SmartExporter 那一支都会漏掉, 因为它们是各写各的字面量。

收口方式: 默认值只定义在 `BrowserManager.DEFAULT_DOWNLOAD_WAIT_S` 一处, 上游一律传
`timeout=None` 落回它; 骨架那个 60 是**刻意的更短**, 保留但注明调就调那一处。
平台脚本里显式传值仍然允许(那是各家后台的真实取向), 所以源码扫描只查 core/ 里
"在调用处写死数字"这一种形状。
"""
import inspect
import os

import pytest

import core.browser as bm
from core.browser import BrowserManager
from core.exporters import SmartExporter
from core.platform_base import PlatformBase

CORE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")


class _Clock:
    """假时钟: 等待不花真时间, 但能量出"到底等了多久"。"""

    def __init__(self, start=1000.0):
        self.now = start
        self.slept = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept += max(0.0, seconds)
        self.now += max(0.0, seconds)
        return None


def _fake_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(bm, "DOWNLOAD_DIR", str(tmp_path))
    b = BrowserManager(headless=True)
    b.log_callback = lambda msg, level="info": None
    b.set_export_context("有赞", "2026-09-01", "2026-09-02", "旗舰店A")
    return b


def test_the_default_is_defined_exactly_once():
    assert BrowserManager.DEFAULT_DOWNLOAD_WAIT_S == 120
    assert inspect.signature(BrowserManager.wait_download).parameters["timeout"].default is None
    for fn in (SmartExporter.export, SmartExporter.quick_export):
        assert inspect.signature(fn).parameters["timeout"].default is None, \
            f"{fn.__qualname__} 不许自己再定一个默认值"


def test_omitting_the_timeout_waits_for_the_shared_default(monkeypatch, tmp_path):
    """不传 timeout 时真正生效的就是那个常量 —— 而不是某处偷偷写死的数。"""
    b = _fake_browser(monkeypatch, tmp_path)
    clock = _Clock()
    monkeypatch.setattr(bm, "time", clock)
    assert b.wait_download() is None
    assert clock.slept >= BrowserManager.DEFAULT_DOWNLOAD_WAIT_S, \
        f"只等了 {clock.slept}s, 没走到默认的 {BrowserManager.DEFAULT_DOWNLOAD_WAIT_S}s"


def test_an_explicit_timeout_still_wins(monkeypatch, tmp_path):
    b = _fake_browser(monkeypatch, tmp_path)
    clock = _Clock()
    monkeypatch.setattr(bm, "time", clock)
    assert b.wait_download(timeout=5) is None
    assert 5 <= clock.slept < BrowserManager.DEFAULT_DOWNLOAD_WAIT_S, clock.slept


def test_core_does_not_hardcode_a_wait_at_the_call_site():
    """core/ 里不许再出现 `wait_download(timeout=<数字>)` 这种就地写死。"""
    import re
    pat = re.compile(r"wait_download\(\s*(?:self,\s*)?timeout\s*=\s*\d")
    offenders = []
    for name in sorted(os.listdir(CORE_DIR)):
        if not name.endswith(".py"):
            continue
        src = open(os.path.join(CORE_DIR, name), encoding="utf-8").read()
        for no, line in enumerate(src.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if pat.search(line):
                offenders.append("core/%s:%d: %s" % (name, no, line.strip()))
    assert not offenders, "默认值只有一个来源, 要改去改 DEFAULT_DOWNLOAD_WAIT_S:\n" + \
        "\n".join(offenders)


def test_the_skeleton_deliberately_waits_less_and_says_so():
    """骨架那个 60 是有意的差异, 但它不许反过来变成第二个"默认"。"""
    assert PlatformBase.DOWNLOAD_TIMEOUT_S < BrowserManager.DEFAULT_DOWNLOAD_WAIT_S
    src = inspect.getsource(PlatformBase)
    assert "13.5" in src or "DEFAULT_DOWNLOAD_WAIT_S" in src, \
        "两个数并存必须写明为什么, 不然下次就有人来'对齐'掉这个差异"
