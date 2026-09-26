"""子商户循环: 钩子的默认值、分发、以及"归属没确认就停手"的语义。

这一组测试盯的不是"能不能多导几份", 而是**什么情况下绝不点导出**。省登录次数这件事
顺带省掉了"人眼看着切对了才按导出"那道人工确认, 所以机器这边必须守得住:
切换失败 / 页面读回来不是要导的那一家 → 整家停手转人工, 宁可一份都不出。
"""
import os
import sys

import pytest

import core.main_gui as mg
import core.submerchants as sm
from core.loader import discover_platforms
from core.main_gui import LiushuiApp
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
        self.contexts = []
        self.snapshots = []
        self.step_debug = None
        self.user_cb = None

    def _log(self, msg, level="info"):
        self.logs(msg, level)

    def click_text(self, text):
        self.clicked.append(text)
        return True

    def set_export_context(self, platform="", start="", end="", merchant="", sub_merchant=""):
        self.contexts.append((platform, merchant, sub_merchant))

    def set_step_debug(self, flag, cb=None):
        self.step_debug = (flag, cb)

    def set_user_wait_callback(self, cb):
        self.user_cb = cb

    def snapshot(self, name):
        self.snapshots.append(name)


def _plat(switch_ok=True, on_page="8234000540", read_raises=None, supports=True):
    """一个假平台: 切换成功与否、页面读回是谁, 都由参数摆出来。"""
    def _switch(self, browser, sub):
        browser.clicked.append("switch:" + sub)
        return switch_ok

    def _current(self, browser):
        if read_raises:
            raise read_raises
        return on_page

    return type("P", (PlatformBase,), {
        "key": "yinlian", "name": "银联", "export_url": "https://x/export",
        "supports_sub_merchants": supports,
        "switch_sub_merchant": _switch, "current_sub_merchant": _current,
        "check_login": lambda self, browser: True,
        "export": lambda self, browser, s, e: getattr(self, "verdict", "success"),
    })()


class _App:
    """只挂循环用到的那几个成员, 不建 Tk、不起浏览器; 导出那一步用记录用的假件。"""

    def __init__(self, results=None, switch_ok=True):
        self.logs = []
        self.browser = _FakeBrowser()
        self.calls = []
        self.abort = False
        self._results = list(results or [])
        self.switch_ok = switch_ok

    def _append_log(self, msg, level="info"):
        self.logs.append((level, msg))

    def _aborted(self):
        return self.abort

    def _ensure_browser(self, plat=None, merchant="", force_visible=False):
        pass

    def _ui(self, fn):
        fn()

    def _switch_sub_merchant(self, plat, merchant, sub):
        self.calls.append(("switch", merchant, sub))
        return self.switch_ok

    def _export_one_merchant(self, plat, merchant, start, end, step_debug=False,
                             sub_merchant=""):
        self.calls.append(("export", merchant, sub_merchant))
        if self._results:
            return self._results.pop(0)
        return "success"

    def _run_sub_merchants(self, plat, merchant, subs, start, end, step_debug):
        """分发性测试要的是"真的走进那套循环", 所以这里转给真实现。"""
        return LiushuiApp._run_sub_merchants(self, plat, merchant, subs, start, end, step_debug)

    def _self_check_selectors(self, plat):
        pass

    def set_platform_status(self, key, status):
        pass

    def text(self):
        return "\n".join(m for _l, m in self.logs)


@pytest.fixture()
def store(monkeypatch):
    """把清单来源换成内存里的映射: 测试不该依赖工作空间里真有一份 json。"""
    data = {}

    def _get(key, merchant, data_=None, path=None):
        return list(data.get(merchant, [])), ""

    monkeypatch.setattr(sm, "get", _get)
    monkeypatch.setattr(mg, "submerchants", sm)
    # 关掉重试: 否则一次 "failed" 会真的睡 30 秒, 还会把排好的返回值吃掉
    monkeypatch.setattr(mg, "load_settings", lambda: {"retry_times": 0, "retry_interval_s": 0})
    return data


def _subs(store, merchant, names):
    store[merchant] = names


# ===== 钩子默认值 =====

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
    """读回默认是「读不到」; 空串走未校验分支, 绝不算匹配通过。"""
    assert PlatformBase().current_sub_merchant(_FakeBrowser()) == ""


# ===== 分发: 没声明/没清单的商户一步都不变 =====

def test_platform_without_support_never_looks_at_the_list(store):
    _subs(store, "主账号甲", ["8234000540"])
    app = _App()
    got = LiushuiApp._run_single_export(app, _plat(supports=False), "主账号甲",
                                        "2026-09-01", "2026-09-02")
    assert got == "success"
    assert app.calls == [("export", "主账号甲", "")], "没声明支持的平台不该被循环"


def test_supported_but_empty_list_is_a_plain_single_export(store):
    app = _App()
    assert LiushuiApp._run_single_export(app, _plat(), "主账号甲",
                                         "2026-09-01", "2026-09-02") == "success"
    assert app.calls == [("export", "主账号甲", "")]


def test_each_sub_merchant_gets_one_switch_then_one_export(store):
    _subs(store, "主账号甲", ["8234000540", "8234000541"])
    app = _App()
    assert LiushuiApp._run_single_export(app, _plat(), "主账号甲",
                                         "2026-09-01", "2026-09-02") == "success"
    assert app.calls == [("switch", "主账号甲", "8234000540"),
                         ("export", "主账号甲", "8234000540"),
                         ("switch", "主账号甲", "8234000541"),
                         ("export", "主账号甲", "8234000541")], "顺序必须是切一个导一个"


# ===== 归属守卫: 三种情况分得很清 =====

def test_failed_switch_stops_before_any_export():
    app = _App()
    plat = _plat(switch_ok=False)
    assert LiushuiApp._switch_sub_merchant(app, plat, "主账号甲", "8234000540") is False
    assert "中止" in app.text() and "8234000540" in app.text()
    assert app.calls == []


def test_wrong_merchant_on_page_stops_and_snapshots():
    app = _App()
    plat = _plat(on_page="8234000541")          # 页面上是另一家
    assert LiushuiApp._switch_sub_merchant(app, plat, "主账号甲", "8234000540") is False
    assert app.browser.snapshots, "停手时要留下页面证据"
    assert "8234000541" in app.text() and "8234000540" in app.text()


def test_missing_readback_continues_but_says_it_was_not_verified():
    """没实现读回 ≠ 读对了: 放行(否则这功能没法用), 但必须留下一句"归属未经校验"。"""
    app = _App()
    assert LiushuiApp._switch_sub_merchant(app, _plat(on_page=""), "主账号甲", "A") is True
    assert "未经校验" in app.text() and "中止" not in app.text()


def test_readback_that_throws_is_treated_as_unverified_not_as_mismatch():
    app = _App()
    plat = _plat(read_raises=RuntimeError("元素没了"))
    assert LiushuiApp._switch_sub_merchant(app, plat, "主账号甲", "A") is True
    assert "未经校验" in app.text()


def test_switch_that_throws_stops():
    app = _App()
    plat = _plat(switch_ok=True)
    plat.switch_sub_merchant = lambda browser, sub: (_ for _ in ()).throw(RuntimeError("崩了"))
    assert LiushuiApp._switch_sub_merchant(app, plat, "主账号甲", "A") is False
    assert "转人工" in app.text()


def test_matching_readback_is_silent_and_passes():
    app = _App()
    assert LiushuiApp._switch_sub_merchant(app, _plat(on_page="商户号：8234000540"),
                                           "主账号甲", "8234000540") is True
    assert "未经校验" not in app.text()


# ===== 循环层的结论与止损 =====

def test_guard_failure_stops_the_rest_of_the_family(store):
    _subs(store, "主账号甲", ["A1", "A2", "A3"])
    app = _App(switch_ok=False)
    assert LiushuiApp._run_sub_merchants(app, _plat(), "主账号甲", ["A1", "A2", "A3"],
                                         "2026-09-01", "2026-09-02", False) == "manual"
    assert [c[0] for c in app.calls] == ["switch"], "第一条切不动就不该再试后面的"


def test_partial_failure_is_reported_as_manual_not_failed(store):
    """整家结论永远不是 "failed" —— 外层据此重试会把成功的几家重导一遍。"""
    _subs(store, "甲", ["A1", "A2"])
    app = _App(results=["success", "manual"])
    assert LiushuiApp._run_sub_merchants(app, _plat(), "甲", ["A1", "A2"],
                                         "2026-09-01", "2026-09-02", False) == "manual"


def test_all_failed_still_returns_manual_so_the_batch_does_not_redo_it(store):
    app = _App(results=["failed", "failed"])
    got = LiushuiApp._run_sub_merchants(app, _plat(), "甲", ["A1", "A2"],
                                        "2026-09-01", "2026-09-02", False)
    assert got == "manual"
    assert [c[2] for c in app.calls if c[0] == "export"] == ["A1", "A2"]


def test_abort_flag_stops_between_sub_merchants(store):
    app = _App()
    app.abort = True
    assert LiushuiApp._run_sub_merchants(app, _plat(), "甲", ["A1", "A2"],
                                         "2026-09-01", "2026-09-02", False) == "manual"
    assert app.calls == []


def test_per_sub_retry_uses_the_same_settings_as_the_batch(monkeypatch, store):
    """重试发生在每个子商户自己身上; 且"间隔 0"就是 0 —— 别睡 30 秒。

    后半句不是洁癖: 这一档最初写成 `int(settings.get("retry_interval_s", 30) or 30)`,
    把间隔设成 0 的机器每次重试反而要睡满 30 秒(0 被 `or` 吃掉了)。
    """
    monkeypatch.setattr(mg, "load_settings", lambda: {"retry_times": 2, "retry_interval_s": 0})
    slept = []
    monkeypatch.setattr(mg.time, "sleep", lambda s: slept.append(s))
    seen = []

    def _export(self, plat, merchant, start, end, step_debug=False, sub_merchant=""):
        seen.append(sub_merchant)
        return "success" if seen.count(sub_merchant) >= 2 else "failed"

    monkeypatch.setattr(_App, "_export_one_merchant", _export)
    app = _App()
    got = LiushuiApp._run_sub_merchants(app, _plat(), "甲", ["A1"], "2026-09-01",
                                        "2026-09-02", False)
    assert got == "success" and seen == ["A1", "A1"]
    assert slept == [0] or slept == [], f"重试间隔 0 却睡了 {slept}"


# ===== 真正跑一次导出时, 上下文与统计都带子商户 =====

def test_export_one_sets_the_sub_merchant_into_the_download_context(monkeypatch):
    recs = []
    monkeypatch.setattr(mg, "record_stat", lambda *a, **k: recs.append((a, k)))
    app = _App()
    got = LiushuiApp._export_one_merchant(app, _plat(), "主账号甲", "2026-09-01",
                                         "2026-09-02", sub_merchant="8234000540")
    assert got == "success"
    assert app.browser.contexts == [("银联", "主账号甲", "8234000540")]
    # 统计里记成 "主/子": 看板上要看得出是哪个子商户没成功
    assert recs[0][0][1] == "主账号甲/8234000540"
    assert "8234000540" in app.text()


def test_export_one_without_sub_merchant_keeps_the_old_stat_label(monkeypatch):
    recs = []
    monkeypatch.setattr(mg, "record_stat", lambda *a, **k: recs.append((a, k)))
    app = _App()
    LiushuiApp._export_one_merchant(app, _plat(supports=False), "主账号甲",
                                    "2026-09-01", "2026-09-02")
    assert recs[0][0][1] == "主账号甲"
    assert app.browser.contexts == [("银联", "主账号甲", "")]


# ===== 界面入口 =====

def test_readback_support_is_reported_per_platform():
    """界面要靠这个判断要不要提醒"归属不会被校验": 只认脚本有没有真的覆盖。"""
    assert PlatformBase().verifies_sub_merchant_identity() is False
    assert _plat(on_page="8234000540").verifies_sub_merchant_identity() is True

    class _Empty(PlatformBase):
        def current_sub_merchant(self, browser):
            return ""          # 覆盖了但永远读不到, 等于没校验

    assert _Empty().verifies_sub_merchant_identity() is True, "覆盖过就该说覆盖了(读不到另有提醒)"


def _tk_or_skip():
    pytest.importorskip("tkinter")
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception:                       # 远程会话/没有窗口站: 界面测不了就跳
        pytest.skip("这台机器起不了 Tk 窗口")
    root.withdraw()
    return root


def _walk(widgets, out=None):
    """摊平整棵控件树。

    ⚠ 必须传**列表**: tkinter 的 widget 自身就有 `append`(= pack_append)和 `__iter__`,
    把一个 Frame 当 accumulator 传进来会变成"边遍历边往容器上挂控件"的死循环(第一版就卡在这)。
    """
    if not isinstance(widgets, (list, tuple)):
        widgets = [widgets]
    out = [] if out is None else out
    for w in widgets:
        out.append(w)
        try:
            kids = list(w.winfo_children())
        except Exception:
            kids = []
        if kids:
            _walk(kids, out)
    return out


def _button_texts(widget):
    import tkinter as tk
    return [w.cget("text") for w in _walk(widget) if isinstance(w, tk.Button)]


class _RowApp:
    """只挂 `_add_merchant_checkbox` 用到的成员, 让真方法在临时窗口里长出一行。"""

    def __init__(self, root, plat):
        import tkinter as tk
        self.root = root
        self.platforms = {"yinlian": plat}
        self.selection = {"merchant": {}}
        self.merchant_frames = {"yinlian": tk.Frame(root)}
        self.merchant_rows = {}
        self.merchant_vars = {}
        self.platform_vars = {}
        self.rebuilds = 0

    def _refresh_summary(self):
        pass

    def _save_selection(self):
        pass

    def _sync_platform_on_merchant(self, key, *args):
        pass

    def _action_open_merchant(self, key, name):
        pass

    def _prompt_delete_merchant(self, key, name):
        pass

    def _rebuild_platform_list(self):
        self.rebuilds += 1


def _button_texts(widget):
    import tkinter as tk
    return [w.cget("text") for w in _walk([widget]) if isinstance(w, tk.Button)]


def test_the_entry_only_shows_up_on_platforms_that_support_it():
    root = _tk_or_skip()
    try:
        app = _RowApp(root, _plat(supports=True))
        LiushuiApp._add_merchant_checkbox(app, "yinlian", "主账号甲")
        texts = _button_texts(app.merchant_frames["yinlian"])
        assert any(str(t).startswith("子商户") for t in texts), texts

        app2 = _RowApp(root, _plat(supports=False))
        LiushuiApp._add_merchant_checkbox(app2, "yinlian", "主账号甲")
        assert not any(str(t).startswith("子商户")
                       for t in _button_texts(app2.merchant_frames["yinlian"]))
    finally:
        root.destroy()


def test_the_button_shows_how_many_sub_merchants_are_configured(monkeypatch):
    root = _tk_or_skip()
    try:
        monkeypatch.setattr(sm, "get", lambda key, merchant, data=None, path=None:
                            (["A1", "A2"], ""))
        app = _RowApp(root, _plat())
        LiushuiApp._add_merchant_checkbox(app, "yinlian", "主账号甲")
        assert "子商户 2" in _button_texts(app.merchant_frames["yinlian"])
    finally:
        root.destroy()


def test_the_dialog_lists_saved_sub_merchants_and_saves_edits(monkeypatch, tmp_path):
    """对话框要能看见已录的、改完点保存就落盘并刷新数量 —— 这条走的是真存储路径。"""
    import tkinter as tk
    root = _tk_or_skip()
    try:
        monkeypatch.setattr("core.config.SUB_MERCHANTS_FILE",
                            str(tmp_path / "sub_merchants.json"))
        assert sm.save_one("yinlian", "主账号甲", ["A1", "A2"])[0] is True
        app = _RowApp(root, _plat())
        LiushuiApp._prompt_sub_merchants(app, "yinlian", "主账号甲")
        dialog = [w for w in _walk(root.winfo_children()) if isinstance(w, tk.Toplevel)]
        assert dialog, "没弹出对话框"
        box = [w for w in _walk(dialog) if isinstance(w, tk.Text)][0]
        assert box.get("1.0", tk.END).strip().splitlines() == ["A1", "A2"]
        # 这个假平台实现了读回, 所以不该出现"归属不会被校验"的红字提醒
        assert not any("不会被校验" in str(w.cget("text"))
                       for w in _walk(dialog) if isinstance(w, tk.Label))
        box.delete("1.0", tk.END)
        box.insert("1.0", "A1\nA3\nA3\n")
        save = [w for w in _walk(dialog) if isinstance(w, tk.Button)
                and w.cget("text") == "保存"][0]
        save.invoke()
        assert sm.get("yinlian", "主账号甲")[0] == ["A1", "A3"], "重复行要塌成一个"
        assert app.rebuilds == 1, "保存后要刷新那行按钮上的数量"
    finally:
        root.destroy()
