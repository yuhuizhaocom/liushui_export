"""CI 测试自举: tests/conftest.py 负责把仓库根和包内依赖挂进 sys.path。

"缺 playwright 会不会红"只有真跑 CI 才看得见(第一次就是这么红的), 但"挂哪些目录、以什么
顺序挂"可以离线钉住 —— 目录形状变了(改 `PKG` 名、把 build 挪走)当场就该报, 而不是等到出包
时又留下一句莫名其妙的 ModuleNotFoundError。

已用"没有 playwright 的干净解释器 + 只有包内一份假 playwright"复现过 CI 的那一步:
挂上前 9 个模块 collection 全红, 挂上后 409 passed。
"""
import importlib.util
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _conftest():
    spec = importlib.util.spec_from_file_location(
        "_liushui_conftest", os.path.join(_REPO, "tests", "conftest.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 顺带真的往 sys.path 上挂了一次
    return mod


def _fresh_path(monkeypatch):
    """给 mount() 一个干净的 sys.path 副本, 免得把 tmp 路径漏给后面的测试。"""
    monkeypatch.setattr(sys, "path", [])


def test_repo_root_goes_first_so_core_resolves_to_this_copy(monkeypatch):
    cf = _conftest()
    _fresh_path(monkeypatch)
    cf.mount()
    assert sys.path[0] == _REPO, "仓库根要排在最前, 否则 import 到的 core 可能不是这份"


def test_packaged_site_packages_is_appended_not_pushed(tmp_path, monkeypatch):
    """包内依赖只能追加到最后: 本机装着真 playwright, 不能被包内那份(或半成品)抢走。"""
    cf = _conftest()
    site = tmp_path / "build" / "out2" / "app" / "site-packages"
    (site / "playwright").mkdir(parents=True)
    monkeypatch.setattr(cf, "_REPO", str(tmp_path))
    assert cf._site_paths() == [str(site)], "工作流改了 PKG 目录名/位置时, 这边要立刻看出来"
    _fresh_path(monkeypatch)
    sys.path.insert(0, str(tmp_path / "real-env-site-packages"))
    cf.mount()
    assert sys.path[-1] == str(site), "包内依赖必须排在环境自带目录之后"
    assert sys.path[0] == str(tmp_path)


def test_only_site_packages_ever_gets_picked_up(tmp_path, monkeypatch):
    """`build/*/app` 本身绝不能进 sys.path —— 那会让测试跑进打进包的代码副本里。"""
    cf = _conftest()
    app = tmp_path / "build" / "pkg" / "app"
    (app / "site-packages" / "playwright").mkdir(parents=True)
    (app / "core").mkdir()
    monkeypatch.setattr(cf, "_REPO", str(tmp_path))
    assert cf._site_paths() == [str(app / "site-packages")]
