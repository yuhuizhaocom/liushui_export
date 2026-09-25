r"""删商户: 只能删 browser_data/<平台>/<商户> 这一层, 多一层少一层都拒绝。

以前全仓没有删除商户的入口: 误点一次 `+` 打错名字, 那家"商户"就永久留在列表里, 每次
导出都被跑一遍、保活也每次都去开它的登录页。现在每行有"删"。危险点在路径 ——
`sanitize_name` 只把 `<>:"/\` 换成下划线, `..` 原样留下, 拼出来的就是父目录, 所以删除
前必须核对"是不是正好深一层"。
"""
import os

import pytest

from core.browser import BrowserManager
from core.main_gui import delete_merchant_profile, merchant_profile_dir


@pytest.fixture()
def base(tmp_path):
    b = tmp_path / "browser_data"
    for m in ("旗舰店A", "旗舰店B", "打错了"):
        (b / "youzan" / m).mkdir(parents=True)
        (b / "youzan" / m / "Default").mkdir()
        (b / "youzan" / m / "Default" / "Cookies").write_text("cookie")
    (b / "tmall" / "旗舰店A").mkdir(parents=True)
    (tmp_path / "downloads" / "有赞" / "旗舰店A").mkdir(parents=True)
    (tmp_path / "downloads" / "有赞" / "旗舰店A" / "流水.xlsx").write_text("x")
    return str(b)


def test_deletes_only_that_merchant_folder(base):
    ok, msg = delete_merchant_profile(base, "youzan", "打错了")
    assert ok is True, msg
    assert sorted(os.listdir(os.path.join(base, "youzan"))) == ["旗舰店A", "旗舰店B"]
    assert os.path.isdir(os.path.join(base, "tmall", "旗舰店A")), "同名的别的平台商户不能连带删掉"
    assert os.path.isfile(os.path.join(os.path.dirname(base), "downloads", "有赞",
                                       "旗舰店A", "流水.xlsx")), "已导出的账单不受影响"


@pytest.mark.parametrize("bad", ["..", ".", "", "   "])
def test_names_that_would_escape_the_platform_folder_are_refused(base, bad):
    ok, msg = delete_merchant_profile(base, "youzan", bad)
    assert ok is False
    assert "拒绝删除" in msg
    assert os.path.isdir(os.path.join(base, "youzan", "旗舰店A")), "拒绝就意味着一个目录都没动"
    assert os.path.isdir(base), "browser_data 本身更不能没"


def test_backslashes_are_neutralised_before_they_can_escape(base):
    r"""`\` 会被 sanitize 换成 `_`, 所以 "..\.." 只是个不存在的普通目录名。"""
    ok, msg = delete_merchant_profile(base, "youzan", "..\\..")
    assert ok is True and "本来就不存在" in msg
    assert set(os.listdir(os.path.join(base, "youzan"))) == {"旗舰店A", "旗舰店B", "打错了"}


def test_missing_folder_is_not_an_error(base):
    ok, msg = delete_merchant_profile(base, "youzan", "从没建过")
    assert ok is True and "本来就不存在" in msg


def test_locked_folder_reports_failure_and_keeps_everything(base, monkeypatch):
    def locked(path, *a, **k):
        raise PermissionError("另一个 Chromium 正占着")
    monkeypatch.setattr("core.main_gui.shutil.rmtree", locked)
    monkeypatch.setattr("core.main_gui.time.sleep", lambda s: None)
    ok, msg = delete_merchant_profile(base, "youzan", "旗舰店A")
    assert ok is False
    assert "删除失败" in msg and "占着" in msg
    assert os.path.isdir(os.path.join(base, "youzan", "旗舰店A"))


def test_target_path_matches_what_the_browser_would_use(base, monkeypatch):
    """删除用的路径必须和运行期真正建 profile 的那套算法一致, 否则删错目录。"""
    monkeypatch.setattr("core.browser.BROWSER_DATA_DIR", base)
    mgr = BrowserManager(headless=True)               # 不 start(), 不碰浏览器
    mgr.set_browser_profile("youzan", "旗舰店A")
    assert os.path.normcase(mgr._profile_dir) == os.path.normcase(
        merchant_profile_dir(base, "youzan", "旗舰店A"))


def test_platform_key_is_sanitized_the_same_way(base):
    assert merchant_profile_dir(base, "不存在的平台", "X").endswith(
        os.path.join("不存在的平台", "X"))
