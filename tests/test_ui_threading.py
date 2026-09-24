"""界面更新的线程模型: _ui 投递 / _pump_ui 消费 / running 互斥。

Tk 控件只能在主线程改, 而导出、保活、定时任务各跑在自己的线程上, 因此界面操作
统一投递给主线程泵。这里用未绑定的真实方法 + 假 self 验证这套机制本身。"""
import queue
import threading

from core.main_gui import LiushuiApp


class _FakeRoot:
    def __init__(self, raise_after=False):
        self.after_calls = []
        self.raise_after = raise_after

    def after(self, ms, fn):
        if self.raise_after:
            raise RuntimeError("main window has been destroyed")
        self.after_calls.append((ms, fn))


class _UiApp:
    _ui = LiushuiApp._ui
    _pump_ui = LiushuiApp._pump_ui

    def __init__(self, root_destroyed=False):
        self._ui_thread = threading.get_ident()
        self._uiq = queue.Queue()
        self.root = _FakeRoot(raise_after=root_destroyed)


def test_ui_runs_inline_on_main_thread():
    app = _UiApp()
    hits = []
    app._ui(lambda: hits.append(1))
    assert hits == [1]                      # 主线程就地执行, 点击不增加延迟
    assert app._uiq.empty()


def test_ui_from_other_thread_only_queues():
    app = _UiApp()
    hits = []
    t = threading.Thread(target=lambda: app._ui(lambda: hits.append(1)))
    t.start()
    t.join()
    assert hits == []                       # 回主线程前绝不能碰控件
    assert app._uiq.qsize() == 1
    app._pump_ui()
    assert hits == [1]


def test_pump_survives_failing_callback():
    """一条界面回调抛错不能吞掉后面的, 也不能停掉泵。"""
    app = _UiApp()
    done = []

    def boom():
        raise RuntimeError("控件已销毁")

    app._uiq.put(boom)
    app._uiq.put(lambda: done.append("second"))
    app._pump_ui()
    assert done == ["second"]
    assert len(app.root.after_calls) == 1   # 泵已重新排程


def test_pump_stops_quietly_when_window_destroyed():
    app = _UiApp(root_destroyed=True)
    app._pump_ui()                          # 不应抛出


class _RunApp:
    _run_async = LiushuiApp._run_async

    def __init__(self):
        self.running = False
        self._task_lock = threading.Lock()
        self.platform_vars = {}
        self.started = []
        self.warned = 0
        self.thread_ran = threading.Event()

    def _ui(self, fn):
        self.warned += 1                    # busy 分支只投递警告, 不真的弹窗

    def set_platform_status(self, key, status):
        pass

    def _thread_wrapper(self, target):
        self.started.append(target)
        self.thread_ran.set()


def test_concurrent_run_async_admits_exactly_one_task():
    """10 条线程同时抢: 只允许 1 条真正开始, 其余必须看到"已有任务在执行"。"""
    app = _RunApp()
    ready = threading.Barrier(10)

    def claim():
        ready.wait()                        # 让 10 条线程尽量同时进入 _run_async
        app._run_async(lambda: None)

    threads = [threading.Thread(target=claim) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert app.thread_ran.wait(timeout=2) is True
    assert len(app.started) == 1
    assert app.warned == 9
