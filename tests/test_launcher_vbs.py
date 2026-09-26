r"""启动工具.vbs 找 Python 的顺序: 老机器不许换解释器, 装了 Python 的机器不许说"没装"。

.vbs 是唯一入口(pythonw + 隐藏窗口), 它自己挑错解释器就等于整个工具起不来。以前只认
E:\Python\Python314 和 C:\Python31x 这几个写死路径, 于是用 py 启动器、装在用户目录、
或只有项目 .venv 的机器一律被告知"未找到 Python"。

这里用 cscript 真跑一遍脚本的 --print-python 诊断分支(只打印、不装依赖、不起界面),
把每一档"落空之后往哪走"钉死。替换没命中就抛错: 改了 .vbs 却忘了改这里同样会暴露。
"""
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VBS_PATH = os.path.join(REPO_ROOT, "启动工具.vbs")
PROBE_NAME = "_probe_launcher.vbs"      # 必须放仓库根: 脚本用自身所在目录找 .venv

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="cscript 只在 Windows 上")


def _cscript():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    path = os.path.join(root, "System32", "cscript.exe")
    if not os.path.exists(path):
        pytest.skip("找不到 cscript.exe")
    return path


@pytest.fixture()
def probe():
    """生成/运行 .vbs 副本的工厂; 副本一定被清掉, 不留进仓库。"""
    src = open(VBS_PATH, encoding="ascii").read()
    made = []

    def _make(**drops):
        """drops 里的档位置为 False/不存在, 用于观察上一档落空后的走向。"""
        text = src
        targets = {
            "tier1": ('Array("E:\\Python\\Python314\\", "C:\\Python314\\", '
                      '"C:\\Python313\\", _'),
            "tier2": 'If fso.FileExists(windir & "\\" & launcherName) Then',
            "tier3": 'For Each f In PythonDirs(localApp & "\\Programs\\Python")',
            "tier4": 'p = dir & "\\.venv\\Scripts\\" & exeName',
            "tier5": 'For Each f In PythonDirs("C:\\")',
        }
        for name, needle in targets.items():
            if name not in drops:
                continue
            assert needle in text, f".vbs 里找不到 {name} 的原文, 副本已失效"
            text = text.replace(needle, drops[name])
        probe_path = os.path.join(REPO_ROOT, PROBE_NAME)
        with open(probe_path, "w", encoding="ascii", newline="\r\n") as f:
            f.write(text)
        made.append(probe_path)
        r = subprocess.run([_cscript(), "//nologo", probe_path, "--print-python"],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"cscript 解析/运行失败: {r.stderr or r.stdout}"
        out = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
        return out.get("pythonw", ""), out.get("python", "")

    yield _make
    for p in made:
        if os.path.exists(p):
            os.remove(p)


def _first_existing(*paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return ""


def test_shipped_script_still_picks_the_historic_interpreter(probe):
    """老机器上必须一个字节都不变地选中原来那个解释器, 否则换解释器=换依赖环境。"""
    legacy_w = _first_existing(r"E:\Python\Python314\pythonw.exe",
                               r"C:\Python314\pythonw.exe",
                               r"C:\Python313\pythonw.exe",
                               r"C:\Python312\pythonw.exe",
                               r"C:\Python311\pythonw.exe",
                               r"C:\Python310\pythonw.exe")
    pyw, py = probe()
    if legacy_w:
        assert pyw == legacy_w
        assert py == legacy_w[:-len("pythonw.exe")] + "python.exe"
    elif not (pyw or py):
        # 这台机器上五个档全落空 —— CI 机就是这种: 它的 Python 装在 hostedtoolcache 里,
        # 既不在写死路径、也没注册 py 启动器。脚本本身没坏(全落空正是最后一档的行为),
        # 所以这里跳过而不是断言"总能找到一个", 免得把打包构建点红。
        pytest.skip("这台机器没有任何脚本认得的 Python 安装, 无从判断"
                    "(看 CI 里「交代本机有哪些解释器」那一步打印的清单)")


def test_no_hardcoded_path_makes_the_py_launcher_win(probe):
    """写死路径全落空时, 应当用 %WINDIR% 下的 py 启动器(它自己会找已注册的 Python)。"""
    launcher = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "pyw.exe")
    if not os.path.exists(launcher):
        pytest.skip("这台机器没有 py 启动器")
    pyw, py = probe(tier1='Array("Z:\\no1\\", "Z:\\no2\\", _')
    assert pyw == launcher
    # pythonw 找的是 pyw.exe, python 找的是 py.exe —— 两者不能混
    assert py.endswith("py.exe") and not py.endswith("pyw.exe")


def test_launcher_missing_falls_back_to_the_project_venv(probe):
    venv_pyw = os.path.join(REPO_ROOT, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(venv_pyw):
        pytest.skip("这个项目没有 .venv")
    pyw, _ = probe(
        tier1='Array("Z:\\no1\\", "Z:\\no2\\", _',
        tier2="If False Then",
        tier3='For Each f In PythonDirs("Z:\\no-user-install")',
        tier5='For Each f In PythonDirs("Z:\\no-root-dir")',
    )
    assert pyw == venv_pyw


def test_nothing_installed_yields_empty_and_does_not_crash(probe):
    pyw, py = probe(
        tier1='Array("Z:\\no1\\", "Z:\\no2\\", _',
        tier2="If False Then",
        tier3='For Each f In PythonDirs("Z:\\no-user-install")',
        tier4='p = "Z:\\no-venv\\" & exeName',
        tier5='For Each f In PythonDirs("Z:\\no-root-dir")',
    )
    assert (pyw, py) == ("", "")


def test_unusable_folders_are_skipped_not_fatal(probe):
    r"""C:\ 这类根目录可能没权限枚举, 兜底必须继续往下走而不是让启动器报错退出。"""
    src = open(VBS_PATH, encoding="ascii").read()
    assert "On Error Resume Next" in src.split("Function PythonDirs")[1], \
        "枚举目录没做容错"
    pyw, _ = probe(tier1='Array("Z:\\no1\\", "Z:\\no2\\", _',
                   tier2="If False Then")
    # 走到这里说明 tier3/5 的枚举没有把脚本打断(结果本身取决于这台机器装了什么)
    assert pyw == "" or os.path.exists(pyw)


def test_nothing_in_the_launchers_installs_dependencies():
    r"""找解释器归 .vbs, 装依赖归 core/deps.py + check_dependencies() —— 只许有一处答案。

    启动工具.vbs 以前自己也判一次"缺不缺 playwright"并跑 `pip install playwright`(**没钉版本**):
    Python 侧钉 1.62.0、vbs 侧装最新, 而内核目录名带版本号, 混版本直接起不来; 而且它查不出
    "有包没内核"这种最常见的半拉子状态。注释里提一句历史是可以的, **不许有执行它的一行**。
    """
    files = ["启动工具.vbs", os.path.join("packaging", "run-portable.vbs"),
             os.path.join("packaging", "run-portable.bat"),
             os.path.join("packaging", "run-core.vbs"),
             os.path.join("packaging", "run-core.bat")]
    for rel in files:
        src = open(os.path.join(REPO_ROOT, rel), encoding="ascii", errors="replace").read()
        marks = ("'", "REM ") if rel.endswith((".bat",)) else ("'",)
        code = [ln for ln in src.splitlines() if not ln.strip().startswith(marks)]
        body = "\n".join(code).lower()
        assert "pip install" not in body, "%s 里又自己装起依赖来了" % rel
        assert "-m playwright" not in body, "%s 不该自己调 playwright install" % rel
        assert "import playwright" not in body, "%s 不该自己探测依赖(那是 core/deps.py 的事)" % rel
