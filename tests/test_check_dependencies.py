"""启动前的依赖体检弹窗: 每种缺法各走各的路, 且体检不许把程序挡在门外。

check_dependencies() 是业务用户每天走的第一步, 所以这几条要钉住:
- 缺包 → 才会真的不启动(程序跑不动); 选"否"就退出 —— 这是老行为, 保持;
- 缺内核 / 版本不对 → **只问不拦**, 放行(这两种以前根本不检查, 放行不是新行为);
- 装不上 → 把 pip 的真实输出与两条命令、离线退路一起给人看, 不许只留一个异常对象;
- 弹窗本身起不来 → 放行, 让真正的错以它自己的形式出现, 而不是"体检没跑成所以别想用"。

不碰真网络: subprocess.run / os.execv / messagebox 全替换成记录用的假件。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.deps as deps            # noqa: E402
import core.main_gui as mg          # noqa: E402


def _d(verdict, **kw):
    args = dict(verdict=verdict, python="3.14.3", python_ok=True, have_playwright=True,
                version=deps.REQUIRED_PLAYWRIGHT_VERSION, expected=deps.REQUIRED_PLAYWRIGHT_VERSION,
                kernel_dir=r"C:\browsers\chromium-1234", searched=r"C:\browsers",
                missing_bits=[], notes=[])
    args.update(kw)
    return deps.Deps(**args)


class Harness:
    """假 Tk + 假 messagebox + 假 subprocess, 把用户的选择与安装结果演出来。"""

    def __init__(self, monkeypatch, verdict_answer=True, install_ok=True, install_tail="",
                 probe=None, tk_broken=False):
        self.asked = []
        self.infos = []
        self.errors = []
        self.ran = []
        self.restarted = []
        self.logs = []

        class _Root:
            def withdraw(self):
                pass

            def destroy(self):
                pass

        def _tk():
            if tk_broken:
                raise RuntimeError("Couldn't create a Tk window")
            return _Root()

        monkeypatch.setattr(mg, "tk", type("F", (), {"Tk": staticmethod(_tk)}))
        monkeypatch.setattr(mg, "log", lambda msg, level="info": self.logs.append(msg))
        monkeypatch.setattr(mg, "deps", deps)
        monkeypatch.setattr(mg.deps, "probe", lambda *a, **k: probe or _d(deps.OK))
        # 安装命令一律不许真跑: 记录 argv, 返回用户指定的成败
        def _run(argv, **kw):
            self.ran.append(list(argv))
            return type("R", (), {"returncode": 0 if install_ok else 1,
                                  "stdout": install_tail, "stderr": ""})()
        monkeypatch.setattr(mg.subprocess, "run", _run)
        monkeypatch.setattr(mg.os, "execv", lambda exe, argv: self.restarted.append((exe, argv)))
        mb = type("MB", (), {})()
        mb.askyesno = lambda title, msg: self._ask(title, msg, verdict_answer)
        mb.showinfo = lambda title, msg: self.infos.append((title, msg))
        mb.showerror = lambda title, msg: self.errors.append((title, msg))
        monkeypatch.setattr(mg, "messagebox", mb)

    def _ask(self, title, msg, answer):
        self.asked.append((title, msg))
        return answer() if callable(answer) else answer

    def text(self):
        return "\n".join([m for _t, m in self.asked] + [m for _t, m in self.errors]
                         + [m for _t, m in self.infos] + self.logs)


def test_all_good_starts_without_asking_anything(monkeypatch):
    h = Harness(monkeypatch)
    assert mg.check_dependencies() is True
    assert h.asked == [] and h.ran == []


def test_broken_probe_is_not_a_reason_to_block(monkeypatch):
    """体检自己出错 = 放行(它只是附加检查, 不许把账单挡掉)。"""
    h = Harness(monkeypatch, probe=_d(deps.UNKNOWN, notes=["体检没跑成, 已跳过: OSError()"]))
    assert mg.check_dependencies() is True
    assert h.asked == []
    assert "已跳过" in h.text()


def test_missing_package_and_user_declines_exits(monkeypatch):
    """老行为: 连包都没有, 用户又说不用装 → 不启动。"""
    h = Harness(monkeypatch, verdict_answer=False,
                probe=_d(deps.NO_PACKAGE, have_playwright=False, version="",
                         kernel_dir="", searched=r"C:\browsers"))
    assert mg.check_dependencies() is False
    assert h.asked and h.asked[0][0] == "缺少依赖"
    assert h.ran == []


def test_missing_package_installs_pinned_version_then_restarts(monkeypatch):
    h = Harness(monkeypatch, verdict_answer=True,
                probe=_d(deps.NO_PACKAGE, have_playwright=False, version="", kernel_dir=""))
    assert mg.check_dependencies() is True
    assert len(h.ran) == 2, "包和内核都缺 → 两条命令都得跑"
    assert h.ran[0][-1] == deps.PIP_SPEC, "装的时候必须钉版本, 不能装个新的来路不明版"
    assert h.ran[1][-2:] == ["install", "chromium"]
    assert h.restarted, "装完要重启才轮到新装的包被 import 到"
    assert h.infos and "重新启动" in h.infos[0][1]


def test_install_failure_shows_pip_output_and_the_way_out(monkeypatch):
    h = Harness(monkeypatch, verdict_answer=True, install_ok=False,
                install_tail="no matching distribution found for playwright (内网索引没有它)",
                probe=_d(deps.NO_PACKAGE, have_playwright=False, version="", kernel_dir=""))
    assert mg.check_dependencies() is False       # 包都没有, 确实跑不动
    t = h.text()
    assert "no matching distribution" in t, "pip 的真实原因必须出现在弹窗里(以前只有一个异常)"
    assert "pip install" in t and "playwright install chromium" in t, "要给人能抄的命令"
    assert "PLAYWRIGHT_BROWSERS_PATH" in t and "全量版" in t, "离线退路"


def test_missing_kernel_declined_still_lets_you_in(monkeypatch):
    """没内核不拦人: 设置、日志、看板都还能看, 只是导出会失败 —— 那句要写进日志。"""
    h = Harness(monkeypatch, verdict_answer=False,
                probe=_d(deps.NO_KERNEL, kernel_dir="", searched=r"C:\Users\x\AppData\Local\ms-playwright"))
    assert mg.check_dependencies() is True
    assert h.asked[0][0] == "缺少浏览器内核"
    assert "导出会失败" in h.text()
    assert h.ran == []


def test_missing_kernel_only_downloads_the_kernel(monkeypatch):
    h = Harness(monkeypatch, verdict_answer=True,
                probe=_d(deps.NO_KERNEL, kernel_dir="", searched=r"C:\x"))
    assert mg.check_dependencies() is True
    assert len(h.ran) == 1 and h.ran[0][-2:] == ["install", "chromium"], "包是好的, 别让人重装包"


def test_version_mismatch_declined_continues(monkeypatch):
    h = Harness(monkeypatch, verdict_answer=False,
                probe=_d(deps.BAD_VERSION, version="1.55.0"))
    assert mg.check_dependencies() is True
    assert h.asked[0][0] == "依赖版本不一致"
    assert h.ran == []
    assert "1.55.0" in h.text() and deps.REQUIRED_PLAYWRIGHT_VERSION in h.text()


def test_version_mismatch_accepted_switches_it(monkeypatch):
    h = Harness(monkeypatch, verdict_answer=True, probe=_d(deps.BAD_VERSION, version="1.55.0"))
    assert mg.check_dependencies() is True
    assert len(h.ran) == 1 and h.ran[0][-1] == deps.PIP_SPEC
    assert h.restarted


def test_dialog_that_cannot_open_does_not_stop_the_program(monkeypatch):
    """Tk 起不来(远程会话/没有窗口站)时不许变成"程序打不开" —— 放行, 让真错自己露面。"""
    h = Harness(monkeypatch, tk_broken=True,
                probe=_d(deps.NO_KERNEL, kernel_dir="", searched="C:\\x"))
    assert mg.check_dependencies() is True
    assert h.ran == []
    assert "弹窗" in h.text()


def test_install_helper_reports_returncode_and_tail(monkeypatch):
    def _run(argv, **kw):
        return type("R", (), {"returncode": 2, "stdout": "a\nb\nc", "stderr": "磁盘满了"})()
    monkeypatch.setattr(mg.subprocess, "run", _run)
    monkeypatch.setattr(mg, "log", lambda msg, level="info": None)
    ok, tail = mg._install_dependencies([("装上 playwright", ["py", "-m", "pip"])])
    assert ok is False and "退出码 2" in tail and "磁盘满了" in tail
