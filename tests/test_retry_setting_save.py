"""勾"失败重试"只该写它自己那一个键。

设置区里的重试勾选框以前每次都顺手把 `retry_interval_s` 写回默认 30 —— 界面上根本没有
这个输入框, 值只能手工改 settings.json, 于是"把首次间隔调成 5 秒"这件事, 用户下一次
点一下勾选框就没了, 而且毫无提示。同一个写法在保活开关、文件名模式那几处也一样会把
别的设置抹平(那部分在 tests/test_config_io.py 里锁)。
"""
import json

import pytest

import core.config as cfg
import core.main_gui as mg
from core.main_gui import LiushuiApp


class _Var:
    def __init__(self, value):
        self._v = value

    def get(self):
        return self._v


class _TimesBox:
    def __init__(self, text):
        self._t = text

    def get(self):
        return self._t


class _App:
    _on_retry_setting = LiushuiApp._on_retry_setting

    def __init__(self, enabled=True, text="3"):
        self.enable_retry_var = _Var(enabled)
        self.retry_times = _TimesBox(text)
        self.settings = dict(cfg.DEFAULT_SETTINGS)


@pytest.fixture()
def written(monkeypatch):
    calls = []
    monkeypatch.setattr(mg, "save_settings", lambda d: calls.append(dict(d)))
    return calls


def test_toggle_writes_only_retry_times(written):
    app = _App(enabled=True, text="3")
    app._on_retry_setting()
    assert written == [{"retry_times": 3}]
    assert "retry_interval_s" not in written[0]
    assert app.settings["retry_times"] == 3       # 内存镜像也跟上, 同一会话内读得到


def test_unchecking_disables_retry_without_touching_the_interval(written):
    app = _App(enabled=False, text="4")
    app._on_retry_setting()
    assert written == [{"retry_times": 0}]


@pytest.mark.parametrize("text,expected", [("9", 5), ("-2", 0), ("abc", 2), ("", 2)])
def test_weird_spinbox_text_falls_back_instead_of_crashing(written, text, expected):
    """Spinbox 里能手打进任何东西, 这里不能因为一个字符就把界面回调抛出去。"""
    app = _App(enabled=True, text=text)
    app._on_retry_setting()
    assert written == [{"retry_times": expected}]


def test_interval_survives_a_real_save_cycle(tmp_path, monkeypatch):
    """端到端: 文件里写着 5 秒, 点一下勾选框之后还得是 5 秒。"""
    path = str(tmp_path / "settings.json")
    monkeypatch.setattr(cfg, "SETTINGS_FILE", path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(cfg.DEFAULT_SETTINGS, retry_interval_s=5,
                       download_name_mode="original"), f)
    app = _App(enabled=True, text="1")
    app._on_retry_setting()
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["retry_interval_s"] == 5
    assert saved["download_name_mode"] == "original"
    assert saved["retry_times"] == 1
