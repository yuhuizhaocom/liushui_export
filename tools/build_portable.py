"""组装绿色版(免安装)目录: 只带程序, 绝不带任何用户数据或登录凭证。

正常由 GitHub Actions 调用, 也可以本地跑一遍看结构:

    python tools/build_portable.py --out dist/流水导出工具 --verify

产物布局:

    <out>/run-portable.vbs  run-portable.bat  README-绿色版.txt
    <out>/python/...                  (--python-dir 给了才拷, 含 tkinter 的完整解释器)
    <out>/app/core/ platforms/ tools/ *.md
    <out>/app/runtime/ms-playwright/  (--browsers-dir: 内核目录名带版本, 必须同版本生成)
    <out>/app/site-packages/          (--site-dir: playwright 及其依赖)

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
LAUNCHER_FILES = ("run-portable.vbs", "run-portable.bat", "README-绿色版.txt")
PRUNE_NAMES = {"__pycache__", ".pytest_cache", ".venv", ".git", ".idea", "dist", "docs"}
# 成品里出现这些就是打包事故(用户数据/凭证外泄或互相覆盖)
FORBIDDEN = ("browser_data", "login_state.json", "downloads", "recordings",
             "settings.json", "selection_state.json", "scheduled_tasks.json",
             "workspace.json", ".liushui_workspace.json", "stats.jsonl")


def _copy_tree(src, dst):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*PRUNE_NAMES), dirs_exist_ok=True)


def build(out_dir, python_dir="", site_dir="", browsers_dir="", quiet=False):
    def say(*a):
        if not quiet:
            emit(*a)

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

    for name in LAUNCHER_FILES:
        shutil.copy2(os.path.join(PKG_LAUNCHERS, name), os.path.join(out, name))
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


def verify(out):
    """用成品目录里的解释器(没有就用当前 python)验一遍路径解析。

    ⚠ 必须把 LIUSHUI_DATA_DIR 指到临时目录再跑: 只 import 一次 `core.logger` 就会在工作
    空间里建出 logs/, 直接按成品默认路径跑会把垃圾写进包里(而且再靠它判"是否新包"就废了)。
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
        "from core.browser import resolve_browsers_path;"
        "assert os.path.normpath(config.DATA_ROOT) == os.path.normpath(r'%s'), config.DATA_ROOT;"
        "assert config.PENDING_PICK is False, '环境变量指定过了就不该再问用户';"
        "assert os.path.normpath(config.DOWNLOAD_DIR) == os.path.normpath(r'%s'), config.DOWNLOAD_DIR;"
        "assert not any(os.path.exists(os.path.join(config.ROOT_DIR, m)) for m in config.LEGACY_MARKERS), "
        "'成品里不该有 settings.json 等用户配置';"
        "print('DATA_ROOT', config.DATA_ROOT);"
        "print('BROWSERS', resolve_browsers_path(env={}) or '(未随包, 用系统默认位置)');"
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
    a = ap.parse_args(argv)

    emit(f"组装绿色包 → {a.out}")
    out = build(a.out, a.python_dir, a.site_dir, a.browsers_dir)
    if a.verify:
        verify(out)
    bad = scan_forbidden(out)          # verify 之后再扫: 它自己会跑一次代码, 别把垃圾留在包里
    if bad:
        for p in bad:
            emit("  ! 不该进包:", p)
        fail("成品含用户数据/登录凭证, 中止")
    emit("  白名单检查通过: 无 browser_data / 登录态 / 账单 / 配置残留")
    if a.zip:
        archive = shutil.make_archive(out, "zip", root_dir=os.path.dirname(out),
                                      base_name=os.path.basename(out))
        emit("  已压缩:", archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
