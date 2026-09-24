"""平台脚本的调用序列快照: 迁移到基类骨架时, 每次浏览器调用都必须逐条不变。

平台脚本最终跑在已登录的真实后台页面上, 离线点不动; 能钉死的只有"调了哪些方法、
什么顺序、什么参数"。tests/data/platform_call_sequences.json 是**迁移前**用同一套
哑 browser 跑出来的真实序列(不是手抄), 任何改动让序列变了都会在这里失败, 提示
"你以为等价的改写其实改变了操作"。
"""
import importlib
import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_FILE = os.path.join(REPO_ROOT, "tests", "data", "platform_call_sequences.json")
START, END = "2026-09-01", "2026-09-02"

with open(SNAPSHOT_FILE, encoding="utf-8") as _f:
    SNAPSHOT = json.load(_f)


class _Any:
    """支持任意链式访问的哑对象: 只要两次跑用的是同一个它, 分支就是确定的。"""

    def __getattr__(self, name):
        return _Any()

    def __call__(self, *args, **kwargs):
        return _Any()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return True

    def __len__(self):
        return 0


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("_") and name != "_log":
            return _Any()      # dunder 探测不计入序列

        def _record(*args, **kwargs):
            self.calls.append([name, [repr(a) for a in args],
                               {k: repr(v) for k, v in kwargs.items()}])
            if name == "wait_download":
                return "流水导出.csv"
            if name == "is_visible_text":
                return True
            return _Any()
        return _record


def _script_class(folder):
    mod = importlib.import_module(f"platforms.{folder}.export")
    from core.platform_base import PlatformBase
    return next(v for k, v in sorted(vars(mod).items())
                if isinstance(v, type) and issubclass(v, PlatformBase)
                and v is not PlatformBase and getattr(v, "key", "")
                and getattr(v, "__module__", "") == mod.__name__)


@pytest.mark.parametrize("folder", sorted(SNAPSHOT))
def test_call_sequence_matches_snapshot(folder):
    expected = SNAPSHOT[folder]
    if "error" in expected:
        pytest.skip(f"快照录制时该脚本就不可用: {expected['error']}")
    browser = Recorder()
    result = _script_class(folder)().export(browser, START, END)
    assert browser.calls == [c[:3] for c in expected["calls"]], "浏览器调用序列与迁移前不一致"
    assert result == expected["result"], "返回值与迁移前不一致"


def test_snapshot_covers_every_platform_folder():
    folders = {d for d in os.listdir(os.path.join(REPO_ROOT, "platforms"))
               if os.path.isfile(os.path.join(REPO_ROOT, "platforms", d, "export.py"))}
    assert folders == set(SNAPSHOT), "新增了平台却没录基线快照"
