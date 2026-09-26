"""等用户手工确认(视频号扫码这类)不许把"没人应答"当成"用户已确认"。

回归的形状: 旧 `_user_wait_cb` 里 `evt.wait(timeout=3600)` 把等待结果整个丢掉,
回调不回任何话, 而 `BrowserManager.wait_user` 一看到回调返回就写"用户已确认,继续"
并 return True → 手机没扫码也会点"下载数据", 拿到空文件/旧文件却记成 success。
再加上提示用的是 showinfo 且没有 topmost(常被最大化的 Chromium 挡住)、模态框压着
主窗口连「中止」都点不动 → 用户离席就是白挂一小时。

现在: 回调必须回话(True 只来自用户亲手点"确定"), 取消/弹窗起不来/超时/回调异常
一律 False, 平台脚本拿到 False 就停手转人工。
"""
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


# ===== GUI 那一侧: 只有用户点"确定"才回 True =====

class _FakeApp:
    """按第 12 章第 11/12 条的形状造个假 self: 弹窗投递给主线程, 本线程等 Event。"""

    USER_WAIT_TIMEOUT_S = 2

    def __init__(self, ui_runs=True, aborted=False):
        self.lines = []
        self.topmost = []
        self.ui_runs = ui_runs
        self.aborted = aborted
        self.root = self

    def attributes(self, name, value=None):
        self.topmost.append((name, value))

    def _ui(self, fn):
        if self.ui_runs:
            fn()

    def _append_log(self, line):
        self.lines.append(line)

    def _aborted(self):
        return self.aborted


class _Box:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.calls = []

    def askokcancel(self, title, msg, **kw):
        self.calls.append((title, msg))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture()
def box(monkeypatch):
    def _make(**kw):
        b = _Box(**kw)
        monkeypatch.setattr(main_gui, "messagebox", b)
        return b
    return _make


def test_gui_returns_true_only_on_ok(box):
    app = _FakeApp()
    bx = box(result=True)
    assert main_gui.LiushuiApp._ask_user_confirmed(app, "请扫码", "视频号", "旗舰店A") is True
    assert bx.calls and "视频号" in bx.calls[0][1] and "旗舰店" in bx.calls[0][1]
    assert app.topmost and app.topmost[0] == ("-topmost", True), "提示必须盖在浏览器上面"
    assert app.topmost[-1] == ("-topmost", False), "点完要把 topmost 收回去"


def test_gui_cancel_returns_false(box):
    app = _FakeApp()
    box(result=False)
    assert main_gui.LiushuiApp._ask_user_confirmed(app, "请扫码", "视频号", "甲") is False


def test_gui_dialog_failure_returns_false(box):
    """弹窗起不来 ≠ 用户确认了(旧代码 `except Exception: pass` 之后照样往下点)。"""
    app = _FakeApp()
    box(error=RuntimeError("invalid command name"))
    assert main_gui.LiushuiApp._ask_user_confirmed(app, "请扫码", "视频号", "甲") is False
    assert any("弹窗没弹出来" in l for l in app.lines), app.lines


def test_gui_abort_during_wait_returns_false(box):
    """等待期间能被打断: 「中止」按下去后这一家按未完成收尾, 不再等满超时。"""
    app = _FakeApp(ui_runs=False, aborted=True)      # 弹窗没投出去, 但用户按了中止
    box(result=True)
    assert main_gui.LiushuiApp._ask_user_confirmed(app, "请扫码", "视频号", "甲") is False
    assert any("收到中止请求" in l for l in app.lines), app.lines


def test_gui_timeout_is_not_confirmation(box):
    """没人应答到点就放手 —— 以前是 evt.wait(3600) 之后照样"已确认"。"""
    app = _FakeApp(ui_runs=False)
    box(result=True)
    assert main_gui.LiushuiApp._ask_user_confirmed(app, "请扫码", "视频号", "甲") is False
    assert any("没等到确认" in l for l in app.lines), app.lines


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
