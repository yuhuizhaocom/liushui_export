"""浏览器 profile 落点与"商户发现"的边界。

旧版在商户为空时把 profile 直接放在 `browser_data/<平台key>/`, 于是那一层同时是
"商户目录的父目录"和某个 Chromium profile 的根 —— `Crashpad`、`Default`、
`Safe Browsing` 这些内部目录就被 `discover_merchants`("每个子目录=一家商户") 当成
商户列进左栏, 还会被自动勾上, 每次导出/检查登录都多跑 N 家空商户。
"""
import os

import pytest

import core.browser as bm
import core.main_gui as mg
from core.browser import BrowserManager


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    """把 browser_data 与 downloads 都挪到 tmp_path 下。"""
    base = tmp_path / "ws"
    browser_data = base / "browser_data"
    browser_data.mkdir(parents=True)
    monkeypatch.setattr(bm, "BROWSER_DATA_DIR", str(browser_data))
    monkeypatch.setattr(mg, "BROWSER_DATA_DIR", str(browser_data))
    return str(browser_data)


def _mkdir(*parts):
    d = os.path.join(*parts)
    os.makedirs(d, exist_ok=True)
    return d


def _touch(path, text="x"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ===== 一、空商户不再把 profile 落在"商户的父目录"上 =====

def test_empty_merchant_does_not_use_the_platform_dir_as_profile(tree):
    b = BrowserManager(headless=True)
    b.set_browser_profile("youzan", "")
    assert os.path.normpath(b._profile_dir) == os.path.normpath(
        os.path.join(tree, "youzan", BrowserManager.RESERVED_MERCHANT_DIR))
    assert os.path.normpath(b._profile_dir) != os.path.normpath(os.path.join(tree, "youzan"))


def test_named_merchant_path_is_unchanged(tree):
    """有商户时路径逐字不变 —— 存量登录态不能因为这次改动搬家。"""
    b = BrowserManager(headless=True)
    b.set_browser_profile("youzan", "旗舰店A")
    assert os.path.normpath(b._profile_dir) == os.path.normpath(
        os.path.join(tree, "youzan", "旗舰店A"))


def test_default_profile_dir_is_not_the_browser_data_root(tree):
    """连平台都没有时也不许落在 browser_data 本身(构造不调 start, 只算路径)。"""
    b = BrowserManager(headless=True)
    assert os.path.normpath(b._profile_dir) != os.path.normpath(tree)
    assert os.path.basename(b._profile_dir) == BrowserManager.RESERVED_PLATFORM_DIR


def test_reserved_dirs_never_collide_with_a_merchant_name(tree):
    from core.main_gui import discover_merchants
    for reserved in (BrowserManager.RESERVED_MERCHANT_DIR, BrowserManager.RESERVED_PLATFORM_DIR):
        _mkdir(tree, "youzan", reserved, "Default")
    assert discover_merchants(["youzan"]) == {}


# ===== 二、已经脏了的目录不再被当成商户 =====

def _build_polluted_platform(tree, key="youzan"):
    plat = _mkdir(tree, key)
    _touch(os.path.join(plat, "Local State"))            # profile 根的标志文件
    _touch(os.path.join(plat, "Last Version"))
    for internal in ("Crashpad", "Default", "GrShaderCache", "segmentation_platform"):
        inner = _mkdir(plat, internal)
        _touch(os.path.join(inner, "whatever"))
    _mkdir(plat, "Safe Browsing")                        # 内部目录里也有空的那种
    # 真商户: 建过档也登录过 -> 自己的 profile 根(里面有 Default/)
    shop = _mkdir(plat, "旗舰店A")
    _mkdir(shop, "Default")
    _touch(os.path.join(shop, BrowserManager.LOGIN_STATE_FILE), "{}")
    # 真商户: 刚点「+」建好、还没登录 -> 空目录
    _mkdir(plat, "新店")
    return plat


def test_polluted_platform_lists_only_real_merchants(tree):
    plat = _build_polluted_platform(tree)
    ignored = []
    got = mg.discover_merchants(["youzan", "alipay"], ignored=ignored)
    assert got == {"youzan": ["新店", "旗舰店A"]}, got
    assert sorted(os.path.basename(p) for p in ignored) == [
        "Crashpad", "Default", "GrShaderCache", "Safe Browsing", "segmentation_platform"]
    assert all(p.startswith("youzan/") for p in ignored)
    assert os.path.isdir(plat), "只是不列出来, 磁盘上的东西一个都不动"


def test_ignored_names_are_reported_not_swallowed(tree):
    """"剔掉了哪些"必须能查得到 —— 静默剔, 用户只会以为程序把他的商户弄丢了。"""
    _build_polluted_platform(tree)

    class _App:
        _note_ignored_profile_dirs = mg.LiushuiApp._note_ignored_profile_dirs

        def __init__(self, ignored):
            self._ignored_profile_dirs = ignored
            self.logs = []

        def _append_log(self, line):
            self.logs.append(line)

    app = _App(["youzan/Default", "youzan/Crashpad"])
    app._note_ignored_profile_dirs("启动")
    assert any("已忽略 2 个浏览器自己的目录" in line for line in app.logs), app.logs
    assert any("youzan/Default" in line for line in app.logs)
    _App([])._note_ignored_profile_dirs()          # 没东西可说时不着一字


def test_clean_platform_does_not_hide_a_merchant_named_like_internals(tree):
    """没被当过 profile 根的平台目录里, 名字名单不生效:
    手工建的、还没登录的空商户目录即使撞名也照常列出来。"""
    plat = _mkdir(tree, "tmall")
    _mkdir(plat, "Default")                 # 空的、名字正好是内部目录名
    assert mg.discover_merchants(["tmall"]) == {"tmall": ["Default"]}


def test_a_merchant_that_really_is_called_default_stays(tree):
    """已登录过的商户目录里有 `Default/` 这一层 —— 判据按内容而不是名字, 所以留着。"""
    plat = _mkdir(tree, "alipay")
    shop = _mkdir(plat, "Default")
    _mkdir(shop, "Default")
    _touch(os.path.join(shop, "marker"))
    assert mg.discover_merchants(["alipay"]) == {"alipay": ["Default"]}


def test_add_merchant_refuses_the_reserved_names(tree):
    """保留名建成商户的话, 商户发现又会把它剔掉 —— 建完就不如当场拒绝。"""
    from core.main_gui import LiushuiApp

    class _Plat:
        key = "youzan"
        name = "有赞"

    class _App:
        _add_merchant = LiushuiApp._add_merchant
        _sanitize_name = staticmethod(LiushuiApp._sanitize_name)

        def __init__(self):
            self.platforms = {"youzan": _Plat()}
            self.merchants = {"youzan": []}
            self.merchant_vars = {}

    app = _App()
    ok, msg = app._add_merchant("youzan", BrowserManager.RESERVED_MERCHANT_DIR)
    assert ok is False and "保留" in msg
    assert not os.path.isdir(os.path.join(tree, "youzan", BrowserManager.RESERVED_MERCHANT_DIR))
