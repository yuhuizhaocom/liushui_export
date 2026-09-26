"""子商户循环: 基类钩子的默认值与"停手"语义。

这三个钩子的返回值是安全相关的 —— 默认必须落在"不做也没坏事"那一侧:
未声明 supports 的平台完全不进循环(行为与今天一致), 声明了但没实现切换的平台必须**停手**
而不是"当作切好了继续导"。后者一旦写成 True, 就是把 A 的账单记到 B 名下。
"""
import os
import sys

from core.loader import discover_platforms
from core.platform_base import PlatformBase


class _Log:
    def __init__(self):
        self.lines = []

    def __call__(self, msg, level="info"):
        self.lines.append((level, msg))


class _FakeBrowser:
    def __init__(self):
        self.logs = _Log()
        self.clicked = []

    def _log(self, msg, level="info"):
        self.logs(msg, level)

    def click_text(self, text):
        self.clicked.append(text)
        return True


def test_support_is_off_by_everywhere_by_default():
    assert PlatformBase.supports_sub_merchants is False
    # 平台脚本要么不写(=False), 要么写真正的布尔值; 写成字符串/数字会让"没声明"看起来像声明了
    for key, plat in discover_platforms().items():
        assert isinstance(plat.supports_sub_merchants, bool), (key, plat.supports_sub_merchants)


def test_switch_hook_refuses_and_says_why():
    plat = type("P", (PlatformBase,), {"key": "x", "name": "某台"})()
    b = _FakeBrowser()
    assert plat.switch_sub_merchant(b, "8234000540") is False, "默认必须是「切不了」"
    level, msg = b.logs.lines[0]
    assert level == "warning" and "转人工" in msg and "8234000540" in msg
    assert b.clicked == [], "默认实现不许顺手点任何东西"


def test_readback_hook_is_empty_not_matching():
    """读回默认是"读不到"。空串在流程里走"未校验"分支, 绝不算匹配通过。"""
    plat = PlatformBase()
    assert plat.current_sub_merchant(_FakeBrowser()) == ""


def test_declaring_support_is_a_plain_class_attribute():
    plat = type("P", (PlatformBase,), {"supports_sub_merchants": True,
                                       "switch_sub_merchant": lambda self, b, s: True,
                                       "current_sub_merchant": lambda self, b: "8234000540"})()
    assert plat.supports_sub_merchants is True
    assert plat.switch_sub_merchant(_FakeBrowser(), "8234000540") is True
    assert plat.current_sub_merchant(_FakeBrowser()) == "8234000540"
