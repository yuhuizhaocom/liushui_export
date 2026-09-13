"""
日志模块 - 同时输出到GUI和文件(项目唯一日志通道)
"""
import os
import json
import glob
import logging
from datetime import datetime

from .config import ROOT_DIR

# 日志目录(基于项目根的绝对路径,不依赖运行目录)
LOG_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 稳定性统计文件(每次导出追加一条 JSON,便于按平台/时间汇总成功率)
STATS_FILE = os.path.join(LOG_DIR, "stats.jsonl")


def record_stat(platform, merchant, start_date, end_date, result, duration_s=0, error=""):
    """记录一次导出结果到 stats.jsonl,供稳定性看板汇总。
    result: success/manual/failed"""
    try:
        rec = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "platform": platform,
            "merchant": merchant,
            "start_date": start_date,
            "end_date": end_date,
            "result": result,
            "duration_s": round(duration_s or 0, 1),
            "error": (error or "")[:200],
        }
        with open(STATS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_stats(limit=None):
    """读取 stats.jsonl 返回记录列表(按时间倒序,默认全部)"""
    records = []
    if not os.path.isfile(STATS_FILE):
        return records
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                pass
    except Exception:
        pass
    records.reverse()   # 倒序(最新在前)
    if limit:
        records = records[:limit]
    return records


def summarize_stats(records=None):
    """按平台汇总成功率统计,返回 [(platform, total, success, manual, failed, success_rate%), ...]
    按 total 倒序"""
    if records is None:
        records = load_stats()
    by_plat = {}
    for r in records:
        p = r.get("platform", "?")
        d = by_plat.setdefault(p, {"total": 0, "success": 0, "manual": 0, "failed": 0})
        d["total"] += 1
        if r.get("result") in d:
            d[r["result"]] += 1
    result = []
    for p, d in by_plat.items():
        rate = (d["success"] / d["total"] * 100) if d["total"] else 0
        result.append((p, d["total"], d["success"], d["manual"], d["failed"], round(rate, 1)))
    result.sort(key=lambda x: x[1], reverse=True)
    return result

# 每次程序启动新建一个日志文件(按运行次而非按天),便于查看单次运行历史
log_file = os.path.join(LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logger = logging.getLogger("liushui")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)


def list_history_logs():
    """列出历史日志文件(按修改时间倒序),返回 [(文件名, 完整路径, 修改时间字符串), ...]"""
    pattern = os.path.join(LOG_DIR, "run_*.log")
    files = glob.glob(pattern)
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    result = []
    for f in files:
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S")
        result.append((os.path.basename(f), f, mtime))
    return result


_PY_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
              "warning": logging.WARNING, "error": logging.ERROR}
_LEVEL_TAG = {"debug": "DEBUG", "warning": "WARN", "error": "ERROR"}


def log(message, level="info", callback=None):
    """统一日志入口: console + 文件 + GUI 回调。
    level: debug/info/warning/error; 界面展示行带 [LEVEL] 前缀(除 info)。"""
    ts = datetime.now().strftime("%H:%M:%S")
    tag = _LEVEL_TAG.get(level, "")
    line = f"[{ts}] [{tag}] {message}" if tag else f"[{ts}] {message}"
    # 无控制台环境(pythonw)下 sys.stdout 可能为 None,print 会抛错
    try:
        print(line)
    except Exception:
        pass
    logger.log(_PY_LEVELS.get(level, logging.INFO), message)
    if callback:
        try:
            callback(line)
        except Exception:
            pass
    return line