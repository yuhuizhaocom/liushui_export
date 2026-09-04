# tests/test_platform_admin.py
import os
import pytest

from core.platform_admin import generate_platform_skeleton, validate_platform_key, DebugProbe


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


class FakeInner:
    """桩 BrowserManager: 记录调用,有 page 属性。"""

    def __init__(self):
        self.calls = []
        self.page = object()

    def navigate(self, url, retries=3):
        self.calls.append(("navigate", url))
        return True

    def click_text(self, text, exact=False, retries=3):
        self.calls.append(("click_text", text))
        return True

    def begin_wait_download(self):
        self.calls.append(("begin_wait_download",))
        return None

    def wait_download(self, timeout=120):
        self.calls.append(("wait_download", timeout))
        return "/tmp/out.xlsx"

    def sleep(self, seconds):
        self.calls.append(("sleep", seconds))

    def screenshot(self, name="screenshot"):
        self.calls.append(("screenshot", name))
        return name


def test_debug_probe_records_steps():
    inner = FakeInner()
    probe = DebugProbe(inner, steps=[])
    assert probe.page is inner.page  # page 透传
    probe.navigate("https://x")
    probe.click_text("导出")
    probe.begin_wait_download()
    path = probe.wait_download(timeout=60)
    assert path == "/tmp/out.xlsx"
    assert len(probe.steps) >= 4
    assert probe.steps[0]["action"] == "navigate"
    assert probe.steps[1]["action"] == "click_text"
    assert probe.steps[2]["action"] == "begin_wait_download"
    assert probe.steps[3]["action"] == "wait_download"


def test_debug_probe_records_exception():
    class BoomInner(FakeInner):
        def click_text(self, text, exact=False, retries=3):
            self.calls.append(("click_text", text))
            raise RuntimeError("boom")

    inner = BoomInner()
    probe = DebugProbe(inner, steps=[])
    probe.click_text("导出")
    last = probe.steps[-1]
    assert last["action"] == "click_text"
    assert last["ok"] is False
    assert "boom" in last["error"]