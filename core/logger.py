"""
日志模块 - 同时输出到GUI和文件
"""

import os
import logging
from datetime import datetime

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d')}.log")

logger = logging.getLogger("liushui")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)


def log(message, level="info", callback=None):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {message}"
    # 无控制台环境(pythonw)下 sys.stdout 可能为 None,print 会抛错
    try:
        print(line)
    except Exception:
        pass
    logger.info(message)
    if callback:
        try:
            callback(line)
        except Exception:
            pass
    return line
