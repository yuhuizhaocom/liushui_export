"""「使用说明」弹窗的文案要跟得上交互改动。

第九轮把首次登录从"登完回来点确定"改成了"关掉浏览器窗口就代表登完", 但弹窗文案
还停在旧说法上 —— 用户照旧说法在没登完时点掉窗口, 正是那次改造要治的病。
所以文案本身要有测试钉住。
"""
import types

from core import main_gui
from core.main_gui import LiushuiApp


def _shown_text(monkeypatch):
    shown = {}

    def fake_showinfo(title, message):
        shown['title'] = title
        shown['message'] = message

    monkeypatch.setattr(main_gui.messagebox, 'showinfo', fake_showinfo)
    app = types.SimpleNamespace()
    LiushuiApp._action_help(app)
    return shown.get('message', '')


def test_help_matches_close_window_flow(monkeypatch):
    text = _shown_text(monkeypatch)
    assert '关掉' in text, '要说清登完的标志是关掉浏览器窗口'
    assert '点"确定"' not in text, '不该再教用户登完回去点确定'


def test_help_mentions_the_two_safety_nets(monkeypatch):
    """开跑前的登录预检、跑起来后的"中止"都已进主流程, 弹窗里得有一句。"""
    text = _shown_text(monkeypatch)
    assert '中止' in text
    assert '登录' in text and '检查' in text
