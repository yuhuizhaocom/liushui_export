"""首次登录的「请登录」提示窗: 自己倒计时关掉、能勾"不再弹出"、设置里能改回来。

以前这扇窗是"弹出来就一直在那儿"的浮窗 —— 用户登完关掉浏览器, 它还在桌面上杵着,
得手工再关一次(Toplevel 不是模态框, 关浏览器窗口不会带走它); 熟手每家商户都要看一眼
同一段话。倒计时自关和"不再弹出"治的就是这两件事。

要钉住的还有一条容易弄反的事: 倒计时关的只是**提示窗**, "登录完了"的信号始终是
用户关掉那个浏览器窗口 —— 所以文案里"关掉浏览器窗口"那句话不能因为加了倒计时被挤掉。
"""
import io
import time
import types

import pytest

from core import main_gui
from core.main_gui import LiushuiApp


@pytest.fixture(scope="module")
def root():
    """模块共用的隐藏根窗口 —— 同进程里反复 tk.Tk() 在这台机器上会偶发失败。"""
    tk = pytest.importorskip("tkinter")
    try:
        r = tk.Tk()
    except Exception as e:                      # 无显示环境 / tk 装得不全
        pytest.skip("无法创建 Tk 根窗口: %s" % e)
    r.withdraw()                                # 不在桌面上闪窗口
    yield r
    r.destroy()


@pytest.fixture()
def app(root, monkeypatch):
    """够 _show_login_hint 用的最小 app: 只认 settings / 那几个方法和倒计时心跳。

    方法要一个个绑上: 窗里的倒计时与勾选框是通过 `self._xxx()` 回调回来的, 少绑一个
    不会当场报错 —— Tk 只会把 AttributeError 打给 report_callback_exception, 窗子
    静静地不关, 所以这里显式列全(测试自己有 test_countdown_closes_the_window_by_itself
    盯着"真的关了")。
    """
    saved, logged = [], []

    def fake_save_settings(kv, ui_call=True):
        saved.append(dict(kv))
        return True

    monkeypatch.setattr(main_gui, "save_settings", fake_save_settings)
    a = types.SimpleNamespace(
        root=root, settings={"show_login_hint": True, "login_hint_seconds": 20},
        _login_hint=None, _login_hint_after=None,
        _append_log=logged.append, saved=saved, logged=logged)
    for name in ("_save_setting", "_hint_seconds", "_close_login_hint",
                 "_remember_no_hint"):
        setattr(a, name, getattr(LiushuiApp, name).__get__(a))
    a.LOGIN_HINT_TICK_MS = LiushuiApp.LOGIN_HINT_TICK_MS
    return a


def _open(app, seconds=None, guide="扫码登录"):
    if seconds is not None:
        app.settings["login_hint_seconds"] = seconds
    LiushuiApp._show_login_hint(app, "微信支付", "测试商户", guide)
    if app._login_hint is not None:
        app._login_hint.withdraw()      # 用例里别在桌面上闪窗(几何值不受影响)
    return app._login_hint


def _widgets(win, cls):
    out, stack = [], list(win.winfo_children())
    while stack:
        w = stack.pop()
        if w.winfo_class() == cls:
            out.append(w)
        stack.extend(w.winfo_children())
    return out


def _pump(root, seconds):
    """转 Tk 事件循环若干秒 —— after 回调只有在 update 时才跑。"""
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def _exists(root, win):
    return bool(root.tk.call("winfo", "exists", str(win)))


def test_window_keeps_saying_the_close_browser_signal(app, root):
    """倒计时不是"登完了"的信号, 那句话必须还在窗里。"""
    win = _open(app)
    try:
        texts = [w.cget("text") for w in _widgets(win, "Label")]
        joined = "\n".join(texts)
        assert "关掉" in joined and "浏览器窗口" in joined
        assert "不用回来点确定" in joined
        assert any("自动关闭" in t for t in texts), "得写清这是提示窗自己关, 不是登录完成"
    finally:
        LiushuiApp._close_login_hint(app)


def test_disabled_in_settings_means_no_window(app, root):
    app.settings["show_login_hint"] = False
    win = LiushuiApp._show_login_hint(app, "微信支付", "测试商户", "扫码登录")
    assert win is None and app._login_hint is None
    assert not _widgets(root, "Toplevel"), "关掉开关后不该再冒出提示窗"


def test_countdown_closes_the_window_by_itself(app, root):
    app.LOGIN_HINT_TICK_MS = 20            # 心跳调快, 别真等 20 秒
    win = _open(app, seconds=3)
    assert win is not None
    _pump(root, 1.0)
    assert app._login_hint is None, "倒计时到点要自己关掉"
    assert not _exists(root, win), "窗口得真的销毁, 不是只把引用清空"


def test_manual_close_stops_the_countdown(app, root):
    """收尾时 _close_login_hint 先关窗, 之后那一下残留的心跳不许打在死控件上。"""
    app.LOGIN_HINT_TICK_MS = 20
    errors = []
    root.report_callback_exception = lambda *a: errors.append(a)
    try:
        win = _open(app, seconds=3)
        LiushuiApp._close_login_hint(app)
        _pump(root, 0.5)
        assert not _exists(root, win)
        assert errors == [], "关窗后还有回调在跑: %r" % (errors,)
    finally:
        root.report_callback_exception = type(root).report_callback_exception


def test_user_closing_the_window_stops_the_countdown_too(app, root):
    """用户按右上角 X 关掉提示窗: 不许再有心跳往已销毁的控件上写字。

    钉的是这条契约, 不是某一处实现 —— 实测两条机制各自都兜得住(窗里那道存活判断, 以及
    这版 tkinter 随控件销毁把它名下的 after 一起撤掉), 所以单独拆掉哪一条这里都不红,
    两条全没了才会露出来。留它是因为"按 X 之后还在往死控件上写"是用户看得见的毛病。
    """
    app.LOGIN_HINT_TICK_MS = 20
    errors = []
    root.report_callback_exception = lambda *a: errors.append(a)
    try:
        win = _open(app, seconds=3)
        win.destroy()                          # 相当于用户点了 X
        _pump(root, 0.5)
        assert errors == [], "按 X 之后心跳还在写死控件: %r" % (errors,)
    finally:
        root.report_callback_exception = type(root).report_callback_exception
        LiushuiApp._close_login_hint(app)


def test_no_more_checkbox_persists(app, root):
    win = _open(app)
    try:
        boxes = [b for b in _widgets(win, "Checkbutton") if "不再弹出" in b.cget("text")]
        assert boxes, "窗里要有『不再弹出』的勾"
        boxes[0].invoke()
    finally:
        LiushuiApp._close_login_hint(app)
    assert app.settings["show_login_hint"] is False
    assert {"show_login_hint": False} in app.saved, "勾了就要当场落盘"
    assert app.logged and "不再弹" in app.logged[-1]
    # 同一轮里后面的商户也不该再弹
    assert LiushuiApp._show_login_hint(app, "微信支付", "另一家", "扫码") is None


def test_settings_row_handler_clamps_and_saves(app):
    """设置面板那一行: 秒数越界要夹回 3-600, 乱填退回默认, 开关原样存。"""
    app.login_hint_var = types.SimpleNamespace(get=lambda: False)
    app.hint_seconds = types.SimpleNamespace(get=lambda: " 9999 ")
    LiushuiApp._on_hint_setting(app)
    assert app.saved[-1] == {"show_login_hint": False, "login_hint_seconds": 600}
    assert app.settings["login_hint_seconds"] == 600

    app.hint_seconds = types.SimpleNamespace(get=lambda: "不是数字")
    LiushuiApp._on_hint_setting(app)
    assert app.saved[-1]["login_hint_seconds"] == 20, "读不出数字要退回默认值"


def test_hint_seconds_falls_back_when_settings_are_dirty(app):
    app.settings["login_hint_seconds"] = "半小时"
    assert LiushuiApp._hint_seconds(app) == 20
    app.settings["login_hint_seconds"] = 1
    assert LiushuiApp._hint_seconds(app) == 3, "太短的倒计时夹到下限, 别一闪而过"


def test_content_fits_the_fixed_window(app, root):
    """窗是写死尺寸的: 标题/说明/操作提示/倒计时/勾选都得装得下, 不许切掉。

    量的是"内容要的身高"跟"窗子开的身高"之比(所以改小窗或加内容都会红), 取最长的那份:
    600 秒的倒计时文案 + 会换行的长操作提示。少一行内容也要跟着调窗子, 否则倒计时那行
    或勾选框会被裁在窗外 —— 那就等于白加。
    """
    win = _open(app, seconds=600,
                guide="用企业微信扫码, 或在手机上点「确认登录」；这一步要等一会儿"
                      "才会跳转, 请勿关闭页面")
    try:
        win.update_idletasks()
        width, height = (int(v) for v in win.geometry().split("+")[0].split("x"))
        assert win.winfo_reqwidth() <= width, (win.winfo_reqwidth(), width)
        assert win.winfo_reqheight() <= height, (win.winfo_reqheight(), height)
    finally:
        LiushuiApp._close_login_hint(app)


def test_settings_panel_has_the_switch():
    """面板上真有这一行(带 Spinbox 秒数), 而不是只有个没人调用的方法。"""
    src = io.open(main_gui.__file__, encoding="utf-8").read()
    assert "登录提示窗" in src
    assert 'self.login_hint_var.trace_add("write", self._on_hint_setting)' in src
    assert "self.hint_seconds.bind" in src
