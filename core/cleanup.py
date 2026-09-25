"""按保留期清理运行日志与"汇总副本"目录。

这工具是给业务用户长期跑的: 每次启动一个 `logs/run_日期.log`、每次导出一个
`downloads/起_止_时间戳/` 汇总文件夹, 谁都不清 → 一年后上千个文件夹, 历史日志窗口和
资源管理器都跟着变慢。

只清两类**可再生/有原件**的东西:
- `logs/run_*.log`、`logs/crash_*.txt` —— 日志本来就只是过程记录;
- `downloads/起_止_时间戳/` —— 它只是把 `downloads/平台/商户/日期/` 里的原件**复制**
  一份方便取用, 原件不动, 删掉副本不丢数据。

绝不碰: `downloads/<平台>/...` 归档、`downloads/待确认/`(无人认领的账单)、
`logs/stats.jsonl`(稳定性看板的唯一历史)、各类 json 配置。
"""
import os
import re
import shutil
from datetime import datetime

# 汇总目录名: 任务日期区间 + 落地时间戳, 如 2026-09-01_2026-09-02_20260924_181000
SUMMARY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}_\d{8}_\d{6}$")
# 运行日志历史上用过两种名字: 按天(`run_20260924.log`)与按启动次数
# (`run_20260924_211344.log`, 现在 logger 写的是这一种)。只列前一种会让清理静默失灵
# ——界面上的"日志保留(天)"改了也没东西被删, 一年下来 logs 里上千个文件。
LOG_FILE_RES = (re.compile(r"^run_\d{8}\.log$"),
                re.compile(r"^run_\d{8}_\d{6}\.log$"),
                re.compile(r"^crash_\d{8}_\d{6}_\d{6}\.txt$"))

# stats.jsonl 是看板唯一的历史来源, 再旧也不能删
KEEP_FOREVER = ("stats.jsonl",)


def _cutoff(now, keep_days):
    return now - keep_days * 86400


def _old_enough(path, cutoff):
    try:
        return os.path.getmtime(path) < cutoff
    except OSError:
        return False            # _stat 失败(被占用/权限)时按"不动"处理


def prune_logs(log_dir, keep_days, now_ts=None, dry_run=False):
    """清理 keep_days 天之前的运行日志/崩溃文件, 返回被处理(或删除)的路径列表。

    keep_days <= 0 表示关闭清理, 一个文件都不动。
    """
    if keep_days <= 0 or not os.path.isdir(log_dir):
        return []
    cutoff = _cutoff(now_ts if now_ts is not None else datetime.now().timestamp(),
                     keep_days)
    removed = []
    try:
        names = sorted(os.listdir(log_dir))
    except OSError:
        return []
    for name in names:
        if name in KEEP_FOREVER:
            continue
        path = os.path.join(log_dir, name)
        if not os.path.isfile(path):
            continue
        if not any(r.match(name) for r in LOG_FILE_RES):
            continue
        if not _old_enough(path, cutoff):
            continue
        if dry_run:
            removed.append(path)
            continue
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass                # 正在被写的日志删不掉就算了, 下次再清
    return removed


def prune_summary_dirs(downloads_dir, keep_days, now_ts=None, dry_run=False):
    """删除 keep_days 天之前的汇总副本目录(原件仍在 `平台/商户/日期/` 里)。"""
    if keep_days <= 0 or not os.path.isdir(downloads_dir):
        return []
    cutoff = _cutoff(now_ts if now_ts is not None else datetime.now().timestamp(),
                     keep_days)
    removed = []
    try:
        names = sorted(os.listdir(downloads_dir))
    except OSError:
        return []
    for name in names:
        if not SUMMARY_DIR_RE.match(name):
            continue            # 平台目录/待确认/杂项一律不看
        path = os.path.join(downloads_dir, name)
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        if not _old_enough(path, cutoff):
            continue
        if dry_run:
            removed.append(path)
            continue
        try:
            shutil.rmtree(path)
            removed.append(path)
        except OSError:
            pass
    return removed


def run_cleanup(log_dir, downloads_dir, keep_days, dry_run=False):
    """一次清理, 返回 {"logs": [...], "summary_dirs": [...]} 供日志说明。"""
    return {"logs": prune_logs(log_dir, keep_days, dry_run=dry_run),
            "summary_dirs": prune_summary_dirs(downloads_dir, keep_days,
                                               dry_run=dry_run)}


def describe(report, keep_days):
    """把清理结果写成一句给人看的话(没清就明说是关着的)。"""
    if keep_days <= 0:
        return "自动清理已关闭(保留天数设为 0)"
    logs = len(report.get("logs") or [])
    dirs = len(report.get("summary_dirs") or [])
    if not logs and not dirs:
        return f"自动清理: 没有超过 {keep_days} 天的日志或汇总副本"
    return (f"自动清理(保留 {keep_days} 天): 删除运行/崩溃日志 {logs} 个, "
            f"过期汇总副本目录 {dirs} 个; 账单原件与 stats.jsonl 未动")
