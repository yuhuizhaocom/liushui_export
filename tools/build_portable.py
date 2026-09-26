"""组装绿色版(免安装)目录: 只带程序, 绝不带任何用户数据或登录凭证。

正常由 GitHub Actions 调用, 也可以本地跑一遍看结构:

    python tools/build_portable.py --out dist/流水导出工具 --verify
    python tools/build_portable.py --out dist/核心版 --flavor core

两个版本(`--flavor`), 程序部分一模一样, 区别只在"带不带依赖":

    全量版  <out>/run-portable.vbs|.bat + README-绿色版.txt
            <out>/python/...                  (--python-dir 给了才拷, 含 tkinter 的完整解释器)
            <out>/app/core/ platforms/ tools/ *.md
            <out>/app/site-packages/          (--site-dir: playwright 及其依赖)
            <out>/app/runtime/ms-playwright/  (--browsers-dir: 内核目录名带版本, 必须同版本生成)

    核心版  <out>/run-core.vbs|.bat + README-核心版.txt   —— 用本机已有的 Python, 什么都不带;
            缺的依赖由启动后的体检(core/deps.py)引导安装。给它传 --python-dir/--site-dir/
            --browsers-dir 会直接失败: 那是"复制粘贴错了目标"最常见的样子。

白名单复制(而不是"整仓删掉一些"): 新增的数据目录不会因为忘了加排除项而被打进包里。
`--verify` 还会扫一遍成品, 出现 browser_data / login_state.json / downloads 等就直接失败。

输出编码: Actions 的 runner 是英文区域, stdout 是管道 → Python 按 cp1252 建流, 于是
`print("组装绿色包 → …")` 会抛 UnicodeEncodeError, 失败原因是"日志写不出来"而不是打包出错。
`harden_streams()` 专门兜这个, 详见函数注释。
"""
import argparse
import codecs
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_LAUNCHERS = os.path.join(ROOT, "packaging")
WORKSPACE_ENV = "LIUSHUI_DATA_DIR"      # 与 core/config.WORKSPACE_ENV 同值(下面会尽量取真的)

# 中文加箭头: 西欧代码页(cp1252)两个都编不出来, 拿它当探针比只测一个汉字靠谱
PROBE = "组装→校验"


def encoding_ok(enc):
    """这个编码写得出中文吗。未知编码名/空值一律按"不行"处理。"""
    if not enc:
        return False
    try:
        return codecs.lookup(enc).encode(PROBE)[1] == len(PROBE)
    except (LookupError, UnicodeEncodeError):
        return False


def harden_streams():
    """流写不出中文就把它换成 UTF-8, 换得动才换。返回被改过的流名。

    只在真要写中文的编码不匹配时才动 —— 开发机的简体中文控制台(cp936)本来就打得开中文,
    强改成 UTF-8 反而会花屏。Actions 上换完是正好的: 它的日志按 UTF-8 显示。
    """
    fixed = []
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or encoding_ok(getattr(stream, "encoding", "")):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue        # 被测试捕获替换掉的流没有 reconfigure, 交给 emit 兜底
        if encoding_ok(getattr(stream, "encoding", "")):
            fixed.append(name)
    return fixed


def emit(*a):
    """print 的稳妥版: 换不动流(自定义/被捕获的输出)时退化成 \\uXXXX, 宁可丑也不能丢行。"""
    text = " ".join(str(x) for x in a)
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def fail(message):
    """退出前先把流理顺: 中文的报错信息自己触发 UnicodeEncodeError 是最坑的失败方式。"""
    harden_streams()
    raise SystemExit(message)


def _workspace_env():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    try:
        from core.config import WORKSPACE_ENV as real
        return real
    except Exception:
        return WORKSPACE_ENV

# 进包的东西: 程序本体 + 面向维护者的文档
APP_ITEMS = ("core", "platforms", "tools", "CODE_WIKI.md", "使用说明.md", "脚本编写指南.md")
# 每个版本的启动入口与说明。文件名**不同**是有意的: 用户截图说"我双击的是 run-core.vbs"
# 就能立刻判断他手上是哪一版 —— 两版共用一个名字时, 这件事只能靠猜。
LAUNCHERS = {
    "full": ("run-portable.vbs", "run-portable.bat", "README-绿色版.txt"),
    "core": ("run-core.vbs", "run-core.bat", "README-核心版.txt"),
}
# 核心版**不许**带的三样(带了就不是核心版, 或者是复制粘贴错了目标)
CORE_FORBIDDEN = ("python", "site-packages", "runtime")
PRUNE_NAMES = {"__pycache__", ".pytest_cache", ".venv", ".git", ".idea", "dist", "docs"}
# 成品里出现这些就是打包事故(用户数据/凭证外泄或互相覆盖)
FORBIDDEN = ("browser_data", "login_state.json", "downloads", "recordings",
             "settings.json", "selection_state.json", "scheduled_tasks.json",
             "workspace.json", ".liushui_workspace.json", "stats.jsonl")


def _copy_tree(src, dst):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*PRUNE_NAMES), dirs_exist_ok=True)


def build(out_dir, python_dir="", site_dir="", browsers_dir="", quiet=False, flavor="full"):
    def say(*a):
        if not quiet:
            emit(*a)

    if flavor not in LAUNCHERS:
        fail("未知的 --flavor: %s(可选 %s)" % (flavor, " / ".join(sorted(LAUNCHERS))))
    if flavor == "core":
        # 核心版的意义就是"什么都不带"。CI 里两个版本只差三个参数, 参数抄错太容易了,
        # 所以这里宁可拒跑: 出一个"名字叫 core、里面却带着解释器"的包最坏。
        given = [n for n, v in (("--python-dir", python_dir), ("--site-dir", site_dir),
                               ("--browsers-dir", browsers_dir)) if v]
        if given:
            fail("核心版不带依赖/解释器/内核, 但收到了 %s —— 这是全量版的参数" % " ".join(given))

    out = os.path.abspath(out_dir)
    app = os.path.join(out, "app")
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(app)

    for item in APP_ITEMS:
        src = os.path.join(ROOT, item)
        if not os.path.exists(src):
            fail(f"缺少要打进包里的东西: {src}")
        dst = os.path.join(app, item)
        if os.path.isdir(src):
            _copy_tree(src, dst)
        else:
            shutil.copy2(src, dst)
        say(f"  + app/{item}")

    for name in LAUNCHERS[flavor]:
        src = os.path.join(PKG_LAUNCHERS, name)
        if not os.path.isfile(src):
            fail("缺少启动器: %s" % src)
        shutil.copy2(src, os.path.join(out, name))
        say(f"  + {name}")

    if python_dir:
        _copy_tree(os.path.abspath(python_dir), os.path.join(out, "python"))
        say(f"  + python/  <- {python_dir}")
    if site_dir:
        dst = os.path.join(app, "site-packages")
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(site_dir):
            src = os.path.join(site_dir, name)
            shutil.copytree(src, os.path.join(dst, name), dirs_exist_ok=True) \
                if os.path.isdir(src) else shutil.copy2(src, dst)
        say(f"  + app/site-packages/  <- {site_dir}")
    if browsers_dir:
        dst = os.path.join(app, "runtime", "ms-playwright")
        _copy_tree(browsers_dir, dst)
        say(f"  + app/runtime/ms-playwright/  <- {browsers_dir}")
    return out


def scan_forbidden(out):
    """成品里不许有用户数据/登录凭证。返回违规路径列表。"""
    hits = []
    for dirpath, dirnames, filenames in os.walk(out):
        for name in list(dirnames) + list(filenames):
            if name in FORBIDDEN:
                hits.append(os.path.join(dirpath, name))
    return hits


def check_flavor(out, flavor):
    """成品结构对不对得上它的名义版本。返回问题列表(空=没问题)。

    核心版最怕的两种翻车: ①名字挂着 core、里面却躺着解释器/依赖/内核(参数抄错或目录没清),
    发出去就是一个"看起来更小、其实一模一样"的包; ②启动器拿错版本(两版共用一个文件名时
    最容易), 用户双击后只会得到"找不到 python\\pythonw.exe"。
    """
    problems = []
    app = os.path.join(out, "app")
    if flavor == "core":
        for name in CORE_FORBIDDEN:
            p = os.path.join(out, name) if name == "python" else os.path.join(app, name)
            if os.path.exists(p):
                problems.append("核心版不该带 %s: %s" % (name, p))
    for entry in sorted(os.listdir(out)):
        if entry.endswith((".vbs", ".bat")) and entry not in LAUNCHERS[flavor]:
            problems.append("包里混进了别的地方的入口: %s(这一版应该是 %s)"
                            % (entry, " / ".join(LAUNCHERS[flavor][:2])))
    return problems


def verify(out):
    """验一遍成品的路径解析: 工作空间被尊重、派生路径跟着走、包里没有用户配置。

    ⚠ 必须把 LIUSHUI_DATA_DIR 指到临时目录再跑: 只 import 一次 `core.logger` 就会在工作
    空间里建出 logs/, 直接按成品默认路径跑会把垃圾写进包里(而且再靠它判"是否新包"就废了)。

    ⚠ 这里**只 import core.config**, 不碰 core.browser: 后者顶层就 import playwright, 那样
    自检就得等依赖装好才能跑, 核心版更是永远跑不了(它根本没有依赖)。依赖能不能 import、
    内核起不起得来, 是工作流"冒烟测试"那一步的事 —— 它用包内的解释器起一次真 Chromium。
    """
    py = os.path.join(out, "python", "python.exe")
    py = py if os.path.isfile(py) else sys.executable
    scratch = os.path.abspath(os.path.join(tempfile.mkdtemp(prefix="liushui-verify-"), "ws"))
    env = dict(os.environ)
    workspace_env = _workspace_env()
    env[workspace_env] = scratch                       # 显式指定工作空间, 不污染成品
    env["PYTHONIOENCODING"] = "utf-8"                  # 子进程要打中文路径, 别按 runner 的 cp1252 收
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.join(out, "app", "site-packages"), os.path.join(out, "app")])
    code = (
        "import os, sys;"
        "sys.argv = [sys.argv[0]];"
        "from core import config;"
        "assert 'playwright' not in sys.modules, '自检不许依赖 playwright(核心版没有它)';"
        "assert os.path.normpath(config.DATA_ROOT) == os.path.normpath(r'%s'), config.DATA_ROOT;"
        "assert config.PENDING_PICK is False, '环境变量指定过了就不该再问用户';"
        "assert os.path.normpath(config.DOWNLOAD_DIR) == os.path.normpath(r'%s'), config.DOWNLOAD_DIR;"
        "assert not any(os.path.exists(os.path.join(config.ROOT_DIR, m)) for m in config.LEGACY_MARKERS), "
        "'成品里不该有 settings.json 等用户配置';"
        "print('DATA_ROOT', config.DATA_ROOT);"
        "print('BROWSERS', config.resolve_browsers_path(env={}) or '(未随包, 用系统默认位置)');"
    ) % (scratch, os.path.join(scratch, "downloads"))
    r = subprocess.run([py, "-c", code], cwd=out, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    shutil.rmtree(os.path.dirname(scratch), ignore_errors=True)
    emit(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        fail("包内路径自检失败")


def main(argv=None):
    harden_streams()          # 第一件正事: 让下面这些中文提示写得出来(见函数注释)
    ap = argparse.ArgumentParser(description="组装绿色版目录")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--python-dir", default="", help="要一起打包的 Python 安装目录")
    ap.add_argument("--site-dir", default="", help="pip install --target 出来的依赖目录")
    ap.add_argument("--browsers-dir", default="", help="ms-playwright 内核目录")
    ap.add_argument("--zip", action="store_true", help="顺手压成 zip")
    ap.add_argument("--verify", action="store_true", help="组装后自检路径解析与违禁文件")
    ap.add_argument("--flavor", default="full", choices=tuple(sorted(LAUNCHERS)),
                    help="full=全量版(自带 Python+依赖+内核) / core=核心版(只带程序)")
    a = ap.parse_args(argv)

    emit(f"组装{'全量' if a.flavor == 'full' else '核心'}版 → {a.out}")
    out = build(a.out, a.python_dir, a.site_dir, a.browsers_dir, flavor=a.flavor)
    if a.verify:
        verify(out)
    bad = scan_forbidden(out)          # verify 之后再扫: 它自己会跑一次代码, 别把垃圾留在包里
    if bad:
        for p in bad:
            emit("  ! 不该进包:", p)
        fail("成品含用户数据/登录凭证, 中止")
    wrong = check_flavor(out, a.flavor)
    if wrong:
        for p in wrong:
            emit("  ! 结构和版本对不上:", p)
        fail("成品结构与 --flavor 不一致, 中止")
    emit("  白名单检查通过: 无 browser_data / 登录态 / 账单 / 配置残留")
    if a.zip:
        # ⚠ make_archive 的**第一个位置参数就是 base_name(输出路径, 不带 .zip)**,
        # 写成 make_archive(out, "zip", ...) 会同时把 out 当成输出与打包目标 → TypeError。
        # zip 里放"包的内容"(与 CI 的 Compress-Archive "$PKG\*" 一致), 不再套一层目录。
        archive = shutil.make_archive(base_name=out, format="zip", root_dir=out, base_dir=".")
        emit("  已压缩:", archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
