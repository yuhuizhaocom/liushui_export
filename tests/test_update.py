"""检查更新与就地更新: 三道闸门、失败回滚, 以及"查不到也不许吵业务"。

全程离线: 网络用注入的假 opener, 包由测试自己造(zip 里摆了什么一目了然)。
"""
import os
import zipfile
from types import SimpleNamespace

import pytest

import core.config as cfg
import core.update as up
import core.version as ver
from core.main_gui import LiushuiApp


def make_zip(path, extra=None, deps_version="1.62.0", no_probe=False,
             backslash=False, top=None, deps_text=None):
    """造一个核心包: app/core/browser.py(探针) + app/core/deps.py + extra。"""
    def fix(name):
        return name.replace("/", "\\") if backslash else name

    with zipfile.ZipFile(str(path), "w") as z:
        if not no_probe:
            z.writestr(fix("app/core/browser.py"), "新浏览器模块".encode())
        z.writestr(fix("app/core/deps.py"),
                   deps_text if deps_text is not None
                   else ('REQUIRED_PLAYWRIGHT_VERSION = "%s"\n' % deps_version))
        for rel, data in (extra or {}).items():
            z.writestr(fix("app/" + rel), data)
        for rel, data in (top or {}).items():
            z.writestr(rel, data)


def release(asset="liushui-export-portable-win64-core.zip", size=0, tag="vportable_0.9.9"):
    return up.Release(tag=tag, download_url="https://example/" + asset,
                      asset=asset, size=size, page_url="https://example/release")


class _Resp:
    """假的 HTTP 响应: 能当上下文管理器, 也支持分块 read(查询与下载都走它)。"""

    def __init__(self, body):
        self._rest = body

    def read(self, n=-1):
        out, self._rest = self._rest[:n], self._rest[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def inst(tmp_path, monkeypatch):
    """一份"已装好的程序目录 + 独立工作空间", 覆盖只发生在它里面。"""
    app = tmp_path / "app"
    (app / "core").mkdir(parents=True)
    (app / "core" / "browser.py").write_bytes("旧浏览器模块".encode())
    (app / "core" / "main_gui.py").write_bytes("旧界面".encode())
    (app / "platforms").mkdir()
    (app / "platforms" / "我自己加的平台.py").write_bytes("用户自己写的脚本".encode())
    (app / "core" / "__pycache__").mkdir()
    (app / "core" / "__pycache__" / "browser.cpython-314.pyc").write_bytes("旧字节码".encode())
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(cfg, "PROGRAM_DIR", str(app))
    monkeypatch.setattr(cfg, "DATA_ROOT", str(ws))
    monkeypatch.setattr(cfg, "UPDATE_DIR", str(ws / "updates"))
    monkeypatch.setattr(cfg, "DOWNLOAD_DIR", str(ws / "downloads"))
    monkeypatch.setattr(up.deps, "installed_version", lambda *a, **k: "1.62.0")
    return SimpleNamespace(app=app, ws=ws, tmp=tmp_path)


# ===== 远端发布信息的解析 =====

def test_parse_release_picks_the_core_asset():
    payload = {"tag_name": "vportable_0.9.9", "html_url": "https://example/release",
               "assets": [{"name": "liushui-export-portable-win64-full.zip",
                           "size": 300000000, "browser_download_url": "https://example/full.zip"},
                          {"name": "liushui-export-portable-win64-core.zip",
                           "size": 4000000, "browser_download_url": "https://example/core.zip"}]}
    rel, why = up.parse_release(payload)
    assert why == "" and rel.tag == "vportable_0.9.9"
    # 认错成全量版就把"更新"变成"重下整个包", 所以只能挑 -core 那一个
    assert rel.asset.endswith("-core.zip") and rel.size == 4000000


def test_parse_release_refuses_a_release_without_a_core_package():
    rel, why = up.parse_release({"tag_name": "v1", "assets": [{"name": "a.zip", "size": 1}]})
    assert rel is None and "核心包" in why and "a.zip" in why
    assert up.parse_release("不是对象")[1]
    assert up.parse_release({"tag_name": "v1"})[1]        # 连 assets 都没有


@pytest.mark.parametrize("out", [
    lambda req, timeout: (_ for _ in ()).throw(OSError("getaddrinfo failed")),
    lambda req, timeout: (_ for _ in ()).throw(up.urllib.error.HTTPError(
        "https://x", 403, "rate limit", {}, None)),
    lambda req, timeout: _Resp("不是 JSON".encode()),
], ids=["连不上", "被限流", "回答读不出"])
def test_network_trouble_becomes_a_sentence_not_an_exception(out):
    rel, why = up.fetch_latest(opener=out)
    assert rel is None and why


def test_is_newer_compares_against_the_installed_version():
    assert up.is_newer(release(tag="vportable_0.9.9"), local="0.0.6") is True
    assert up.is_newer(release(tag="vportable_0.0.6"), local="0.0.6") is False
    # 脏 tag / 没这个 Release 一律当"没新版", 不许拿读不懂的数据让用户覆盖自己的程序
    assert up.is_newer(release(tag="不是版本"), local="0.0.6") is False
    assert up.is_newer(None, local="0.0.6") is False
    assert ver.is_newer("vportable_0.0.10", "vportable_0.0.9") is True


# ===== 包的结构校验 =====

def test_inspect_accepts_a_real_core_package(tmp_path):
    p = tmp_path / "core.zip"
    make_zip(p, extra={"platforms/youzan/export.py": b"x"},
             top={"run-core.vbs": b"vbs", "README-核心版.txt": b"readme"})
    members, why = up.inspect_package(str(p))
    assert why == ""
    assert set(members) == {"core/browser.py", "core/deps.py", "platforms/youzan/export.py"}
    # 顶层的启动器/README 不在就地更新的范围里
    assert "run-core.vbs" not in members and "README-核心版.txt" not in members


def test_inspect_accepts_backslash_separated_entries(tmp_path):
    """Compress-Archive 的分隔符口径不只一种, 认不出来就等于永远更新不了。"""
    p = tmp_path / "core.zip"
    make_zip(p, extra={"platforms/x.py": b"y"}, backslash=True)
    members, why = up.inspect_package(str(p))
    assert why == "" and "platforms/x.py" in members


@pytest.mark.parametrize("build,expect", [
    (dict(no_probe=True), "结构对不上"),
    (dict(extra={"site-packages/playwright/x.py": b"y"}), "site-packages"),
    (dict(extra={"runtime/ms-playwright/chromium-1234/chrome.exe": b"y"}), "runtime"),
    (dict(top={"python/python.exe": b"y"}), "python"),
], ids=["缺探针", "混进依赖", "混进内核", "混进解释器"])
def test_inspect_rejects_the_wrong_package(tmp_path, build, expect):
    p = tmp_path / "bad.zip"
    make_zip(p, **build)
    members, why = up.inspect_package(str(p))
    assert members is None and expect in why


def test_inspect_rejects_a_file_that_is_not_a_zip(tmp_path):
    p = tmp_path / "not.zip"
    p.write_bytes("<html>登录页</html>".encode())
    members, why = up.inspect_package(str(p))
    assert members is None and "zip" in why


# ===== 覆盖与回滚 =====


def test_a_member_escaping_the_program_dir_rejects_the_whole_package(inst, tmp_path):
    """Zip Slip: `app/../../…` 这种条目能把文件写到程序目录之外(启动文件夹之类)。"""
    p = tmp_path / "slip.zip"
    make_zip(p, extra={"../../evil.py": b"x"})
    members, why = up.inspect_package(str(p))
    assert members is None and "越过程序目录" in why
    ok, msg = up.apply_update(str(p), program_dir=str(inst.app),
                              backup_root=str(tmp_path / "bk"))
    assert not ok and "越过程序目录" in msg
    # 拒了就是一个字节都不落地, 不是"跳过那一条、其余照覆盖"
    assert (inst.app / "core/browser.py").read_bytes() == "旧浏览器模块".encode()

def test_apply_replaces_program_files_and_touches_nothing_else(inst, tmp_path):
    p = tmp_path / "core.zip"
    make_zip(p, extra={"platforms/youzan/export.py": "新脚本".encode()},
             top={"run-core.vbs": b"vbs"})
    ok, msg = up.apply_update(str(p), backup_root=str(tmp_path / "bk"))
    assert ok, msg
    assert (inst.app / "core/browser.py").read_bytes() == "新浏览器模块".encode()
    assert (inst.app / "platforms/youzan/export.py").read_bytes() == "新脚本".encode()
    # 用户自己加的平台脚本不能被更新删掉, 顶层启动器不能被写进程序目录
    assert (inst.app / "platforms/我自己加的平台.py").exists()
    assert not (inst.app / "run-core.vbs").exists()
    # 旧字节码清掉, 否则换完代码还在跑旧的那份
    assert not (inst.app / "core/__pycache__").exists()
    assert "重启" in msg
    # 被替换的旧文件在备份里找得到
    assert _backup_of(tmp_path / "bk", "core/browser.py") == "旧浏览器模块".encode()


def _backup_of(bk_root, rel):
    """backup/<时刻>/<rel>: 时刻目录是当场生成的, 按文件名在备份树里把它捞出来。"""
    hits = [os.path.join(dirpath, f)
            for dirpath, _d, files in os.walk(str(bk_root)) for f in files
            if f == os.path.basename(rel)]
    assert hits, "备份里没有 %s" % rel
    with open(hits[0], "rb") as fh:
        return fh.read()


def test_apply_rolls_back_everything_when_a_write_fails(inst, tmp_path, monkeypatch):
    """第 2 个文件写不下去(被占用/只读)时, 第 1 个必须复原, 新文件必须收走。"""
    p = tmp_path / "core.zip"
    make_zip(p, extra={"core/newcomer.py": "新版本才有的文件".encode()})
    real_replace = os.replace
    calls = []

    def boom(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("另一个程序正在使用此文件")
        return real_replace(src, dst)

    monkeypatch.setattr(up.os, "replace", boom)
    ok, msg = up.apply_update(str(p), backup_root=str(tmp_path / "bk"))
    assert not ok and "退回" in msg
    assert (inst.app / "core/browser.py").read_bytes() == "旧浏览器模块".encode()
    assert not (inst.app / "core/newcomer.py").exists()
    # 半截的暂存文件不许留在程序目录里(开发时程序目录就是仓库根)
    assert not [f for f in os.listdir(str(inst.app / "core")) if ".tmp-" in f]


# ===== 三道闸门 =====

def test_full_flavor_is_blocked_when_playwright_would_mismatch(inst, tmp_path):
    (inst.app / "site-packages").mkdir()          # 这就是"全量版"的样子
    p = tmp_path / "core.zip"
    make_zip(p, deps_version="1.63.0")
    ok, why = up.dependency_gate(str(p))
    assert not ok and "全量版" in why


def test_core_flavor_may_update_even_if_the_dependency_differs(inst, tmp_path):
    """核心版不自带依赖, 换完代码后启动体检自会引导安装, 这里拦它没道理。"""
    p = tmp_path / "core.zip"
    make_zip(p, deps_version="1.63.0")
    assert up.dependency_gate(str(p)) == (True, "")


def test_a_package_we_cannot_read_is_not_applied(inst, tmp_path):
    p = tmp_path / "core.zip"
    make_zip(p, deps_text="# 这行被人删了\n")
    ok, why = up.dependency_gate(str(p))
    assert not ok and "读不出" in why


def test_readonly_program_dir_is_reported(inst, monkeypatch):
    monkeypatch.setattr(cfg, "_probe_writable", lambda path: (False, "拒绝访问"))
    ok, why = up.program_writable()
    assert not ok and "拒绝访问" in why


def test_flavor_detection(inst):
    assert up.flavor() == "core"
    (inst.app / "runtime").mkdir()
    assert up.flavor() == "full"


# ===== 下载 =====

def test_download_lands_in_the_updates_dir(inst, tmp_path):
    body = b"zip-bytes"
    path, why = up.download(release(size=len(body)),
                            dest_dir=str(tmp_path), opener=lambda req, timeout: _Resp(body))
    assert why == "" and open(path, "rb").read() == body


def test_truncated_download_is_thrown_away(inst, tmp_path):
    folder = tmp_path / "dl"
    path, why = up.download(release(size=9999), dest_dir=str(folder),
                            opener=lambda req, timeout: _Resp("短".encode()))
    assert path is None and "对不上" in why
    assert os.listdir(str(folder)) == []        # 半成品不许留在原地等人误用


# ===== 路径与忽略规则必须同步 =====

def test_update_dir_is_not_inside_the_download_tree(inst):
    """冒进账单树就会被兜底扫描认领成某家商户的对账单, 所以这条要钉住。"""
    d = up.update_dir()
    assert not os.path.normpath(d).startswith(
        os.path.normpath(cfg.DOWNLOAD_DIR) + os.sep), d
    assert os.path.basename(d) == os.path.basename(cfg.UPDATE_DIR)


def test_updates_dir_is_gitignored():
    """开发时工作空间就是仓库根: 下好的包没被忽略就会冒进 git status(甚至被人提交)。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".gitignore"), encoding="utf-8") as f:
        rules = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    name = "/" + os.path.basename(cfg.UPDATE_DIR) + "/"
    assert name in rules, ".gitignore 里没有 %s, 与 config.UPDATE_DIR 不同步" % name


# ===== 界面侧: 这个组件永远不许传染业务 =====

class _App:
    """只带被测方法要用的三样东西: 日志、投递弹窗、被绑进来的那个方法。

    _ui 只把回调收起来不执行 —— 执行就要起真 Tk 窗口, 而这里要验的恰恰是
    "子线程只投递、不自己弹窗"。
    """

    def __init__(self):
        self.logs = []
        self.posted = []

    def _append_log(self, line):
        self.logs.append(line)

    def _ui(self, fn):
        self.posted.append(fn)


_App._update_check_worker = LiushuiApp._update_check_worker


@pytest.mark.parametrize("manual", [False, True], ids=["启动时", "手动点"])
def test_a_broken_updater_never_reaches_the_business_flow(inst, monkeypatch, manual):
    def boom(*a, **k):
        raise RuntimeError("这个模块自己炸了")
    monkeypatch.setattr(up, "fetch_latest", boom)
    app = _App()
    app._update_check_worker(manual)             # 不抛 = 导出/启动都不会被它带崩
    assert any("不影响使用" in line for line in app.logs)
    if manual:
        # 弹窗只能在主线程做, 子线程只负责投递; 这里断言它被投递而不是被就地执行
        assert len(app.posted) == 1


def test_startup_check_stays_silent_about_dialogs(inst, monkeypatch):
    monkeypatch.setattr(up, "fetch_latest",
                        lambda *a, **k: (release(tag="vportable_9.9.9"), ""))
    app = _App()
    app._update_check_worker(False)
    assert app.posted == []                       # 启动时一个字都不弹
    assert any("发现新版本" in line for line in app.logs)
    assert any("检查更新" in line for line in app.logs)
