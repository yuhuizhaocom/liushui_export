"""刷新商户: 手工建/从别的电脑拷进来的 profile 目录, 不重启也该能看见。

以前 `discover_merchants` 只在启动、新增商户、平台管理重建列表时跑, 所以往
`browser_data/有赞/` 里放一个新目录, 界面上要重启才有; 商户数徽标建好之后也不再更新。
"""
from core.main_gui import LiushuiApp, merchant_changes


class _Plat:
    def __init__(self, key, name):
        self.key = key
        self.name = name


class _App:
    """只给 _refresh_merchants 用到的成员; 重建列表这一步用假的, 不碰 Tk。"""

    _refresh_merchants = LiushuiApp._refresh_merchants

    def __init__(self, before, after):
        self.platforms = {"youzan": _Plat("youzan", "有赞"), "tmall": _Plat("tmall", "天猫")}
        self.merchants = before
        self._after = after
        self.logs = []
        self.rebuilt = 0

    def _rebuild_platform_list(self):
        self.rebuilt += 1
        self.merchants = dict(self._after)

    def _append_log(self, line):
        self.logs.append(line)


def test_new_folder_on_disk_is_reported_and_listed():
    app = _App({"youzan": ["旗舰店A"]}, {"youzan": ["旗舰店A", "新店C"], "tmall": ["旗舰B"]})
    app._refresh_merchants()
    assert app.rebuilt == 1
    assert any("新增 有赞/新店C" in line for line in app.logs), app.logs
    assert any("新增 天猫/旗舰B" in line for line in app.logs), app.logs


def test_folder_removed_outside_the_tool_is_reported():
    app = _App({"youzan": ["旗舰店A", "打错了"]}, {"youzan": ["旗舰店A"]})
    app._refresh_merchants()
    assert any("已消失 有赞/打错了" in line for line in app.logs), app.logs


def test_nothing_changed_says_so_with_the_total():
    app = _App({"youzan": ["旗舰店A", "旗舰店B"]}, {"youzan": ["旗舰店B", "旗舰店A"]})
    app._refresh_merchants()
    assert app.logs == ["[刷新商户] 没有变化(当前共 2 家商户)"], app.logs


def test_a_failing_rebuild_does_not_escape_the_button():
    class _Boom(_App):
        def _rebuild_platform_list(self):
            raise RuntimeError("控件已经销毁了")

    app = _Boom({"youzan": ["A"]}, {"youzan": ["A"]})
    app._refresh_merchants()          # 不向外抛: 按钮按下去不能把程序带崩
    assert any("刷新失败" in line for line in app.logs), app.logs


def test_merchant_changes_ignores_order_and_reports_both_directions():
    added, removed = merchant_changes({"a": ["x", "y"]}, {"a": ["y", "x"], "b": ["z"]})
    assert added == {"b": ["z"]} and removed == {}
    added, removed = merchant_changes({"a": ["x", "y"]}, {"a": ["y"]})
    assert added == {} and removed == {"a": ["x"]}


def test_merchant_changes_tolerates_missing_sides():
    assert merchant_changes({}, {"a": ["x"]}) == ({"a": ["x"]}, {})
    assert merchant_changes(None, None) == ({}, {})
