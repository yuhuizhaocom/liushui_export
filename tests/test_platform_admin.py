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


def test_debug_probe_records_exception_and_propagates(monkeypatch):
    class BoomInner(FakeInner):
        def click_text(self, text, exact=False, retries=3):
            self.calls.append(("click_text", text))
            raise RuntimeError("boom")

    inner = BoomInner()
    probe = DebugProbe(inner, steps=[])
    with pytest.raises(RuntimeError):        # 必须像生产一样抛出, 否则调试结果是假的
        probe.click_text("导出")
    last = probe.steps[-1]
    assert last["action"] == "click_text"
    assert last["ok"] is False
    assert "boom" in last["error"]


# 平台脚本与 PlatformBase 实际会调到的 browser 方法(逐个列在探针里早就漏过)
PROBED_METHODS = ["wait_for", "snapshot", "snapshot_on_failure", "step_pause",
                  "wait_user", "_log", "wait_download", "close_popup",
                  "fill_placeholder", "get_page_info"]


@pytest.mark.parametrize("name", PROBED_METHODS)
def test_probe_proxies_every_method_the_scripts_actually_call(name):
    calls = []

    class Inner:
        page = object()

        def __getattr__(self, item):
            def _any(*a, **k):
                calls.append((item, a, k))
                return "ok"
            return _any

    probe = DebugProbe(Inner(), steps=[])
    getattr(probe, name)()
    assert calls and calls[0][0] == name
    assert probe.steps[-1]["action"] == name and probe.steps[-1]["ok"] is True


def test_probe_forwards_arguments_verbatim():
    """以前 navigate 只转发 (url, retries), 脚本传 skip_if_same 会 TypeError。"""
    seen = {}

    class Inner:
        page = None

        def navigate(self, url, retries=3, skip_if_same=True):
            seen.update(url=url, retries=retries, skip_if_same=skip_if_same)
            return True

    DebugProbe(Inner(), steps=[]).navigate("u", skip_if_same=False)
    assert seen == {"url": "u", "retries": 3, "skip_if_same": False}


def test_probe_still_raises_attribute_error_for_unknown_names():
    class Inner:
        page = None

    with pytest.raises(AttributeError):
        DebugProbe(Inner(), steps=[]).no_such_method()


GOOD = 'from core.platform_base import PlatformBase\n\n\nclass OkExporter(PlatformBase):\n    key = "ok"\n'


def test_save_rejects_syntax_error_without_touching_the_file(tmp_path):
    """一次误编辑不能把原本能跑的脚本毁掉 —— 磁盘上必须还是旧的好内容。"""
    from core.platform_admin import save_platform_script

    p = tmp_path / "export.py"
    p.write_text(GOOD, encoding="utf-8")
    with pytest.raises(ValueError) as e:
        save_platform_script(str(p), "def broken(:\n    pass\n")
    assert p.read_text(encoding="utf-8") == GOOD
    assert "语法错误" in str(e.value)
    assert not (tmp_path / "export.py.tmp").exists()


def test_save_writes_valid_content_without_temp_leftovers(tmp_path):
    from core.platform_admin import save_platform_script

    p = tmp_path / "platforms" / "ok" / "export.py"
    save_platform_script(str(p), GOOD)         # 目录不存在也要能写
    assert p.read_text(encoding="utf-8") == GOOD
    assert not (p.parent / "export.py.tmp").exists()


def test_save_reports_line_number(tmp_path):
    from core.platform_admin import save_platform_script

    p = tmp_path / "export.py"
    p.write_text(GOOD, encoding="utf-8")
    with pytest.raises(ValueError) as e:
        save_platform_script(str(p), "x = 1\ny = (\n")
    assert "第 2 行" in str(e.value)