"""依赖体检: 三档结论、命令建议、以及"体检自己出错也不许拦人"。

核心版(只带代码, 用本机 Python)第一次在别人机器上跑起来时, 缺的通常是 playwright 包、
版本对不上、或者**装了包没下内核**。最后这种以前查不出来, 症状推迟到点导出、Chromium
起不来时才炸。所以这里的测试重点是"缺哪一档就说哪一档", 不是"能不能装成功"。

不依赖本机到底装了什么: 现场全靠注入(env / program_dir / version / has_module)。
"""
import io
import os
import re

import pytest

import core.config as cfg
import core.deps as deps

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _kernel(root, rev=1148, marker=True, exe_dir="chrome-win64"):
    """造一个像样的内核目录 —— 按开发机上真实看到的布局: chromium-<版本>/chrome-win64/chrome.exe
    外加 Playwright 自己写的 INSTALLATION_COMPLETE 标记。"""
    base = os.path.join(str(root), "chromium-%d" % rev)
    d = os.path.join(base, exe_dir)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "chrome.exe"), "wb").close()
    if marker:
        open(os.path.join(base, "INSTALLATION_COMPLETE"), "wb").close()
    return base


def _env(tmp_path, **extra):
    """一个"干净机器"的环境: LOCALAPPDATA 指到 tmp, 没有外部内核目录。"""
    e = {"LOCALAPPDATA": str(tmp_path / "AppDataLocal")}
    e.update(extra)
    return e


# ===== 版本只有一个真源 =====

def test_requirements_and_code_pin_the_same_playwright():
    """装的时候说一个版本、requirements 写另一个版本 = 核心版用户装完还得再来一遍。"""
    txt = io.open(os.path.join(REPO, "requirements.txt"), encoding="utf-8").read()
    m = re.search(r"playwright==([0-9][\w.]*)", txt)
    assert m, "requirements.txt 里必须钉住 playwright 版本"
    assert m.group(1) == deps.REQUIRED_PLAYWRIGHT_VERSION
    assert deps.PIP_SPEC == "playwright==" + deps.REQUIRED_PLAYWRIGHT_VERSION
    # CI 的 PLAYWRIGHT_VERSION 由工作流里那条断言当场核, 不在测试能读到的范围内
    wf = io.open(os.path.join(REPO, ".github", "workflows", "build-portable.yml"),
                 encoding="utf-8").read()
    assert 'PLAYWRIGHT_VERSION: "%s"' % deps.REQUIRED_PLAYWRIGHT_VERSION in wf, \
        "工作流的版本和代码里的对不上了"


# ===== 三档结论 =====

def test_missing_package_is_the_first_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "has_module", lambda name: False)
    d = deps.probe(env=_env(tmp_path), program_dir=str(tmp_path / "pkg"), version=None, fallback=None)
    assert d.verdict == deps.NO_PACKAGE
    assert "playwright 包" in " ".join(d.missing_bits)
    assert d.kernel_dir == ""


def test_version_mismatch_is_reported_but_not_as_a_missing_package(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "has_module", lambda name: True)
    _kernel(tmp_path / "AppDataLocal" / "ms-playwright")
    d = deps.probe(env=_env(tmp_path), program_dir=str(tmp_path / "pkg"), version="1.55.0", fallback=None)
    assert d.verdict == deps.BAD_VERSION
    assert d.version == "1.55.0" and d.expected == deps.REQUIRED_PLAYWRIGHT_VERSION
    txt = " ".join(deps.describe(d))
    assert "1.55.0" in txt and deps.REQUIRED_PLAYWRIGHT_VERSION in txt
    assert "继续" in txt, "这一档只问不拦, 文案要让人知道可以选继续"


def test_kernel_missing_is_its_own_verdict(tmp_path, monkeypatch):
    """装了包、没下内核 —— 以前正是这一档漏网, 炸在导出时。"""
    monkeypatch.setattr(deps, "has_module", lambda name: True)
    d = deps.probe(env=_env(tmp_path), program_dir=str(tmp_path / "pkg"),
                   version=deps.REQUIRED_PLAYWRIGHT_VERSION, fallback=None)
    assert d.verdict == deps.NO_KERNEL
    assert "内核" in " ".join(deps.describe(d))


def test_all_present_is_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "has_module", lambda name: True)
    pkg = tmp_path / "pkg"
    _kernel(pkg / "runtime" / "ms-playwright")          # 绿色包自带的内核(程序目录)
    d = deps.probe(env=_env(tmp_path), program_dir=str(pkg),
                   version=deps.REQUIRED_PLAYWRIGHT_VERSION, fallback=None)
    assert d.verdict == deps.OK, d
    assert d.kernel_dir.endswith("chromium-1148")
    assert not d.missing_bits


def test_headless_shell_alone_does_not_count(tmp_path):
    """只有 chromium_headless_shell-* 时我们照样起不来(用持久化上下文), 不能算有内核。"""
    shell = os.path.join(str(tmp_path), "chromium_headless_shell-1148", "chrome-win")
    os.makedirs(shell)
    open(os.path.join(shell, "chrome-headless-shell.exe"), "wb").close()
    assert deps.find_chromium_dir(str(tmp_path)) == ""
    _kernel(tmp_path)
    assert deps.find_chromium_dir(str(tmp_path)).endswith("chromium-1148")


def test_kernel_layout_is_not_hardcoded(tmp_path):
    """子目录名随 playwright 版本变过(chrome-win -> chrome-win64), 写死就会集体误报"缺内核"。"""
    _kernel(tmp_path / "老布局", exe_dir="chrome-win")
    assert deps.find_chromium_dir(str(tmp_path / "老布局")).endswith("chromium-1148")
    # 只有标记没有 exe 也算数: 那正是 playwright 认定"装完了"的凭据
    empty = str(tmp_path / "只有标记")
    os.makedirs(os.path.join(empty, "chromium-7", "chrome-win64"))
    open(os.path.join(empty, "chromium-7", "INSTALLATION_COMPLETE"), "wb").close()
    assert deps.find_chromium_dir(empty).endswith("chromium-7")


def test_half_downloaded_kernel_does_not_count(tmp_path):
    """`playwright install` 下到一半断网: 没有标记也没有 exe, 必须报"缺内核"而不是"有"。"""
    half = str(tmp_path / "chromium-1148")
    os.makedirs(os.path.join(half, "chrome-win64"))
    open(os.path.join(half, "chrome.dll.tmp"), "wb").close()
    assert deps.find_chromium_dir(str(tmp_path)) == ""


def test_resolved_root_is_not_falled_back(tmp_path, monkeypatch):
    """env 指到一个存在但没内核的目录: 运行时 Playwright 也只认它, 体检不能回退去别处报"有"。"""
    monkeypatch.setattr(deps, "has_module", lambda name: True)
    custom = tmp_path / "公司统一下的内核目录"
    custom.mkdir()
    _kernel(tmp_path / "AppDataLocal" / "ms-playwright")        # 默认位置里其实有
    d = deps.probe(env=_env(tmp_path, PLAYWRIGHT_BROWSERS_PATH=str(custom)),
                   program_dir=str(tmp_path / "pkg"),
                   version=deps.REQUIRED_PLAYWRIGHT_VERSION, fallback=None)
    assert d.verdict == deps.NO_KERNEL
    assert d.searched == str(custom)


# ===== 红线: 体检自己出错不拦人 =====

def test_probe_failure_becomes_unknown_and_keeps_going(monkeypatch):
    monkeypatch.setattr(cfg, "resolve_browsers_path", lambda **k: (_ for _ in ()).throw(
        OSError("盘没插好")))
    d = deps.probe(env={}, program_dir="x", version="1.62.0", fallback=None)
    assert d.verdict == deps.UNKNOWN
    assert d.python_ok and d.have_playwright, "不知道就当能跑, 不拦"
    assert d.missing_bits == []
    assert "已跳过" in " ".join(deps.describe(d)), "吞掉的东西要写明吞了什么"


def test_old_python_only_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "has_module", lambda name: True)
    _kernel(tmp_path / "AppDataLocal" / "ms-playwright")
    d = deps.probe(env=_env(tmp_path), program_dir=str(tmp_path / "pkg"),
                   version=deps.REQUIRED_PLAYWRIGHT_VERSION, python="3.8.10")
    assert d.verdict == deps.OK and not d.python_ok
    assert "3.8.10" in " ".join(deps.describe(d))
    assert deps.probe(env=_env(tmp_path), program_dir=str(tmp_path),
                      python="读不懂的版本").python_ok is True


# ===== 给人抄的东西 =====

def test_commands_match_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "has_module", lambda name: False)
    d = deps.probe(env=_env(tmp_path), program_dir=str(tmp_path), version=None, fallback=None)
    cmds = " \n".join(c for _t, c in deps.fix_commands(d, python_exe=r"C:\py\python.exe"))
    assert "pip install playwright==%s" % deps.REQUIRED_PLAYWRIGHT_VERSION in cmds, "要钉版本"
    assert "playwright install chromium" in cmds, "包和内核都缺时两条都要给"
    assert '"C:\\py\\python.exe"' in cmds, "用哪台机器的解释器就写哪台的路径"

    monkeypatch.setattr(deps, "has_module", lambda name: True)
    d2 = deps.probe(env=_env(tmp_path), program_dir=str(tmp_path),
                    version=deps.REQUIRED_PLAYWRIGHT_VERSION, fallback=None)
    only = " \n".join(c for _t, c in deps.fix_commands(d2, python_exe="py"))
    assert "pip install" not in only and "install chromium" in only


def test_offline_hint_names_the_copy_the_kernel_escape_hatch():
    """内网机器 pip 装不上是常态, 退路要写在体检里而不是等人问。"""
    assert "PLAYWRIGHT_BROWSERS_PATH" in deps.OFFLINE_HINT
    assert "全量版" in deps.OFFLINE_HINT


def test_describe_and_describe_lines_are_never_empty():
    for v in (deps.OK, deps.NO_PACKAGE, deps.BAD_VERSION, deps.NO_KERNEL, deps.UNKNOWN):
        d = deps.Deps(v, "3.14.3", True, True, "1.62.0", "1.62.0", "", "", [], [])
        assert deps.describe(d), v
