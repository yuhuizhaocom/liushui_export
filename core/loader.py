"""
平台加载器 - 自动发现 platforms/ 目录下的所有平台脚本

新增平台步骤:
    1. 在 platforms/ 目录下新建文件夹,如 platforms/myplatform/
    2. 在文件夹中创建 export.py,继承 PlatformBase
    3. 重启程序,平台自动出现在界面中
"""

import os
import sys
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORMS_DIR = os.path.join(ROOT, "platforms")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from .platform_base import PlatformBase


def discover_platforms():
    """扫描 platforms/ 目录,加载所有平台,返回 {key: 平台实例}"""
    platforms = {}
    if not os.path.isdir(PLATFORMS_DIR):
        return platforms

    for folder in sorted(os.listdir(PLATFORMS_DIR)):
        dirpath = os.path.join(PLATFORMS_DIR, folder)
        if not os.path.isdir(dirpath):
            continue
        # 跳过非平台目录(如 __pycache__)
        if folder.startswith("_"):
            continue
        export_file = os.path.join(dirpath, "export.py")
        if not os.path.exists(export_file):
            continue
        try:
            mod = importlib.import_module(f"platforms.{folder}.export")
            for attr in dir(mod):
                obj = getattr(mod, attr)
                if (isinstance(obj, type) and issubclass(obj, PlatformBase)
                        and obj is not PlatformBase and getattr(obj, "key", "")):
                    inst = obj()
                    platforms[inst.key] = inst
                    break
        except Exception as e:
            print(f"[加载失败] 平台 {folder}: {e}")
    return platforms


def reload_platforms():
    """清除平台模块缓存后重新发现。

    通过平台管理修改/新增 export.py 后调用, 使改动无需重启立即生效
    (importlib 会将模块缓存于 sys.modules, 必须显式删除)。
    """
    if os.path.isdir(PLATFORMS_DIR):
        for folder in os.listdir(PLATFORMS_DIR):
            dirpath = os.path.join(PLATFORMS_DIR, folder)
            if not os.path.isdir(dirpath) or folder.startswith("_"):
                continue
            for mod_name in (f"platforms.{folder}", f"platforms.{folder}.export"):
                if mod_name in sys.modules:
                    del sys.modules[mod_name]
    return discover_platforms()
