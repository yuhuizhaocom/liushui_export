"""依赖体检: 缺哪一样、怎么补, 说人话。

绿色版分两版发货: **全量版**连解释器、playwright、浏览器内核一起带; **核心版**只带代码,
指望本机有 Python。核心版第一次在别人的机器上跑, 缺的多半不是"程序"而是下面三样之一:

    ① 没有 playwright 包        —— 装上就行
    ② 版本和实测过的对不上      —— 能跑就别拦, 但要说一声(内核目录名带版本, 版本一漂移
                                    行为就可能变, 出问题第一个该想到这里)
    ③ 有包但没有浏览器内核      —— pip 装成功、`playwright install` 没跑或中途断网, 这是
                                    **最常见**的半拉子状态, 而以前这一步查不出来: 症状推迟
                                    到点导出、Chromium 起不来时才炸, 报错业务用户读不懂

所以本模块**既不 import playwright、也不碰界面** —— 缺依赖时这两件事恰恰做不到(它就是被
`core/browser.py` 顶层那句 import 挡住的)。结论由 `main_gui.check_dependencies()` 渲染成
弹窗, 这里只负责"看清楚现场 + 给出命令"。

红线(和用户机上的其它兜底一样): 体检**自身**出任何错一律折成"不知道"并放行, 绝不因为
"没查清楚"就不让人导出账单; 但吞掉的东西要写进 notes, 日志里看得见。
"""
import os
import sys
from collections import namedtuple

# 与 requirements.txt 的 pin、以及 .github/workflows/build-portable.yml 的 PLAYWRIGHT_VERSION
# 三处必须一致: requirements.txt 由 tests 钉住, 工作流由 CI 里那一步断言钉住。
REQUIRED_PLAYWRIGHT_VERSION = "1.62.0"
PIP_SPEC = "playwright==%s" % REQUIRED_PLAYWRIGHT_VERSION
INSTALL_CMD = "%s -m pip install " + PIP_SPEC
KERNEL_CMD = "%s -m playwright install chromium"
MIN_PYTHON = (3, 10)          # 启动工具.vbs 对用户明说的下限; 低于它只提示不拦

_UNSET = object()             # 区分"没传"和"传了 None(=把这一档关掉)"

# 体检结论的四种取值
OK = "ok"                      # 齐活
NO_PACKAGE = "no_playwright"   # ① 包都没有
BAD_VERSION = "bad_version"    # ② 有包但版本不对(只问不拦)
NO_KERNEL = "no_kernel"        # ③ 有包没内核
UNKNOWN = "unknown"            # 体检自己出错 → 放行

Deps = namedtuple("Deps", "verdict python python_ok have_playwright version expected "
                          "kernel_dir searched missing_bits notes")


def installed_version(dist=PIP_SPEC.split("==")[0]):
    """已装版本号字符串; 没装或读不到元数据返回空串(不抛)。

    走 importlib.metadata 而不是 `import playwright` —— 后者会执行整个包(它一 import 就
    准备好驱动与网络), 而且装了别的版本时我们也只是想知道"装的是几点几"。
    """
    try:
        from importlib import metadata
        return (metadata.version(dist) or "").strip()
    except Exception:
        return ""


def has_module(name):
    """模块在不在 sys.path 上(不执行它)。查不清就当"在", 别拦人。"""
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return True


def _installed_kernel(bdir):
    """这个 chromium-* 目录是真能起的内核, 还是下到一半的壳?

    先认 Playwright 自己的完成标记(`INSTALLATION_COMPLETE`); 没标记就实打实找可执行文件 ——
    子目录名随版本变过(开发机上是 `chrome-win64`, 老版本是 `chrome-win`), 所以不写死它。
    """
    try:
        if os.path.isfile(os.path.join(bdir, "INSTALLATION_COMPLETE")):
            return True
        with os.scandir(bdir) as it:
            for e in it:
                try:
                    if not e.is_dir():
                        continue
                except OSError:
                    continue
                if any(os.path.isfile(os.path.join(e.path, n)) for n in ("chrome.exe", "chrome")):
                    return True
    except Exception:
        return False
    return False


def find_chromium_dir(root):
    """内核根下面的 `chromium-<版本号>` 目录(要真装齐才算)。

    只认 `chromium-*`: `chromium_headless_shell-*` 服务的是 headless shell, 我们用持久化
    上下文起有头/无头浏览器, 只有那个壳照样起不来。
    """
    if not root or not os.path.isdir(root):
        return ""
    try:
        names = sorted(n for n in os.listdir(root) if n.startswith("chromium-"))
    except Exception:
        return ""
    for n in names:
        bdir = os.path.join(root, n)
        if _installed_kernel(bdir):
            return bdir
    return ""


def kernel_dir(env=None, program_dir=None, fallback=_UNSET):
    """(找到的内核目录, 去哪几处找过) —— 与 core.config.resolve_browsers_path 同一套优先级。

    解析出目录了却没找到内核时**不回退**到默认位置: 运行时 env 已经按解析结果设好了,
    Playwright 也只认那一个, 回退只会让体检和实际行为各说一套。
    `fallback` 是给测试留的口子 —— 开发机上 `C:\\pw_browsers` 真实存在, 不注入就假装不了"干净机器"。
    """
    from .config import resolve_browsers_path, default_browsers_root
    kw = {} if fallback is _UNSET else {"fallback": fallback}
    resolved = resolve_browsers_path(env=env, program_dir=program_dir, **kw)
    searched = resolved or default_browsers_root(env=env)
    return find_chromium_dir(searched), searched


def _python_text_and_ok(python=None):
    """(给人看的版本号, 够不够新)。注入 python 时按注入的值判, 便于离线测那条提醒。"""
    if python:
        parts = python.split(".")
        try:
            return python, (int(parts[0]), int(parts[1])) >= MIN_PYTHON
        except (ValueError, IndexError):
            return python, True          # 读不懂就不下判断(宁可漏提醒, 不误伤)
    text = "%d.%d.%d" % sys.version_info[:3]
    return text, sys.version_info[:2] >= MIN_PYTHON


def probe(env=None, program_dir=None, version=None, python=None, fallback=_UNSET):
    """看一眼现场, 返回 Deps。参数都能注入, 是为了离线测试不必真去装东西。"""
    env = os.environ if env is None else env
    notes = []
    try:
        py_text, py_ok = _python_text_and_ok(python)
        have = has_module("playwright")
        got = version if version is not None else (installed_version() if have else "")
        kernel, searched = kernel_dir(env=env, program_dir=program_dir, fallback=fallback)
    except Exception as e:                      # 体检自身出错 = 不拦人
        return Deps(UNKNOWN, python or "?", True, True, "", REQUIRED_PLAYWRIGHT_VERSION,
                    "", "", [], ["体检没跑成, 已跳过: %r" % (e,)])

    missing = []
    if not have:
        verdict = NO_PACKAGE
        missing.append("playwright 包")
    elif got and got != REQUIRED_PLAYWRIGHT_VERSION:
        verdict = BAD_VERSION
        missing.append("和实测一致的 playwright 版本")
    elif not kernel:
        verdict = NO_KERNEL
        missing.append("Chromium 浏览器内核")
    else:
        verdict = OK
    if not have and not kernel:
        missing.append("Chromium 浏览器内核")
    if got and not have:
        notes.append("playwright 包没装, 却读到了版本号 %s(环境残留?), 按没装处理" % got)
    if have and not got:
        notes.append("读不到 playwright 的版本元数据(装法少见), 版本这一档跳过不判")
    if not py_ok:
        notes.append("Python %s 低于建议的 %d.%d, 可能缺语法或 tkinter" % (py_text, *MIN_PYTHON))
    return Deps(verdict, py_text, py_ok, have, got, REQUIRED_PLAYWRIGHT_VERSION,
                kernel, searched, missing, notes)


def describe(d):
    """给人看的几行(界面日志与弹窗共用, 免得两处文案漂移)。"""
    lines = []
    if d.verdict == OK:
        lines.append("依赖齐全: playwright %s, 内核 %s" % (d.version or "?", d.kernel_dir))
    elif d.verdict == NO_PACKAGE:
        lines.append("缺少依赖: 没有 playwright 包(需要 %s)" % d.expected)
    elif d.verdict == BAD_VERSION:
        lines.append("playwright 版本和实测过的不一致: 本机 %s, 应为 %s" % (d.version, d.expected))
        lines.append("选「继续用当前版本」也能跑; 真出问题时, 第一个该怀疑的就是这里。")
    elif d.verdict == NO_KERNEL:
        lines.append("有 playwright 但机器上没有 Chromium 内核(找过: %s)" % (d.searched or "默认位置"))
    else:
        lines.append("没能完成依赖检查, 直接继续(下面的日志里会写明原因)")
    for n in d.notes:
        lines.append("[提醒] " + n)
    return lines


def fix_commands(d, python_exe=None):
    """缺哪样给哪条命令 —— 装不上时原样打在窗口里, 用户/IT 复制就能跑。"""
    py = '"%s"' % (python_exe or sys.executable)
    out = []
    if d.verdict == NO_PACKAGE or not d.have_playwright:
        out.append(("装上 playwright(钉死实测过的版本)", INSTALL_CMD % py))
    if d.verdict == BAD_VERSION:
        out.append(("换成实测过的版本", INSTALL_CMD % py))
    if d.verdict in (NO_PACKAGE, NO_KERNEL) or not d.kernel_dir:
        out.append(("下载浏览器内核(约 100 多 MB, 需要联网)", KERNEL_CMD % py))
    return out


OFFLINE_HINT = (
    "装不上(内网/代理/证书拦截)时的两条退路:\n"
    "  1) 找一台装好的机器, 把它的 ms-playwright 目录整个拷过来, 再设环境变量\n"
    "     PLAYWRIGHT_BROWSERS_PATH 指到那个目录(内核目录名带版本号, 必须与 playwright 同版本);\n"
    "  2) 直接用「全量版」压缩包 —— 它连 Python、playwright 和内核一起带, 不需要联网。"
)
