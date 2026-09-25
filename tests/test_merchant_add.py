"""添加商户必须查重: 两行同名等于"勾了没勾"。

复现的是原来的 `+` 流程: 名字一样时 `os.makedirs(exist_ok=True)` 照样成功, 界面照样
塞一行, 但 `merchant_vars[平台][商户名]` 是字典 —— 新的 BooleanVar 把旧的顶掉, 于是
屏幕上出现两行同名, 勾上面那一行改的是同一个键, 底下那行永远灰着; 重启按目录重建才塌成
一行, 用户以为自己勾了。现在重名直接拒绝, 目录也不建。
"""
import os

import pytest

from core import main_gui
from core.main_gui import LiushuiApp, find_duplicate_merchant
from core.outputs import sanitize_name


class _Plat:
    key = "youzan"
    name = "有赞"


class _App:
    """只给 `_add_merchant` 用到的那几样, 不建 Tk 窗口。"""
    _sanitize_name = staticmethod(sanitize_name)

    def __init__(self, merchants, merchant_vars):
        self.platforms = {"youzan": _Plat()}
        self.merchants = merchants
        self.merchant_vars = merchant_vars
        self.added = []

    def _add_merchant_checkbox(self, key, name, checked=True):
        self.added.append((key, name, checked))
        self.merchant_vars.setdefault(key, {})[name] = object()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    base = tmp_path / "youzan"
    (base / "旗舰店A").mkdir(parents=True)          # 已建档的那家
    monkeypatch.setattr(main_gui, "BROWSER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main_gui, "discover_merchants",
                        lambda keys: {"youzan": sorted(os.listdir(str(base)))})
    return _App({"youzan": ["旗舰店A"]}, {"youzan": {"旗舰店A": object()}})


def _add(app, raw):
    return LiushuiApp._add_merchant(app, "youzan", raw)


def test_duplicate_name_is_refused_and_adds_no_row(app):
    ok, msg = _add(app, "旗舰店A")
    assert ok is False
    assert "旗舰店A" in msg and "已经存在" in msg
    assert app.added == [], "重名不该再塞一行勾选框"
    assert sorted(os.listdir(os.path.join(str(main_gui.BROWSER_DATA_DIR), "youzan"))) \
        == ["旗舰店A"], "重名也不该动目录"


def test_name_differing_only_in_case_or_spaces_is_the_same_merchant(app):
    for raw in ("旗舰店a", " 旗舰店A ", "  旗舰店A"):
        ok, msg = _add(app, raw)
        assert ok is False, raw
        assert "已经存在" in msg
    assert app.added == []


def test_new_name_creates_profile_dir_and_one_row(app):
    ok, msg = _add(app, "新店B")
    assert ok is True
    assert msg == '已添加商户:有赞 / 新店B,请对其执行"首次登录"'
    assert app.added == [("youzan", "新店B", True)]
    assert os.path.isdir(os.path.join(
        os.path.abspath(main_gui.BROWSER_DATA_DIR), "youzan", "新店B"))


def test_blank_name_is_rejected_without_a_dir(app):
    for raw in ("", "   "):
        ok, msg = _add(app, raw)
        assert ok is False, repr(raw)
        assert msg == "商户名称不能为空"
    assert app.added == []


def test_dot_only_names_cannot_escape_the_profile_folder(app):
    """`sanitize_name` 只换掉 <>:"/\\ 等字符, ".." 原样通过 —— 拼进路径就指到
    browser_data 本身, 会把别的商户的登录目录当成自己的 profile。"""
    for raw in ("..", ".", "....", "___", "///"):
        ok, msg = _add(app, raw)
        assert ok is False, repr(raw)
        assert "实际内容" in msg
    assert app.added == []
    assert sorted(os.listdir(os.path.join(str(main_gui.BROWSER_DATA_DIR), "youzan"))) \
        == ["旗舰店A"]


def test_duplicate_check_ignores_case_and_edges():
    assert find_duplicate_merchant(" Shop A ", ["shop a", "其它"]) == "shop a"
    assert find_duplicate_merchant("新店", []) == ""
    assert find_duplicate_merchant("", ["旗舰店A"]) == ""
    assert find_duplicate_merchant("新店", [None, ""]) == ""


def test_duplicate_check_survives_a_missing_existing_list():
    assert find_duplicate_merchant("新店", None) == ""
