"""清理只碰"能再生/有原件"的东西: 老日志和汇总副本目录。

误删的代价远大于省下的磁盘, 所以规则要钉死: 账单原件、`待确认/`(无人认领的账单)、
`stats.jsonl`(稳定性看板唯一历史)、平台目录, 无论如何都不能动。保留天数设成 0 就是
完全关掉的。
"""
import os

import pytest

import core.config as cfg
import core.main_gui as mg
from core.cleanup import (KEEP_FOREVER, describe, prune_logs, prune_summary_dirs,
                          run_cleanup)
from core.main_gui import LiushuiApp

NOW = 1_800_000_000.0        # 固定"现在", 免得测试跟着钟表走


def _touch(path, age_days, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    t = NOW - age_days * 86400
    os.utime(path, (t, t))
    return path


def _touch_summary(dl, name, age_days):
    """汇总目录: 判定用的是目录自己的 mtime(Windows 上写文件里的内容不更新它)。"""
    d = os.path.join(dl, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "流水.csv"), "w", encoding="utf-8") as f:
        f.write("x")
    t = NOW - age_days * 86400
    os.utime(d, (t, t))
    return d


@pytest.fixture()
def tree(tmp_path):
    log_dir = str(tmp_path / "logs")
    dl = str(tmp_path / "downloads")
    _touch(os.path.join(log_dir, "run_20250101.log"), 400)
    _touch(os.path.join(log_dir, "run_20260101.log"), 1)
    _touch(os.path.join(log_dir, "crash_20250101_101010_123456.txt"), 400)
    _touch(os.path.join(log_dir, KEEP_FOREVER[0]), 400, '{"result":"success"}\n')
    _touch(os.path.join(log_dir, "notes.txt"), 400)          # 不是本工具产生的
    _touch_summary(dl, "2025-01-01_2025-01-02_20250103_090000", 400)
    _touch_summary(dl, "2026-01-01_2026-01-02_20260103_090000", 1)
    _touch(os.path.join(dl, "有赞", "旗舰店A", "2025-01-01_2025-01-02", "流水.xlsx"), 400)
    _touch(os.path.join(dl, "待确认", "3f1a2b3c"), 400)
    return log_dir, dl


def test_only_old_run_and_crash_files_go(tree):
    log_dir, _ = tree
    removed = prune_logs(log_dir, keep_days=180, now_ts=NOW)
    assert [os.path.basename(p) for p in removed] == ["crash_20250101_101010_123456.txt",
                                                      "run_20250101.log"]
    left = set(os.listdir(log_dir))
    assert left == {"run_20260101.log", "stats.jsonl", "notes.txt"}, "统计与别人的文件不能动"


def test_per_run_log_names_are_recognised(tree):
    """回归: logger 早就改成"每次启动一个文件"(`run_YYYYMMDD_HHMMSS.log`), 而白名单
    只认按天那一种 —— 于是界面上的"日志保留(天)"改了多少天都没东西被删, logs 只增不减。
    """
    log_dir, _ = tree
    _touch(os.path.join(log_dir, "run_20250101_090000.log"), 400)
    _touch(os.path.join(log_dir, "run_20260101_090000.log"), 1)
    removed = {os.path.basename(p) for p in prune_logs(log_dir, keep_days=180, now_ts=NOW)}
    assert removed == {"run_20250101.log", "run_20250101_090000.log",
                       "crash_20250101_101010_123456.txt"}
    assert os.path.isfile(os.path.join(log_dir, "run_20260101_090000.log"))


def test_summary_dirs_gone_but_archives_and_pending_stay(tree):
    _, dl = tree
    removed = prune_summary_dirs(dl, keep_days=180, now_ts=NOW)
    assert [os.path.basename(p) for p in removed] == [
        "2025-01-01_2025-01-02_20250103_090000"]
    assert os.path.isfile(os.path.join(dl, "有赞", "旗舰店A", "2025-01-01_2025-01-02",
                                       "流水.xlsx")), "账单原件必须还在"
    assert os.path.isdir(os.path.join(dl, "待确认")), "无人认领的账单不是垃圾"
    assert os.path.isdir(os.path.join(dl, "2026-01-01_2026-01-02_20260103_090000"))


def test_keep_days_zero_means_touch_nothing(tree):
    log_dir, dl = tree
    assert prune_logs(log_dir, 0, now_ts=NOW) == []
    assert prune_summary_dirs(dl, 0, now_ts=NOW) == []
    assert os.path.isfile(os.path.join(log_dir, "run_20250101.log"))


def test_dry_run_lists_without_deleting(tree):
    log_dir, dl = tree
    report = run_cleanup(log_dir, dl, keep_days=180, dry_run=True)
    assert len(report["logs"]) == 2 and len(report["summary_dirs"]) == 1
    assert os.path.isfile(os.path.join(log_dir, "run_20250101.log"))
    assert os.path.isdir(os.path.join(dl, "2025-01-01_2025-01-02_20250103_090000"))


def test_missing_directories_are_not_an_error(tmp_path):
    missing = str(tmp_path / "没有这个目录")
    assert run_cleanup(missing, missing, keep_days=180) == {"logs": [], "summary_dirs": []}


def test_locked_log_is_skipped_quietly(tree, monkeypatch):
    """日志正被本进程开着写时删不掉, 只能跳过, 不能把清理失败传给启动流程。"""
    log_dir, _ = tree
    def boom(path):
        raise OSError("被占用")
    monkeypatch.setattr(os, "remove", boom)
    assert prune_logs(log_dir, keep_days=180, now_ts=NOW) == []
    assert os.path.isfile(os.path.join(log_dir, "run_20250101.log"))


def test_describe_wording_reports_both_cases(tree):
    log_dir, dl = tree
    report = run_cleanup(log_dir, dl, keep_days=180)
    text = describe(report, 180)
    assert "2 个" in text and "1 个" in text and "原件" in text
    assert describe(report, 0) == "自动清理已关闭(保留天数设为 0)"
    assert "没有超过 180 天" in describe({"logs": [], "summary_dirs": []}, 180)


def test_recent_file_mtime_is_not_matched_by_the_log_pattern(tree):
    """名字必须像本工具写的(run_YYYYMMDD.log / crash_..._微秒.txt), 别的名字不删。"""
    log_dir, _ = tree
    _touch(os.path.join(log_dir, "run_2025-01-01.log"), 400)      # 名字不合规矩
    _touch(os.path.join(log_dir, "crash_20250101_101010.txt"), 400)  # 少了微秒段
    removed = prune_logs(log_dir, keep_days=180, now_ts=NOW)
    names = {os.path.basename(p) for p in removed}
    assert names == {"run_20250101.log", "crash_20250101_101010_123456.txt"}


# ===== 界面接线: 清理失败只写日志, 绝不能影响启动 =====

DEFAULT_KEEP = int(cfg.DEFAULT_SETTINGS["cleanup_keep_days"])


class _Box:
    def __init__(self, text):
        self._t = text

    def get(self):
        return self._t


class _App:
    _run_cleanup_once = LiushuiApp._run_cleanup_once
    _on_cleanup_setting = LiushuiApp._on_cleanup_setting

    def __init__(self, text="180"):
        self.logs = []
        self.settings = dict(cfg.DEFAULT_SETTINGS)
        self.cleanup_days = _Box(text)

    def _append_log(self, line):
        self.logs.append(line)


def test_startup_cleanup_reads_the_setting_and_reports(monkeypatch):
    seen = {}

    def fake_run(log_dir, downloads_dir, keep_days):
        seen.update(log_dir=log_dir, downloads=downloads_dir, keep=keep_days)
        return {"logs": ["a.log"], "summary_dirs": ["b"]}

    monkeypatch.setattr(mg, "run_cleanup", fake_run)
    monkeypatch.setattr(mg, "load_settings", lambda: {"cleanup_keep_days": 30})
    app = _App()
    app._run_cleanup_once()
    assert seen == {"log_dir": mg.LOG_DIR, "downloads": mg.DOWNLOAD_DIR, "keep": 30}
    assert len(app.logs) == 1 and app.logs[0].startswith("[清理] 自动清理(保留 30 天)")


@pytest.mark.parametrize("junk", ["abc", None, {}])
def test_junk_setting_falls_back_to_the_default(monkeypatch, junk):
    calls = []
    monkeypatch.setattr(mg, "run_cleanup",
                        lambda a, b, keep: calls.append(keep) or {"logs": [], "summary_dirs": []})
    monkeypatch.setattr(mg, "load_settings", lambda: {"cleanup_keep_days": junk})
    _App()._run_cleanup_once()
    assert calls == [int(cfg.DEFAULT_SETTINGS["cleanup_keep_days"])]


def test_cleanup_crash_is_only_a_log_line(monkeypatch):
    def boom(*a, **k):
        raise OSError("磁盘在另一台机器上")
    monkeypatch.setattr(mg, "run_cleanup", boom)
    app = _App()
    app._run_cleanup_once()                 # 不向外抛: 后台线程抛错也只剩一个 crash 文件
    assert any("没跑成" in line for line in app.logs), app.logs


def test_keep_days_box_writes_only_its_own_key(monkeypatch):
    written = []
    monkeypatch.setattr(mg, "save_settings", lambda d: written.append(dict(d)))
    app = _App("60")
    app._on_cleanup_setting()
    assert written == [{"cleanup_keep_days": 60}]
    assert app.settings["cleanup_keep_days"] == 60
    assert "保留 60 天" in app.logs[-1]


@pytest.mark.parametrize("text,expected", [("0", 0), ("-5", 0), ("99999", 3650),
                                           (" ", DEFAULT_KEEP), ("abc", DEFAULT_KEEP)])
def test_box_text_is_clamped_not_crashed(monkeypatch, text, expected):
    written = []
    monkeypatch.setattr(mg, "save_settings", lambda d: written.append(dict(d)))
    _App(text)._on_cleanup_setting()
    assert written == [{"cleanup_keep_days": expected}]


def test_turning_cleanup_off_is_said_out_loud(monkeypatch):
    written = []
    monkeypatch.setattr(mg, "save_settings", lambda d: written.append(dict(d)))
    app = _App("0")
    app._on_cleanup_setting()
    assert "已关闭自动清理" in app.logs[-1]
