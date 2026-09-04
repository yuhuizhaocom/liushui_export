# tests/test_platform_admin.py
import os
import pytest

from core.platform_admin import generate_platform_skeleton, validate_platform_key


def _read_rel(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


def test_validate_key_rejects_invalid():
    assert validate_platform_key("youzan2") is True
    assert validate_platform_key("has space") is False
    assert validate_platform_key("大写X") is False
    assert validate_platform_key("ok_1") is True


def test_generate_skeleton_creates_files(tmp_path):
    gen_dir = tmp_path / "platforms"
    generate_platform_skeleton("mypay", "我的支付", "https://a/login",
                               "https://a/export", "后台→流水→导出", str(gen_dir))
    init_py = _read_rel(str(gen_dir), "mypay/__init__.py")
    export_py = _read_rel(str(gen_dir), "mypay/export.py")
    assert init_py.strip() == ""
    assert 'key = "mypay"' in export_py
    assert 'name = "我的支付"' in export_py
    assert "class MypayExporter(PlatformBase)" in export_py
    assert "from core.platform_base import PlatformBase" in export_py


def test_generate_skeleton_rejects_conflict(tmp_path):
    gen_dir = tmp_path / "platforms"
    (gen_dir / "youzan").mkdir(parents=True)
    with pytest.raises(ValueError, match="已存在"):
        generate_platform_skeleton("youzan", "有赞", "", "", "", str(gen_dir))