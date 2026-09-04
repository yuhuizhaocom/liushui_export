"""
平台管理模块 - 客户端内新增/编辑/调试平台脚本
- 骨架生成与 key 校验为纯函数,便于单元测试
- PlatformManagerDialog / DebugDialog 为 Tkinter UI 壳(后续 Task 补充)
"""
import os
import re
import time

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


class DebugProbe:
    """包装 BrowserManager,记录平台脚本对 browser 的每次调用(步骤调试)。

    steps: 每项为 dict {i, action, args, ms, ok, error}。
    以 browser 参数传入 PlatformBase.export(), 平台脚本无需任何改动。
    """

    def __init__(self, inner, steps=None):
        self.inner = inner
        self.steps = steps if steps is not None else []
        self.page = getattr(inner, "page", None)

    def _record(self, action, args, ok=True, error=None):
        self.steps.append({
            "i": len(self.steps) + 1,
            "action": action,
            "args": args,
            "ok": ok,
            "error": error,
        })

    def _call(self, action, fn, args, kwargs):
        t0 = time.time()
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            self._record(action, (args, kwargs), ok=False, error=str(e))
            return None
        self._record(action, (args, kwargs), ok=True)
        return result

    # ---- 透传拦截: 平台脚本常用方法 ----
    def navigate(self, url, retries=3):
        return self._call("navigate", self.inner.navigate, (url,), {"retries": retries})

    def safe_click(self, selector, description="", retries=3):
        return self._call("safe_click", self.inner.safe_click,
                          (selector,), {"description": description, "retries": retries})

    def click_text(self, text, exact=False, retries=3):
        return self._call("click_text", self.inner.click_text,
                          (text,), {"exact": exact, "retries": retries})

    def click_selector(self, selector, retries=3):
        return self._call("click_selector", self.inner.click_selector, (selector,), {"retries": retries})

    def fill_placeholder(self, placeholder, value, retries=2):
        return self._call("fill_placeholder", self.inner.fill_placeholder,
                          (placeholder,), {"value": value, "retries": retries})

    def fill_selector(self, selector, value, retries=2):
        return self._call("fill_selector", self.inner.fill_selector,
                          (selector,), {"value": value, "retries": retries})

    def sleep(self, seconds):
        return self._call("sleep", self.inner.sleep, (seconds,), {})

    def is_visible_text(self, text, timeout=3):
        return self._call("is_visible_text", self.inner.is_visible_text,
                          (text,), {"timeout": timeout})

    def close_popup(self, retries=2):
        return self._call("close_popup", self.inner.close_popup, (), {"retries": retries})

    def begin_wait_download(self):
        return self._call("begin_wait_download", self.inner.begin_wait_download, (), {})

    def wait_download(self, timeout=120):
        return self._call("wait_download", self.inner.wait_download, (), {"timeout": timeout})

    def screenshot(self, name="screenshot"):
        return self._call("screenshot", self.inner.screenshot, (name,), {})

    def get_page_info(self):
        return self._call("get_page_info", self.inner.get_page_info, (), {})