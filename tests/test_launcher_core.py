"""核心版启动器 run-core.vbs 找 Python 的档序。

核心版 = 包里只有程序, 指望本机有 Python。所以这个 .vbs 挑中谁, 就等于这台机器的
导出工具跑在谁身上: 挑不到就得到一个"双击没反应"的窗口, 挑错成一个没有 tkinter 的
嵌入式 Python 则是启动即崩。测试手法与 tests/test_launcher_vbs.py 相同 —— 用 cscript
真跑脚本的 `--print-python` 诊断分支(只打印、不起界面), 环境里三个候选位置由临时目录
摆出来, 所以档序是**测出来的**, 不是读注释读出来的。
"""
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VBS = os.path.join(REPO, "packaging", "run-core.vbs")
BAT = os.path.join(REPO, "packaging", "run-core.bat")

pytestmark = pytest.mark.skipif(not sys.platform.startswith("win"),
                                reason="cscript 只在 Windows 上")


def _cscript():
    path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cscript.exe")
    if not os.path.exists(path):
        pytest.skip("找不到 cscript.exe")
    return path


def _touch(path, size=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        if size:
            f.write(b"\0" * size)          # 0 字节 = 商店占位桩, 与真文件区分开


def _run(tmp_path, windir=None, path_dirs=(), local_app=None, script=VBS):
    """按给定的三档环境跑一次诊断分支, 返回 {pythonw:…, python:…}。"""
    env = dict(os.environ)
    fake_windir = str(windir if windir is not None else tmp_path / "空windir")
    os.makedirs(fake_windir, exist_ok=True)
    env["WINDIR"] = fake_windir
    env["SystemRoot"] = fake_windir
    env["PATH"] = ";".join(str(p) for p in path_dirs)
    env["LocalAppData"] = str(local_app if local_app is not None else tmp_path / "空localapp")
    os.makedirs(env["LocalAppData"], exist_ok=True)
    r = subprocess.run([_cscript(), "//nologo", script, "--print-python"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, "cscript 解析/运行失败: %s%s" % (r.stdout, r.stderr)
    got = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    return got.get("pythonw", "").strip(), got.get("python", "").strip()


def test_the_script_is_pure_ascii():
    """和启动工具.vbs 同一条铁律: .vbs 用 ANSI 代码页读, 写中文注释都会把脚本弄坏。"""
    src = open(VBS, encoding="ascii").read()
    assert "Option Explicit" in src
    # 核心版不许要求包内有解释器(那是全量版的事)
    assert "python\\pythonw.exe" not in src, src


def test_py_launcher_wins_over_path(tmp_path):
    """写死的机器级安装之前先给 py 启动器: 它自己会解析注册过的 Python。"""
    windir = tmp_path / "windir"
    _touch(str(windir / "pyw.exe"))
    _touch(str(windir / "py.exe"))
    on_path = tmp_path / "bin"
    _touch(str(on_path / "pythonw.exe"))
    _touch(str(on_path / "python.exe"))
    pyw, py = _run(tmp_path, windir=windir, path_dirs=[on_path])
    assert pyw == str(windir / "pyw.exe")
    assert py == str(windir / "py.exe"), "pythonw 找 pyw.exe, python 找 py.exe, 不能混"


def test_path_comes_before_the_per_user_folder(tmp_path):
    """没有 py 启动器时用 PATH 上的(venv/conda/Store 都在这一档), 它排在用户目录前。"""
    on_path = tmp_path / "bin"
    _touch(str(on_path / "pythonw.exe"))
    _touch(str(on_path / "python.exe"))
    user = tmp_path / "LocalApp" / "Programs" / "Python" / "Python314"
    _touch(str(user / "pythonw.exe"))
    _touch(str(user / "python.exe"))
    pyw, py = _run(tmp_path, path_dirs=[on_path], local_app=tmp_path / "LocalApp")
    assert pyw == str(on_path / "pythonw.exe")
    assert py == str(on_path / "python.exe")


def test_store_stub_is_skipped(tmp_path):
    """WindowsApps 里那个 0 字节的 pythonw.exe 是"还没装, 点开会跳商店"的占位桩。"""
    stub = tmp_path / "WindowsApps"
    _touch(str(stub / "pythonw.exe"), size=0)
    _touch(str(stub / "python.exe"), size=0)
    real = tmp_path / "bin"
    _touch(str(real / "pythonw.exe"))
    _touch(str(real / "python.exe"))
    pyw, py = _run(tmp_path, path_dirs=[stub, real])
    assert pyw == str(real / "pythonw.exe"), "占位桩排前面时不许选它"
    assert py == str(real / "python.exe")


def test_per_user_install_is_the_last_tier(tmp_path):
    user = tmp_path / "LocalApp" / "Programs" / "Python" / "Python313"
    _touch(str(user / "pythonw.exe"))
    _touch(str(user / "python.exe"))
    pyw, py = _run(tmp_path, path_dirs=[tmp_path / "空bin"], local_app=tmp_path / "LocalApp")
    assert pyw == str(user / "pythonw.exe")


def test_nothing_installed_reports_empty_without_crashing(tmp_path):
    """全落空时打印空值就行 —— 报错与"要不要退出"是启动器另一半的事, 诊断分支不许掺和。"""
    pyw, py = _run(tmp_path, path_dirs=[])
    assert (pyw, py) == ("", "")


def test_unreadable_folders_do_not_abort_the_search(tmp_path):
    """用户目录下可能有没权限枚举的东西: 兜底要跳过而不是让启动器报错退出。"""
    user = tmp_path / "LocalApp" / "Programs" / "Python"
    locked = user / "Python399"
    locked.mkdir(parents=True)
    _touch(str(user / "Python312" / "pythonw.exe"))
    _touch(str(user / "Python312" / "python.exe"))
    pyw, _ = _run(tmp_path, path_dirs=[], local_app=tmp_path / "LocalApp")
    assert pyw == str(user / "Python312" / "pythonw.exe")


def test_console_launcher_looks_for_the_same_things():
    """run-core.bat 是排错入口, 它挑解释器的口径不能和 vbs 各说一套到看不懂的地步。"""
    bat = open(BAT, encoding="utf-8").read()
    assert "py.exe" in bat and "where python.exe" in bat
    assert "core.main_gui" in bat and "PYTHONPATH" in bat
    assert "python\\python.exe" not in bat, "核心版不许要求包内有解释器"
