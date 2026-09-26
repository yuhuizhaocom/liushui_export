"""
日志模块 - 同时输出到GUI和文件(项目唯一日志通道)

⚠ 目录**不能保证可写**: 绿色版可能放在只读介质/U 盘/受限目录下, 而本模块是被 import 的
最早几个之一 —— 在导入期抛错的话, 用户看到的就是"双击没反应"。所以这里的原则是
**建不出来就退回临时目录, 一句话都不吵, 但把经过记进 LOGGER_NOTES 让界面去说**。
"""
import os
import json
import glob
import logging
import tempfile
import threading
from datetime import datetime

from . import config as _config

# 日志目录: 由 config 按工作空间算出(config.LOG_DIR), 建不出来时退到临时目录
LOG_DIR = _config.LOG_DIR
STATS_FILE = os.path.join(LOG_DIR, "stats.jsonl")
LOGGER_NOTES = []            # 界面启动时逐行写进日志区


def _ensure_log_dir():
    """依次试: 工作空间的 logs/ → 临时目录。返回真正可用的目录(都不行则空串)。"""
    global LOG_DIR, STATS_FILE
    candidates = [(LOG_DIR, "工作空间")]
    try:
        tmp = os.path.join(tempfile.gettempdir(), "liushui_export", "logs")
    except Exception:
        tmp = ""
    if tmp:
        candidates.append((tmp, "临时目录"))
    for cand, label in candidates:
        try:
            os.makedirs(cand, exist_ok=True)
        except Exception as e:
            LOGGER_NOTES.append(f"日志目录建不出来({label}: {cand}): {str(e)[:80]}")
            continue
        if cand != LOG_DIR:
            LOGGER_NOTES.append(f"日志改写到{label}: {cand}(原定位 {LOG_DIR} 不能写)")
        LOG_DIR = cand
        STATS_FILE = os.path.join(cand, "stats.jsonl")
        _config.LOG_DIR = cand            # 让稍后才 import 的模块取到同一个值
        return cand
    LOGGER_NOTES.append("日志文件通道不可用, 本次运行只输出到界面与控制台")
    return ""


_STATS_LOCK = threading.Lock()
# 为什么要有这把锁: stats.jsonl 是"每次导出一行"的追加写, 而写它的线程不止一个
# (任务线程收尾、导出前预检那一路也落统计)。实测同进程 10 线程各写 100 行: 不加锁
# 只落 964 行、0 个坏行 —— **整条静默消失**, 看板分母凭空变小, 查都没法查。
# ⚠ 这把锁挡不住"开了两份程序"(跨进程), 那一层由 core/instance.py 的单实例守卫去问。


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
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _STATS_LOCK:
            with open(STATS_FILE, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def _read_tail_lines(path, max_lines, block=65536):
    """从文件末尾反向按块读最后 max_lines 行(返回文件顺序)。

    stats.jsonl 是每次导出追加一行、永不清理的; 整文件 readlines 会让"打开看板"
    随使用时长越来越慢。汇总表仍走全量(它要统计全部历史), 只有明细用这个。
    """
    block = 65536
    out = []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        leftover = b""
        while pos > 0 and len(out) < max_lines:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            parts = (f.read(step) + leftover).split(b"\n")
            leftover = parts[0]          # 块首这行可能被切断, 留到下一轮再拼
            out = [_decode(p) for p in parts[1:] if p.strip()] + out
            if len(out) > max_lines:
                out = out[-max_lines:]   # 丢最靠前的(离文件末尾最远)
        if leftover.strip():
            out.insert(0, _decode(leftover))
    return out[-max_lines:]


def _decode(raw):
    """一行字节 → 去掉行尾符的字符串(与 readlines() 的结果保持同一种类型)。"""
    return raw.decode("utf-8", "replace").strip()


def load_stats(limit=None):
    """读取 stats.jsonl 返回记录列表(按时间倒序,默认全部)"""
    records = []
    if not os.path.isfile(STATS_FILE):
        return records
    try:
        if limit:
            # 只要最近 limit 条: 尾部反向读, 不把整个文件吃进内存
            lines = _read_tail_lines(STATS_FILE, limit)
            lines.reverse()
        else:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            lines.reverse()   # 倒序(最新在前)
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
_ensure_log_dir()
log_file = os.path.join(LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log") if LOG_DIR else ""

logger = logging.getLogger("liushui")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    try:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)
    except Exception as e:
        # 连临时目录都写不了(磁盘满/权限): 文件通道这次就放弃,
        # 控制台 + 界面回调这两条通道仍然照常, 绝不能把导入带崩
        LOGGER_NOTES.append(f"日志文件打不开: {str(e)[:80]}")


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