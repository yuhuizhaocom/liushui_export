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

DEFAULT_SETTINGS = {
    "show_browser": True,        # 是否显示浏览器窗口(取消勾选=后台运行)
    "download_name_mode": "unified",  # 下载文件名: unified=统一命名 / original=保留原始名称(非UUID)
    "enable_keepalive": True,        # 登录保活开关
    "keepalive_interval_min": 30,    # 保活巡检间隔(分钟)
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


def save_settings(settings):
    """保存用户设置到文件"""
    try:
        merged = dict(DEFAULT_SETTINGS)
        if isinstance(settings, dict):
            merged.update(settings)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
