"""等用户手工确认(视频号扫码这类)不许把"没人应答"当成"用户已确认"。

两层各钉一件事:
- `BrowserManager.wait_user`: 回调必须回话, True 只来自"用户点了确定"; 放弃 / 超时 /
  窗没建起来 / 回调自身异常一律 False。旧实现是回调一返回就无条件记"用户已确认,继续"
  → 手机没扫码也会点"下载数据", 拿到空文件/旧文件却记成 success。
- `LiushuiApp._ask_user_confirmed`: 这张确认窗是**自建的非模态 Toplevel**。以前用
  `messagebox` 模态框, 实测两个坑: 模态框压住主窗口让「中止」点不动; 超时那一路 worker
  先返回、`_ask` 还卡在框里 → `finally` 根本没跑 → 主窗口 topmost 永不收回(量到
  attributes 序列只有 [True])、那张没人应答的框一直挂在屏上, 下一家再叠一张。
"""
import queue
import threading
import time
import tkinter as tk

import pytest

from core.browser import BrowserManager
from core import main_gui


# ===== BrowserManager.wait_user 的四种回话 =====

@pytest.fixture()
def mgr():
    b = BrowserManager(headless=True)          # 不 start(), 不碰浏览器
    logs = []
    b._log = lambda msg, level="info": logs.append(f"{level}|{msg}")
    b.snapshot = lambda *a, **k: None
    return b, logs


def test_wait_user_true_only_when_the_user_says_yes(mgr):
    b, logs = mgr
    b.set_user_wait_callback(lambda prompt: True)
    assert b.wait_user("请扫码") is True
    assert any("用户已确认" in l for l in logs), logs


@pytest.mark.parametrize("verdict", [False, None, 0, ""],
                         ids=["取消", "回调不回话(旧写法)", "0", "空串"])
def test_wait_user_treats_silence_as_not_confirmed(mgr, verdict):
    """回调返回 None/False 都不算确认 —— 老代码里 None 也是"继续"。"""
    b, logs = mgr
    b.set_user_wait_callback(lambda prompt: verdict)
    assert b.wait_user("请扫码") is False
    assert not any("用户已确认" in l for l in logs), logs
    assert any("转人工" in l for l in logs), logs


def test_wait_user_callback_error_is_not_confirmed(mgr):
    b, logs = mgr

    def boom(prompt):
        raise RuntimeError("界面挂了")
    b.set_user_wait_callback(boom)
    assert b.wait_user("请扫码") is False
    assert any("按未完成处理" in l for l in logs), logs


def test_wait_user_without_callback_still_pauses_and_proceeds(mgr):
    """没界面可问的场合(录制/调试)保持旧兜底: 停留一会儿后继续, 别把调试流程卡死。"""
    b, logs = mgr
    assert b.wait_user("请扫码", timeout=0) is True


# ===== GUI 那一侧: 真 Tk + 真控件; worker 线程等, 测试线程扮演主线程抽干 _ui =====

@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("这台机器起不了 Tk")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


def _topmost_off(value):
    """Tk 对 -topmost 可能回 0 / "0" / False, 统一按"没置顶"判。"""
    return str(value).strip().lower() in ("", "0", "false", "none")


class _PumpApp:
    """假 self: `_ui` 只投递不执行(与生产一致), 由测试线程抽干队列扮演主线程。"""

    _close_confirm_window = main_gui.LiushuiApp._close_confirm_window
    _answer_confirm = main_gui.LiushuiApp._answer_confirm

    USER_WAIT_TIMEOUT_S = 3
    _CONFIRM_POLL_S = 0.1

    def __init__(self, root, aborted=False):
        self.root = root
        self.aborted = aborted
        self.lines = []
        self.q = queue.Queue()
        self._confirm_win = None
        self._confirm_buttons = []

    def _ui(self, fn):
        self.q.put(fn)

    def _append_log(self, line):
        self.lines.append(line)

    def _aborted(self):
        return self.aborted

    def pump_once(self):
        while True:
            try:
                fn = self.q.get_nowait()
            except queue.Empty:
                break
            fn()
        try:
            self.root.update()
        except tk.TclError:
            pass


def _alive(app, win):
    """这张窗还在不在主窗口的子窗清单里。

    不用 `winfo_exists()`: 销毁后 Tk 会把这个路径名**复给后建的窗**, 于是它有时抛
    TclError、有时返回 1, 判起来像随机数(实测两条用例就一条红)。
    """
    return str(win) in [str(c) for c in app.root.winfo_children()]


def _ask_in_thread(app):
    out = []
    w = threading.Thread(target=lambda: out.append(
        main_gui.LiushuiApp._ask_user_confirmed(app, "请在手机上扫码", "视频号", "旗舰店A")))
    w.start()
    for _ in range(60):                     # 这张窗只能由"主线程"建出来
        app.pump_once()
        if app._confirm_win is not None:
            break
        time.sleep(0.02)
    return w, out


def _settle(app, worker, seconds=6.0):
    """继续扮演主线程, 直到 worker 走完(超时/中止后的那次关窗也是投递过去的)。"""
    end = time.time() + seconds
    while time.time() < end:
        app.pump_once()
        if not worker.is_alive():
            break
        time.sleep(0.02)
    worker.join(2)
    app.pump_once()


def test_confirm_window_is_not_modal_and_offers_two_choices(root):
    app = _PumpApp(root)
    w, out = _ask_in_thread(app)
    win = app._confirm_win
    assert win is not None and _alive(app, win), "确认窗没建出来"
    assert [b.cget("text") for b in app._confirm_buttons] == \
        ["我已完成, 继续下载", "这一家先放弃"], "两个答复都得不言自明"
    assert not win.tk.call("grab", "current"), "抢了 grab 就等于模态, 「中止」会点不动"
    app._confirm_buttons[0].invoke()
    _settle(app, w)
    assert out == [True], out


def test_a_stale_window_being_cleared_does_not_answer_this_one(root):
    """建窗前的那次"清场"不许顺手把本次等待 set 掉。

    第一版实现就是这么写的(`_close_confirm_window(evt)`), 结果窗口刚建出来 worker 就
    带着 False 走了 —— 生产里等于"视频号永远 manual", 而屏幕上明明有个窗在等人点。
    """
    app = _PumpApp(root)
    w, out = _ask_in_thread(app)
    assert w.is_alive(), "刚建出确认窗, worker 还在等答复才对"
    app._confirm_buttons[0].invoke()
    _settle(app, w)
    assert out == [True], out


def test_cancel_button_answer_and_window_destroyed(root):
    app = _PumpApp(root)
    w, out = _ask_in_thread(app)
    win = app._confirm_win
    app._confirm_buttons[1].invoke()
    _settle(app, w)
    assert out == [False], out
    assert app._confirm_win is None
    assert not _alive(app, win), "真关掉了才行, 不是藏起来"


def test_timeout_closes_the_window_and_never_touches_the_main_window(root):
    """超时那一路以前把主窗口留在 topmost、把框留在屏上; 现在两条都得收干净。"""
    app = _PumpApp(root)
    app.USER_WAIT_TIMEOUT_S = 1
    assert _topmost_off(app.root.attributes("-topmost"))
    w, out = _ask_in_thread(app)
    win = app._confirm_win
    _settle(app, w)
    assert out == [False], out
    assert app._confirm_win is None and not _alive(app, win), "超时后那张窗不该还挂着"
    assert any("没等到确认" in l for l in app.lines), app.lines
    assert _topmost_off(app.root.attributes("-topmost")), \
        "主窗口的 topmost 从头到尾都不该被碰(置顶只设在这张窗身上)"


def test_abort_closes_the_window(root):
    app = _PumpApp(root)
    w, out = _ask_in_thread(app)
    win = app._confirm_win
    app.aborted = True                        # 用户按了「中止」
    _settle(app, w)
    assert out == [False], out
    assert not _alive(app, win), "中止后也必须把这张窗收掉"
    assert any("收到中止请求" in l for l in app.lines), app.lines


def test_window_that_never_opens_is_not_a_confirmation():
    """连窗都建不起来(Tk 坏了)≠ 用户确认了。"""
    class _Broken:
        _close_confirm_window = main_gui.LiushuiApp._close_confirm_window
        _answer_confirm = main_gui.LiushuiApp._answer_confirm

        USER_WAIT_TIMEOUT_S = 1
        _CONFIRM_POLL_S = 0.1

        def __init__(self):
            self.lines = []
            self.root = object()              # tk.Toplevel(这个) 会抛

        def _ui(self, fn):
            fn()

        def _append_log(self, line):
            self.lines.append(line)

        def _aborted(self):
            return False

    app = _Broken()
    assert main_gui.LiushuiApp._ask_user_confirmed(app, "请扫码", "视频号", "甲") is False
    assert any("确认窗没弹出来" in l for l in app.lines), app.lines


# ===== 平台脚本: 没确认就不点下载 =====

class _FakeBrowser:
    """只记调用名; wait_user 的返回值由用例给。"""

    def __init__(self, confirmed):
        self.calls = []
        self.confirmed = confirmed

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append(name)
            return self.confirmed if name == "wait_user" else True
        return rec


def _run_shipinhao(monkeypatch, confirmed):
    from platforms.shipinhao.export import ShipinhaoExporter
    # 日期那一档不是本用例要验的(它有自己的回归), 假定设成功, 好看"待确认"这一步
    monkeypatch.setattr(ShipinhaoExporter, "_set_time", lambda self, b, s, e: True)
    b = _FakeBrowser(confirmed)
    return ShipinhaoExporter().export(b, "2026-09-01", "2026-09-02"), b


def test_shipinhao_stops_when_the_user_did_not_confirm(monkeypatch):
    out, b = _run_shipinhao(monkeypatch, confirmed=False)
    assert out == "manual"
    assert "begin_wait_download" not in b.calls, "没拿到确认就不该开始等下载"
    assert "wait_user" in b.calls and "snapshot" in b.calls, "要留一张现场图"


def test_shipinhao_still_downloads_after_a_real_confirmation(monkeypatch):
    out, b = _run_shipinhao(monkeypatch, confirmed=True)
    assert out == "success"
    assert "begin_wait_download" in b.calls and "wait_download" in b.calls
