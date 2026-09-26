"""打包脚本: 白名单组装 + "成品里不许有用户数据"的守卫。

CI 只是把这两个函数当检查用, 所以它们本身要有测试 —— 万一以后有人往 APP_ITEMS 里加了
`docs`/仓库根整目录, 泄漏的就是本机所有商户的登录凭证。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import build_portable as bp     # noqa: E402


@pytest.fixture()
def out(tmp_path):
    return str(tmp_path / "pkg")


def test_build_copies_whitelist_only(out, tmp_path):
    bp.build(out)
    app = os.path.join(out, "app")
    assert os.path.isfile(os.path.join(app, "core", "main_gui.py"))
    assert os.path.isdir(os.path.join(app, "platforms", "youzan"))
    assert os.path.isfile(os.path.join(app, "tools", "build_portable.py"))
    assert os.path.isfile(os.path.join(out, "run-portable.vbs"))
    assert os.path.isfile(os.path.join(out, "run-portable.bat"))
    assert os.path.isfile(os.path.join(out, "README-绿色版.txt"))
    # 仓库根那些运行时产物一个都不该跟着走
    for junk in ("tests", "docs", "downloads", "browser_data", "logs", ".venv", ".git",
                 "settings.json", "selection_state.json"):
        assert not os.path.exists(os.path.join(app, junk)), junk
        assert not os.path.exists(os.path.join(out, junk)), junk


def test_build_drops_pycache(out):
    bp.build(out)
    for dirpath, dirnames, _files in os.walk(os.path.join(out, "app")):
        assert "__pycache__" not in dirnames, dirpath


def test_missing_item_fails_loudly(out, monkeypatch, tmp_path):
    """白名单里点名了却不存在 → 直接失败, 不能默默出一个少文件的包。"""
    monkeypatch.setattr(bp, "APP_ITEMS", ("core", "没有这个东西.md"))
    with pytest.raises(SystemExit):
        bp.build(out)


def test_scan_catches_user_data_and_credentials(out, tmp_path):
    app = os.path.join(out, "app")
    os.makedirs(os.path.join(app, "browser_data", "youzan", "店A"), exist_ok=True)
    with open(os.path.join(app, "browser_data", "youzan", "店A",
                           "login_state.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    with open(os.path.join(app, "settings.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    hits = bp.scan_forbidden(out)
    joined = " ".join(hits)
    assert "login_state.json" in joined and "browser_data" in joined and "settings.json" in joined
    # 只报告不动文件: 删谁、留谁是打包脚本外面决定的
    assert os.path.isfile(os.path.join(app, "settings.json"))


def test_clean_package_passes_the_scan(out):
    bp.build(out)
    assert bp.scan_forbidden(out) == []
