"""工作空间(产出空间)可指定: 路径解析、优先级、不可写时的兜底。

绿色版的分界线: **程序目录**可以只读(代码 + 平台脚本 + Playwright 内核), **工作空间**
必须可写(账单、浏览器数据/登录态、日志、三个 json、录制)。默认两者相同 = 就地模式,
老用户零迁移; 全新的一份包在第一次启动时该问用户"账单存哪儿"。
"""
import json
import os

import pytest

import core.browser as bm
import core.config as cfg
import core.config as workspace_cfg
import core.crashguard as crashguard
import core.logger as logger_module


# ===== 派生路径 =====

MAPPING = {"downloads": "DOWNLOAD_DIR", "browser_data": "BROWSER_DATA_DIR",
           "logs": "LOG_DIR", "recordings": "RECORDINGS_DIR",
           "settings.json": "SETTINGS_FILE",
           "scheduled_tasks.json": "SCHEDULED_TASKS_FILE",
           "selection_state.json": "SELECTION_FILE"}


@pytest.mark.parametrize("sub,const", sorted(MAPPING.items()))
def test_every_path_hangs_off_the_data_root(sub, const):
    """工作空间换到哪儿, 七个路径就都跟到哪儿 —— 一处算, 别处不许再自己拼。"""
    d = cfg._derive("D:/流水数据")
    assert os.path.normpath(d[const]) == os.path.normpath(os.path.join("D:/流水数据", sub))


def test_derived_names_cover_exactly_the_public_constants():
    assert set(cfg._derive("x")) == {"DATA_ROOT", "DOWNLOAD_DIR", "BROWSER_DATA_DIR",
                                     "LOG_DIR", "RECORDINGS_DIR", "SETTINGS_FILE",
                                     "SCHEDULED_TASKS_FILE", "SELECTION_FILE",
                                     "SUB_MERCHANTS_FILE"}


# ===== 解析优先级 =====

@pytest.fixture()
def fake_pkg(tmp_path, monkeypatch):
    """把"程序目录"搬到一个干净临时目录, 免得测试碰到真仓库。"""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    monkeypatch.setattr(cfg, "ROOT_DIR", str(pkg))
    monkeypatch.setattr(cfg, "PROGRAM_DIR", str(pkg))
    monkeypatch.setattr(cfg, "WORKSPACE_POINTER", str(pkg / "workspace.json"))
    monkeypatch.delenv(cfg.WORKSPACE_ENV, raising=False)
    monkeypatch.setattr(cfg, "_cli_data_root", lambda argv=None: "")
    return pkg


def test_fresh_package_asks_the_user(fake_pkg):
    assert cfg.resolve_data_root() == (str(fake_pkg), True)


def test_a_package_already_in_use_is_left_alone(fake_pkg):
    """只有"用户真动过"的痕迹才算用过: 存过设置/勾过商户/建过定时任务。"""
    (fake_pkg / "settings.json").write_text("{}", encoding="utf-8")
    assert cfg.resolve_data_root() == (str(fake_pkg), False)


def test_startup_artifacts_do_not_silent_the_first_run(fake_pkg, monkeypatch):
    """回归: logs/ 是 import logger 时建的, downloads/browser_data 是 BrowserManager
    构造时建的 —— 拿它们当"用过"的痕迹, 自检跑一次之后打包就永远不会再问用户选工作空间,
    于是账单和登录态被悄悄写回只读的程序目录。
    """
    for junk in ("logs", "downloads", "browser_data", "recordings"):
        (fake_pkg / junk).mkdir(exist_ok=True)
    assert cfg.resolve_data_root() == (str(fake_pkg), True)


def test_command_line_wins_over_everything(fake_pkg, tmp_path, monkeypatch):
    chosen = tmp_path / "cli"
    monkeypatch.setattr(cfg, "_cli_data_root", lambda argv=None: str(chosen))
    monkeypatch.setenv(cfg.WORKSPACE_ENV, str(tmp_path / "env"))
    (fake_pkg / "workspace.json").write_text(json.dumps({"data_root": str(tmp_path / "ptr")}),
                                             encoding="utf-8")
    root, pending = cfg.resolve_data_root()
    assert root == os.path.abspath(str(chosen)) and pending is False
    assert os.path.isdir(str(chosen)), "指定过的目录要当场建出来"


def test_env_then_pointer_then_default(fake_pkg, tmp_path, monkeypatch):
    monkeypatch.setenv(cfg.WORKSPACE_ENV, str(tmp_path / "env"))
    assert cfg.resolve_data_root()[0] == os.path.abspath(str(tmp_path / "env"))
    monkeypatch.delenv(cfg.WORKSPACE_ENV)
    (fake_pkg / "workspace.json").write_text(json.dumps({"data_root": str(tmp_path / "ptr")}),
                                             encoding="utf-8")
    assert cfg.resolve_data_root() == (os.path.abspath(str(tmp_path / "ptr")), False)


def test_broken_pointer_falls_back_and_says_why(fake_pkg, tmp_path):
    """书签指向的盘没了(U 盘拔了/换机): 退回程序目录并重新问, 但不能静默。"""
    (fake_pkg / "workspace.json").write_text(json.dumps({"data_root": "Z:/没有这个盘"}),
                                             encoding="utf-8")
    root, pending = cfg.resolve_data_root()
    assert root == str(fake_pkg) and pending is True
    assert len(cfg.RESOLVE_NOTES) == 1 and "书签" in cfg.RESOLVE_NOTES[0]


def test_unwritable_choice_is_reported(tmp_path):
    ok, why = cfg._probe_writable("Z:/绝对不存在的盘/子目录")
    assert ok is False and why
    good = tmp_path / "fine"
    assert cfg._probe_writable(str(good)) == (True, "")
    assert good.is_dir()


# ===== 选定/更换 =====

def test_apply_data_root_recomputes_and_remembers(fake_pkg, tmp_path, monkeypatch):
    ws = tmp_path / "工作空间"
    ok, msg = cfg.apply_data_root(str(ws))
    assert ok and os.path.abspath(msg) == os.path.abspath(str(ws))
    assert cfg.DATA_ROOT == os.path.abspath(str(ws))
    assert cfg.PENDING_PICK is False
    assert os.path.basename(cfg.SETTINGS_FILE) == "settings.json"
    assert os.path.normpath(cfg.LOG_DIR) == os.path.normpath(os.path.join(str(ws), "logs"))
    assert cfg.has_marker(str(ws)), "工作空间里要留标记, 搬家时能认出"
    assert cfg.read_pointer() == os.path.abspath(str(ws)), "程序目录里要留书签"


def test_apply_data_root_refuses_unwritable(fake_pkg, tmp_path):
    ok, why = cfg.apply_data_root("Z:/没有这个盘/ws")
    assert ok is False and "不能写入" in why
    assert cfg.read_pointer() == ""


def test_pointer_round_trip_survives_junk(fake_pkg, tmp_path):
    assert cfg.read_pointer() == ""
    cfg.write_pointer(str(tmp_path))
    assert cfg.read_pointer() == os.path.abspath(str(tmp_path))
    (fake_pkg / "workspace.json").write_text("{ 坏掉的 json", encoding="utf-8")
    assert cfg.read_pointer() == ""


def test_describe_mentions_same_dir_mode(fake_pkg):
    assert "工作空间" in cfg.describe_data_root()


# ===== Playwright 内核目录 =====

def test_browsers_path_priority(tmp_path):
    inside = tmp_path / "runtime" / "ms-playwright"
    inside.mkdir(parents=True)
    outside = tmp_path / "外部指定"
    outside.mkdir()
    legacy = tmp_path / "C盘老位置"
    legacy.mkdir()
    # 1) 外部已设的 env 最大
    assert bm.resolve_browsers_path(env={"PLAYWRIGHT_BROWSERS_PATH": str(outside)},
                                    program_dir=str(tmp_path), fallback=str(legacy)) == str(outside)
    # 2) 其次包内(绿色版)
    assert bm.resolve_browsers_path(env={}, program_dir=str(tmp_path),
                                    fallback=str(legacy)) == str(inside)
    # 3) 再次是开发机上那个 C:\pw_browsers
    import shutil
    shutil.rmtree(str(inside))
    assert bm.resolve_browsers_path(env={}, program_dir=str(tmp_path),
                                    fallback=str(legacy)) == str(legacy)
    # 4) 都没有 → 交回 Playwright 默认位置(它自己会去 %LOCALAPPDATA%\ms-playwright)
    assert bm.resolve_browsers_path(env={}, program_dir=str(tmp_path),
                                    fallback=str(tmp_path / "没有")) == ""
    # 指向不存在目录的 env 不算数
    assert bm.resolve_browsers_path(env={"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "无")},
                                    program_dir=str(tmp_path),
                                    fallback=str(legacy)) == str(legacy)


def test_browsers_path_lives_in_config_but_still_exports_from_browser():
    """搬家是为了让 deps 体检能在没装 playwright 的机器上用; browser 那份再导出不能悄悄没。

    CI 的冒烟脚本、build_portable 的自检、上面的测试都从 core.browser 取这个名字。
    """
    assert cfg.resolve_browsers_path is bm.resolve_browsers_path
    assert cfg.PW_BROWSERS_PATH == bm.PW_BROWSERS_PATH == r"C:\pw_browsers"
    # 默认按**程序目录**找包内内核, 不是按工作空间 —— 内核不跟着产出空间走
    assert cfg.resolve_browsers_path(env={}) == bm.resolve_browsers_path(env={})


def test_default_browsers_root_is_the_playwright_own_registry(tmp_path):
    """体检要说"内核没下"就得连 Playwright 的默认位置一起看。"""
    env = {"LOCALAPPDATA": str(tmp_path / "AppData" / "Local")}
    assert cfg.default_browsers_root(env=env) == os.path.join(env["LOCALAPPDATA"], "ms-playwright")
    # 没有 LOCALAPPDATA 时退回按家目录拼, 至少不返回空串
    assert cfg.default_browsers_root(env={}).endswith("ms-playwright")
    assert cfg.default_browsers_root(env={"LOCALAPPDATA": "  "}).endswith("ms-playwright")


# ===== 崩溃文件跟着工作空间走 =====

def test_crash_dir_follows_the_workspace(fake_pkg, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (fake_pkg / "workspace.json").write_text(json.dumps({"data_root": str(ws)}),
                                             encoding="utf-8")
    monkeypatch.setattr(crashguard, "POINTER_FILE", str(fake_pkg / "workspace.json"))
    monkeypatch.setattr(crashguard, "CRASH_DIR", str(fake_pkg / "logs"))
    dirs = crashguard._crash_dirs()
    assert dirs[0] == os.path.join(str(ws), "logs"), "优先写工作空间, 用户才找得到"
    assert os.path.join(str(fake_pkg), "logs") in dirs[1:]


def test_crash_dir_without_pointer_still_works(fake_pkg, monkeypatch):
    monkeypatch.setattr(crashguard, "POINTER_FILE", str(fake_pkg / "workspace.json"))
    monkeypatch.setattr(crashguard, "CRASH_DIR", str(fake_pkg / "logs"))
    assert crashguard._crash_dirs()[0] == os.path.join(str(fake_pkg), "logs")


# ===== 界面侧: 首启选择与工作空间说明 =====

import core.main_gui as mg
from core.main_gui import LiushuiApp


def test_first_run_choice(tmp_path):
    sug = str(tmp_path / "Documents/流水导出工作空间")
    assert mg.decide_workspace(True, "", sug) == ("use", os.path.abspath(sug))
    mine = str(tmp_path / "D")
    assert mg.decide_workspace(False, mine, sug) == ("use", os.path.abspath(mine))
    # 「否」之后又关掉选择框 → 取消, 不拿一个没确认的路径开工
    assert mg.decide_workspace(False, "", sug) == ("cancel", "")


def test_ensure_workspace_leaves_existing_install_alone(monkeypatch):
    """已经用过的拷贝(PENDING_PICK=False)绝不打扰: 首启对话框只该出现在全新的一份包里。"""
    monkeypatch.setattr(workspace_cfg, "PENDING_PICK", False)
    assert mg.ensure_workspace(object()) is True      # root 根本没被碰


def test_report_paths_says_workspace_and_notes():
    class _App:
        _report_paths = LiushuiApp._report_paths
        def __init__(self):
            self.logs = []
        def _append_log(self, line):
            self.logs.append(line)
    app = _App()
    app._report_paths()
    assert any("工作空间" in line for line in app.logs)
    for note in (workspace_cfg.RESOLVE_NOTES + logger_module.LOGGER_NOTES):
        assert any(note in line for line in app.logs)
