"""
平台管理模块 - 客户端内新增/编辑/调试平台脚本
- 骨架生成与 key 校验为纯函数,便于单元测试
- PlatformManagerDialog / DebugDialog 为 Tkinter UI 壳(后续 Task 补充)
"""
import os
import re

from core.loader import PLATFORMS_DIR

_KEY_RE = re.compile(r"^[a-z0-9_]+$")

_SKELETON = '''\
"""{name}平台导出脚本"""

from core.platform_base import PlatformBase


class {Key}Exporter(PlatformBase):
    key = "{key}"
    name = "{name}"
    login_url = "{login_url}"
    export_url = "{export_url}"
    guide = "{guide}"

    # 不覆盖 export() 时使用默认智能导出(SmartExporter)
'''


def validate_platform_key(key):
    """平台 key 合法性: 小写字母/数字/下划线。"""
    return bool(key) and bool(_KEY_RE.match(key))


def generate_platform_skeleton(key, name, login_url, export_url, guide, platforms_dir=PLATFORMS_DIR):
    """生成平台骨架文件到 platforms_dir/<key>/。重名/非法 key 抛 ValueError。"""
    if not validate_platform_key(key):
        raise ValueError("平台 key 只能包含小写字母、数字、下划线且不能为空")
    target = os.path.join(platforms_dir, key)
    if os.path.isdir(target):
        raise ValueError(f"平台 {key} 已存在")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "__init__.py"), "w", encoding="utf-8"):
        pass
    content = _SKELETON.format(
        Key=key.title().replace("_", ""),
        key=key,
        name=name or "",
        login_url=login_url or "",
        export_url=export_url or "",
        guide=guide or "",
    )
    with open(os.path.join(target, "export.py"), "w", encoding="utf-8") as f:
        f.write(content)
    return target