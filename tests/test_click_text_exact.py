"""exact=True 的点击不能被自己的回退链削弱成子串匹配。

复现的是拼多多那一类页面: "导出" 按钮和"导出历史"入口同屏。click_text 在真实按钮
点不动(被遮挡/不可交互而抛错)时会走回退链; 回退1 的"忽略空白"正则以前不锚定, 于是
exact=True 也能命中 DOM 里更靠前的"导出历史", 点开完全不相干的入口。回退2 是 JS 直接
el.click(), 只按首路的确切选择器取元素, 因此不会跑偏。

等待文字出现(is_visible_text)是另一套语义: 页面上出现相关文字就算到位, 所以那条路径
保持原来的非锚定匹配, 本文件特意钉住这一点。
"""
import re

from core.browser import BrowserManager


class _Node:
    def __init__(self, page, texts):
        self._page = page
        self._texts = texts

    def count(self):
        return len(self._texts)

    @property
    def first(self):
        if not self._texts:
            raise LookupError("没有匹配元素")
        return self

    def click(self, timeout=None):
        name = self._texts[0]
        if name in self._page.blocked:
            raise TimeoutError("元素不可交互")
        self._page.clicked.append(name)

    def wait_for(self, timeout=None):
        if not self._texts:
            raise TimeoutError("等待超时")

    def evaluate(self, script):
        # JS 的 el.click() 不看可见性与遮挡, 一律成功 —— 与真实浏览器一致
        self._page.clicked.append(self._texts[0])


class FakePage:
    """按 Playwright 语义实现 get_by_text: 字符串看 exact, 正则一律按 search。"""

    def __init__(self, texts, blocked=()):
        self.texts = texts
        self.blocked = set(blocked)
        self.clicked = []

    def get_by_text(self, needle, exact=False):
        hit = []
        for t in self.texts:
            if isinstance(needle, re.Pattern):
                if needle.search(t):
                    hit.append(t)
            elif exact:
                if t.strip() == needle.strip():
                    hit.append(t)
            elif needle.lower() in t.lower():
                hit.append(t)
        return _Node(self, hit)


def _mgr(texts, blocked=()):
    b = BrowserManager(headless=True)          # 不 start(), 不碰浏览器
    b.page = FakePage(texts, blocked)
    b._log = lambda msg, level="info": None
    return b


def test_exact_click_falls_back_to_the_same_button_not_a_longer_label():
    # "导出"按钮被遮挡 → 首路抛错 → 回退链必须仍然只认这一个按钮
    b = _mgr(["导出历史", "导出"], blocked=["导出"])
    assert b.click_text("导出", exact=True, retries=1) is True
    assert b.page.clicked == ["导出"]


def test_exact_click_does_not_click_a_longer_label():
    b = _mgr(["导出历史"])
    assert b.click_text("导出", exact=True, retries=1) is False
    assert b.page.clicked == []


def test_non_exact_click_still_takes_the_substring_label():
    b = _mgr(["导出历史"])
    assert b.click_text("导出", retries=1) is True
    assert b.page.clicked == ["导出历史"]


def test_whole_text_pattern_anchors_but_default_does_not():
    assert not BrowserManager._text_pattern("导出", whole=True).search("导出历史")
    assert BrowserManager._text_pattern("导出", whole=True).search(" 导 出 ")
    # 等待路径保持旧行为: 出现相关文字就算页面到位了
    assert BrowserManager._text_pattern("导出").search("导出历史")


def test_wait_for_text_still_matches_inside_a_longer_label():
    b = _mgr(["本月可导出 12 条记录"])
    assert b.is_visible_text("导出", timeout=0) is True
