"""
日志模块 - 同时输出到GUI和文件(项目唯一日志通道)
"""
import os
import logging
from datetime import datetime

from .config import ROOT_DIR

# 日志目录(基于项目根的绝对路径,不依赖运行目录)
LOG_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d')}.log")

logger = logging.getLogger("liushui")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

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