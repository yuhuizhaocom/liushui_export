"""平台加载器 reload 语义测试: 编辑/新增平台脚本后无需重启即生效。"""
import sys
import types

import core.loader as loader

_TPL = '''from core.platform_base import PlatformBase


class FakeExporter(PlatformBase):
    key = "fake"
    name = "{name}"
    login_url = ""
    export_url = ""
    guide = ""
'''


def _setup_tmp_platforms(tmp_path, monkeypatch, name="旧名称"):
    """把 tmp 下的 platforms 目录挂载为可导入的 "platforms" 包, 返回目录。
    同时重置 platforms 子模块缓存, 避免上一用例遗留的 sys.modules 污染。"""
    d = tmp_path / "platforms"
    (d / "fake").mkdir(parents=True)
    (d / "fake" / "__init__.py").write_text("", encoding="utf-8")
    (d / "fake" / "export.py").write_text(_TPL.format(name=name), encoding="utf-8")
    mp = types.ModuleType("platforms")
    mp.__path__ = [str(d)]
    monkeypatch.setitem(sys.modules, "platforms", mp)
    # 清除子模块缓存, 避免上一用例遗留; 直接 pop 即可(无其他测试引用这些名字)
    sys.modules.pop("platforms.fake", None)
    sys.modules.pop("platforms.fake.export", None)
    monkeypatch.setattr(loader, "PLATFORMS_DIR", str(d))
    return d


def test_reload_platforms_picks_up_edited_script(tmp_path, monkeypatch):
    d = _setup_tmp_platforms(tmp_path, monkeypatch, name="旧名称")

    pls1 = loader.reload_platforms()
    assert pls1["fake"].name == "旧名称"

    # 修改脚本后 reload, 必须看到新内容(验证 sys.modules 缓存被清理)
    (d / "fake" / "export.py").write_text(_TPL.format(name="新名称"), encoding="utf-8")
    pls2 = loader.reload_platforms()
    assert pls2["fake"].name == "新名称"


def test_discover_keeps_working_with_same_dir(tmp_path, monkeypatch):
    """直接 discover_platforms 在存量目录下正常工作。"""
    _setup_tmp_platforms(tmp_path, monkeypatch, name="第一个")
    pls = loader.discover_platforms()
    assert pls["fake"].name == "第一个"
    assert pls["fake"].key == "fake"