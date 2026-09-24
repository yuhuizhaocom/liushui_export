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


_MULTI = '''from core.platform_base import PlatformBase


class AExporter(PlatformBase):
    key = "a"
    name = "甲"
    login_url = ""
    export_url = ""
    guide = ""


class BExporter(PlatformBase):
    key = "b"
    name = "乙"
    login_url = ""
    export_url = ""
    guide = ""
'''


def _one_platform(key, name):
    return ('from core.platform_base import PlatformBase\n\n\n'
            f'class P{name}Exporter(PlatformBase):\n'
            f'    key = "{key}"\n    name = "{name}"\n'
            '    login_url = ""\n    export_url = ""\n    guide = ""\n')


def _make_platforms(tmp_path, monkeypatch, folders):
    """把 {目录名: 模块源码} 挂成可导入的 "platforms" 包并指向 loader。"""
    d = tmp_path / "platforms"
    for fname, src in folders.items():
        pdir = d / fname
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "__init__.py").write_text("", encoding="utf-8")
        (pdir / "export.py").write_text(src, encoding="utf-8")
    mp = types.ModuleType("platforms")
    mp.__path__ = [str(d)]
    monkeypatch.setitem(sys.modules, "platforms", mp)
    for fname in folders:
        sys.modules.pop(f"platforms.{fname}", None)
        sys.modules.pop(f"platforms.{fname}.export", None)
    monkeypatch.setattr(loader, "PLATFORMS_DIR", str(d))
    return d


def _capture_log(monkeypatch):
    records = []
    monkeypatch.setattr(loader, "log",
                        lambda msg, level="info", callback=None: records.append((level, msg)))
    return records


def test_two_platform_classes_in_one_module_all_load(tmp_path, monkeypatch):
    """以前 dir() + break 只注册属性名最靠前的那个, 第二个平台无声消失。"""
    _make_platforms(tmp_path, monkeypatch, {"multi": _MULTI})
    assert sorted(loader.discover_platforms()) == ["a", "b"]


def test_imported_platform_class_is_not_registered_again(tmp_path, monkeypatch):
    """别的模块 import 进来的平台类不能算本平台(否则同一个平台注册两遍)。"""
    other = ('from platforms.multi.export import AExporter\n'
             + _one_platform("c", "丙"))
    _make_platforms(tmp_path, monkeypatch, {"multi": _MULTI, "other": other})
    pls = loader.discover_platforms()
    assert sorted(pls) == ["a", "b", "c"]
    assert pls["a"].__class__.__module__ == "platforms.multi.export"


def test_duplicate_key_is_reported_not_silent(tmp_path, monkeypatch):
    records = _capture_log(monkeypatch)
    _make_platforms(tmp_path, monkeypatch, {"aa": _one_platform("dup", "甲"),
                                            "bb": _one_platform("dup", "乙")})
    pls = loader.discover_platforms()
    assert list(pls) == ["dup"]
    assert pls["dup"].name == "甲"          # 目录序在前的胜出
    assert any(lvl == "warning" and "dup" in msg for lvl, msg in records), records


def test_module_without_platform_class_warns(tmp_path, monkeypatch):
    records = _capture_log(monkeypatch)
    _make_platforms(tmp_path, monkeypatch, {"empty": "x = 1\n"})
    assert loader.discover_platforms() == {}
    assert any(lvl == "warning" and "empty" in msg for lvl, msg in records), records


def test_import_failure_goes_to_log_channel(tmp_path, monkeypatch):
    """pythonw 启动没有控制台, 只 print 的话界面完全看不到加载失败。"""
    records = _capture_log(monkeypatch)
    _make_platforms(tmp_path, monkeypatch,
                    {"broken": "import no_such_platform_dep\n"})
    assert loader.discover_platforms() == {}
    assert any(lvl == "error" and "broken" in msg for lvl, msg in records), records