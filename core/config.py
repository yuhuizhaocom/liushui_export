"""
全局配置
平台信息已迁移到 platforms/ 目录下的各平台脚本中,不再在此维护
"""

import json
import os

# 项目根目录(此文件位于 core/ 子目录,上溯一级)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 下载文件保存目录(基于项目根的绝对路径,不依赖运行目录)
DOWNLOAD_DIR = os.path.join(ROOT_DIR, "downloads")

# 浏览器数据目录(保存登录状态;基于项目根,不依赖运行目录)
BROWSER_DATA_DIR = os.path.join(ROOT_DIR, "browser_data")

# 用户设置文件(自动保存,无需手动编辑)
SETTINGS_FILE = os.path.join(ROOT_DIR, "settings.json")

# 定时任务持久化文件
SCHEDULED_TASKS_FILE = os.path.join(ROOT_DIR, "scheduled_tasks.json")

# 平台/商户勾选状态持久化文件(重启后恢复上次勾选)
SELECTION_FILE = os.path.join(ROOT_DIR, "selection_state.json")

DEFAULT_SETTINGS = {
    "show_browser": True,        # 是否显示浏览器窗口(取消勾选=后台运行)
    "download_name_mode": "unified",  # 下载文件名: unified=统一命名 / original=保留原始名称(非UUID)
    "enable_keepalive": True,        # 登录保活开关
    "keepalive_interval_min": 30,    # 保活巡检间隔(分钟)
    "retry_times": 2,                # 失败自动重试次数(0=不重试)
    "retry_interval_s": 30,          # 首次重试间隔秒(后续递增×2)
}


def load_settings():
    """读取用户设置,缺失字段用默认值"""
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                for k, v in DEFAULT_SETTINGS.items():
                    if k in data:
                        settings[k] = data[k]
    except Exception:
        pass
    return settings


def write_json_atomic(path, data):
    """先写同目录临时文件再 os.replace, 不留半截 JSON。

    这几个文件都是"界面每次改动就整体重写"的(settings / scheduled_tasks /
    selection_state): 直接 open("w") 覆盖时若进程在写入中途死掉, 文件会是截断的;
    而三处读取都是 except → 用默认值/返回空, 结果用户看到的是"配置和定时任务被
    静默清空"。os.replace 在同一磁盘卷上是原子替换。
    """
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:                      # 失败的这次不算数, 别留垃圾文件
            os.remove(tmp)
        except OSError:
            pass
        raise


def save_settings(settings):
    """保存用户设置到文件"""
    try:
        merged = dict(DEFAULT_SETTINGS)
        if isinstance(settings, dict):
            merged.update(settings)
        write_json_atomic(SETTINGS_FILE, merged)
    except Exception:
        pass
