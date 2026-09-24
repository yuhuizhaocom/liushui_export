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
from .logger import log


def _platform_classes(mod):
    """取模块内**自己定义**的平台类。

    两条限制都是必要的:
    - __module__ 必须是本模块, 否则 `from platforms.youzan.export import ...`
      这种引用会让有赞的类被注册两遍(一遍在 youzan, 一遍在这个文件夹);
    - 带非空 key 才算平台。
    """
    classes = []
    for attr in dir(mod):
        obj = getattr(mod, attr)
        if (isinstance(obj, type) and issubclass(obj, PlatformBase)
                and obj is not PlatformBase and getattr(obj, "key", "")
                and getattr(obj, "__module__", "") == mod.__name__):
            classes.append(obj)
    return classes


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
            classes = _platform_classes(mod)
            if not classes:
                log(f"[未加载] 平台 {folder}: export.py 里没有定义带 key 的 PlatformBase 子类",
                    "warning")
            for cls in classes:
                inst = cls()
                if inst.key in platforms:
                    # 以前这里 dir()+break 只留一个, 另一个无声消失
                    log(f"[跳过] 平台 {folder} 的 key={inst.key} 与已加载的平台重复",
                        "warning")
                    continue
                platforms[inst.key] = inst
        except Exception as e:
            # 走统一日志通道: pythonw 启动时没有控制台, 只 print 的话界面上完全看不到
            log(f"[加载失败] 平台 {folder}: {e}", "error")
    return platforms


def _drop_platform_bytecode():
    """删除 platforms/ 各脚本目录下 __pycache__ 里的字节码文件。

    .pyc 头里存的是"截断到秒"的源文件 mtime 加上源文件字节数。在平台管理里改完
    脚本马上 reload 时, 只要这次改动没改变文件长度又落在同一秒内, 旧字节码就会被
    判定为仍然有效并再次执行 —— sys.modules 已经清干净了也没用, 重新 import 读到的
    还是旧代码(表现为"保存了但没生效")。
    """
    for dirpath, _dirnames, filenames in os.walk(PLATFORMS_DIR):
        if os.path.basename(dirpath) != "__pycache__":
            continue
        for name in filenames:
            try:
                os.remove(os.path.join(dirpath, name))
            except OSError:
                pass   # 被占用时跳过, 最坏是这一次没生效, 不能让 reload 抛错


def reload_platforms():
    """清除平台模块缓存后重新发现。

    通过平台管理修改/新增 export.py 后调用, 使改动无需重启立即生效。三处缓存都要
    清: sys.modules 里的模块对象、importlib 的目录/源码 stat 缓存、磁盘上的 .pyc。
    """
    if os.path.isdir(PLATFORMS_DIR):
        for folder in os.listdir(PLATFORMS_DIR):
            dirpath = os.path.join(PLATFORMS_DIR, folder)
            if not os.path.isdir(dirpath) or folder.startswith("_"):
                continue
            for mod_name in (f"platforms.{folder}", f"platforms.{folder}.export"):
                if mod_name in sys.modules:
                    del sys.modules[mod_name]
        _drop_platform_bytecode()
    importlib.invalidate_caches()
    return discover_platforms()
