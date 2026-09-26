"""打包脚本: 白名单组装 + "成品里不许有用户数据"的守卫。

CI 只是把这两个函数当检查用, 所以它们本身要有测试 —— 万一以后有人往 APP_ITEMS 里加了
`docs`/仓库根整目录, 泄漏的就是本机所有商户的登录凭证。

另一组测试盯的是输出编码: Actions 的 runner 是英文区域(cp1252), 中文日志一度把整个构建
打死在 `print("组装绿色包 → …")` 上, 报的是 UnicodeEncodeError 而不是打包失败。
"""
import codecs
import io
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import build_portable as bp     # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "tools", "build_portable.py")


@pytest.fixture()
def out(tmp_path):
    return str(tmp_path / "pkg")


def test_build_copies_whitelist_only(out, tmp_path):
    bp.build(out)
    app = os.path.join(out, "app")
    assert os.path.isfile(os.path.join(app, "core", "main_gui.py"))
    assert os.path.isdir(os.path.join(app, "platforms", "youzan"))
    assert os.path.isfile(os.path.join(app, "tools", "build_portable.py"))
    assert os.path.isfile(os.path.join(out, "run-portable.vbs"))
    assert os.path.isfile(os.path.join(out, "run-portable.bat"))
    assert os.path.isfile(os.path.join(out, "README-绿色版.txt"))
    # 仓库根那些运行时产物一个都不该跟着走
    for junk in ("tests", "docs", "downloads", "browser_data", "logs", ".venv", ".git",
                 "settings.json", "selection_state.json"):
        assert not os.path.exists(os.path.join(app, junk)), junk
        assert not os.path.exists(os.path.join(out, junk)), junk


def test_build_drops_pycache(out):
    bp.build(out)
    for dirpath, dirnames, _files in os.walk(os.path.join(out, "app")):
        assert "__pycache__" not in dirnames, dirpath


def test_missing_item_fails_loudly(out, monkeypatch, tmp_path):
    """白名单里点名了却不存在 → 直接失败, 不能默默出一个少文件的包。"""
    monkeypatch.setattr(bp, "APP_ITEMS", ("core", "没有这个东西.md"))
    with pytest.raises(SystemExit):
        bp.build(out)


def test_scan_catches_user_data_and_credentials(out, tmp_path):
    app = os.path.join(out, "app")
    os.makedirs(os.path.join(app, "browser_data", "youzan", "店A"), exist_ok=True)
    with open(os.path.join(app, "browser_data", "youzan", "店A",
                           "login_state.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    with open(os.path.join(app, "settings.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    hits = bp.scan_forbidden(out)
    joined = " ".join(hits)
    assert "login_state.json" in joined and "browser_data" in joined and "settings.json" in joined
    # 只报告不动文件: 删谁、留谁是打包脚本外面决定的
    assert os.path.isfile(os.path.join(app, "settings.json"))


def test_clean_package_passes_the_scan(out):
    bp.build(out)
    assert bp.scan_forbidden(out) == []


# --------------------------------------------------------------------------- 输出编码

class _NoReconfigure:
    """只吃得下 ASCII、又没有 reconfigure 的流 —— 模拟 stdout 被框架换掉的情形。"""

    encoding = "cp1252"

    def __init__(self):
        self.chunks = []

    def write(self, text):
        self.chunks.append(codecs.encode(text, self.encoding))   # 中文在这里抛
        return len(text)

    def flush(self):
        pass

    def value(self):
        return b"".join(self.chunks).decode(self.encoding, "replace")


def _stream(enc):
    return io.TextIOWrapper(io.BytesIO(), encoding=enc, errors="strict")


def test_encoding_probe_only_accepts_codepages_with_chinese():
    assert bp.encoding_ok("utf-8") and bp.encoding_ok("UTF8") and bp.encoding_ok("cp936")
    assert not bp.encoding_ok("cp1252")          # Actions runner 就是这一个
    assert not bp.encoding_ok("ascii")
    assert not bp.encoding_ok("") and not bp.encoding_ok(None)
    assert not bp.encoding_ok("no-such-codec")   # 未知编码名不能当成"能写"


def test_harden_switches_only_the_stream_that_cannot_write(monkeypatch):
    bad = _stream("cp1252")                        # Actions runner 的那一档
    monkeypatch.setattr(sys, "stdout", bad)
    assert bp.harden_streams() == ["stdout"]
    bp.emit("组装绿色包 → 校验")                    # 修复前这一句就是构建失败的原因
    bad.flush()
    bad.buffer.seek(0)
    assert "组装绿色包" in bad.buffer.read().decode("utf-8")   # 换完才写得进管道

    good = _stream("utf-8")                        # 写得出来的就不动
    monkeypatch.setattr(sys, "stdout", good)
    assert bp.harden_streams() == [] and good.encoding == "utf-8"


def test_harden_leaves_console_encoding_alone(monkeypatch):
    """简体中文控制台(cp936)本来打得开中文, 强转 UTF-8 会花屏 —— 不许动。"""
    zh = _stream("cp936")
    monkeypatch.setattr(sys, "stdout", zh)
    assert bp.harden_streams() == []
    bp.emit("组装绿色包 → 校验")
    zh.flush()
    zh.buffer.seek(0)
    assert "组装" in zh.buffer.read().decode("cp936")


def test_emit_falls_back_instead_of_losing_the_line(monkeypatch):
    """换不动流的时候宁可输出 \\uXXXX 转义, 也不能把这一行整个丢掉。"""
    odd = _NoReconfigure()
    monkeypatch.setattr(sys, "stdout", odd)
    assert bp.harden_streams() == []              # 没有 reconfigure, 兜不住
    bp.emit("成品含用户数据/登录凭证, 中止")        # 不许抛
    assert "\\u6210\\u54c1" in odd.value()          # 转义出去, 行还在


def test_whole_build_runs_under_cp1252(tmp_path):
    """真跑一遍 CI 第一步: 英文区域 + 管道, 修复前退出码是 1(traceback 而不是打包结论)。"""
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
    r = subprocess.run([sys.executable, SCRIPT, "--out", str(tmp_path / "pkg")],
                       cwd=REPO, env=env, capture_output=True)
    report = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
    assert r.returncode == 0, report
    assert "UnicodeEncodeError" not in report
    assert "组装全量版" in report and "白名单检查通过" in report
    assert os.path.isfile(os.path.join(str(tmp_path / "pkg"), "run-portable.vbs"))


# --------------------------------------------------------------------------- 两个版本

def test_core_flavor_refuses_the_full_build_arguments(out):
    """CI 里两版只差三个参数, 抄错太容易 —— 出一个"名叫核心版、里面带解释器"的包最坏。"""
    with pytest.raises(SystemExit) as e:
        bp.build(out, site_dir=REPO, flavor="core")
    assert "核心版" in str(e.value) and "--site-dir" in str(e.value)
    with pytest.raises(SystemExit):
        bp.build(out, python_dir=REPO, flavor="core")
    with pytest.raises(SystemExit):
        bp.build(out, browsers_dir=REPO, flavor="core")


def test_core_flavor_ships_only_code_and_its_own_launchers(out):
    bp.build(out, flavor="core", quiet=True)
    top = sorted(os.listdir(out))
    assert top == ["README-核心版.txt", "app", "run-core.bat", "run-core.vbs"], top
    app = os.path.join(out, "app")
    assert os.path.isfile(os.path.join(app, "core", "main_gui.py"))
    assert os.path.isfile(os.path.join(app, "core", "deps.py")), "启动体检要跟着程序走"
    for junk in ("site-packages", "runtime", "python"):
        assert not os.path.exists(os.path.join(app, junk) if junk != "python"
                                 else os.path.join(out, junk)), junk
    assert bp.scan_forbidden(out) == []
    assert bp.check_flavor(out, "core") == []


def test_full_flavor_is_not_confused_with_core(out):
    bp.build(out, quiet=True)
    assert bp.check_flavor(out, "full") == []
    # 拿全量版的成品去核"核心版"结构 → 必须报"包里混进了别的地方的入口"
    problems = bp.check_flavor(out, "core")
    assert any("run-portable" in p for p in problems), problems


def test_check_flavor_catches_a_dirty_core_package(out):
    """手工造两种翻车: 核心版里躺着依赖/内核, 或启动器拿错版本。"""
    bp.build(out, flavor="core", quiet=True)
    os.makedirs(os.path.join(out, "app", "site-packages", "playwright"))
    os.makedirs(os.path.join(out, "app", "runtime", "ms-playwright", "chromium-1234"))
    got = bp.check_flavor(out, "core")
    assert any("site-packages" in p for p in got) and any("runtime" in p for p in got)
    shutil.copy2(os.path.join(REPO, "packaging", "run-portable.vbs"),
                 os.path.join(out, "run-portable.vbs"))
    assert any("入口" in p for p in bp.check_flavor(out, "core"))


def test_unknown_flavor_fails_loudly(out):
    with pytest.raises(SystemExit):
        bp.build(out, flavor="core2")
