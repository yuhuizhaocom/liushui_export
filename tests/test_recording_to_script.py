"""录制 → 骨架生成器: 生成的代码必须语法正确、能跑通基类骨架, 且不覆盖线上脚本。"""
import ast

import pytest

from tools.recording_to_script import DOWNLOAD_LABEL, render_skeleton, write_skeleton

RECORDS = [
    {"type": "click", "element": {"tag": "button", "cls": "el-button", "text": "导出报表"}},
    {"type": "change", "element": {"tag": "input", "ph": "备注"}},
    {"type": "click", "element": {"tag": "a", "text": DOWNLOAD_LABEL}},
    {"type": "click", "element": {"tag": "a", "text": DOWNLOAD_LABEL}},   # 重复点击应被去重
]


def _code(**kw):
    kw.setdefault("platform_name", "测试台")
    kw.setdefault("class_name", "TestExporter")
    kw.setdefault("platform_key", "test")
    return render_skeleton(RECORDS, **kw)


class _Recorder:
    def __init__(self, download_path="流水.csv"):
        self.calls = []
        self.download_path = download_path

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "wait_download":
                return self.download_path
            if name == "run_standard_flow":
                return "success"
        return _record


def _load(code):
    ns = {}
    exec(compile(code, "<skeleton>", "exec"), ns)
    return ns["TestExporter"]


def test_skeleton_is_valid_python():
    ast.parse(_code())


def test_skeleton_delegates_tail_to_base():
    code = _code()
    assert "run_standard_flow" in code
    assert "def set_date_range" in code and "def trigger_export" in code
    for dropped in ("begin_wait_download", "wait_download(timeout=", "navigate(self.export_url)"):
        assert dropped not in code, dropped   # 收尾由基类负责, 不再各写一份超时


def test_recorded_download_click_not_duplicated(tmp_path):
    """基类收尾会点"下载"; 录制里那一步必须被丢掉, 否则会点到列表另一行。"""
    code = _code()
    assert code.count(f'click_text("{DOWNLOAD_LABEL}")') == 0
    assert DOWNLOAD_LABEL in code             # 但有注释说明它去哪了


def test_skeleton_runs_through_base_flow():
    browser = _Recorder()
    result = _load(_code())().export(browser, "2026-09-01", "2026-09-02")
    names = [c[0] for c in browser.calls]
    assert result == "success"
    assert names.count("begin_wait_download") == 1
    assert ("wait_download", (), {"timeout": 60}) in browser.calls   # 基类默认超时
    assert ("click_selector", ('button.el-button:has-text("导出报表")',), {}) in browser.calls
    # 录制里的 change 步骤生成的是字符串占位值, 不会 NameError
    assert ("fill_placeholder", ("备注", "TODO_VALUE"), {}) in browser.calls


def test_skeleton_returns_manual_when_no_download():
    browser = _Recorder(download_path=None)
    assert _load(_code())().export(browser, "2026-09-01", "2026-09-02") == "manual"


def test_skeleton_without_download_step_still_generates():
    code = render_skeleton([{"type": "click", "element": {"tag": "a", "text": "生成报表"}}],
                           platform_name="测试台", class_name="TestExporter", platform_key="t")
    assert "TODO" in code
    _load(code)()


def test_write_skeleton_refuses_to_clobber(tmp_path):
    target = tmp_path / "platforms" / "test" / "export.py"
    target.parent.mkdir(parents=True)
    target.write_text("# 手工调过的线上脚本\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_skeleton(str(target), _code())
    assert target.read_text(encoding="utf-8") == "# 手工调过的线上脚本\n"


def test_write_skeleton_overwrites_only_when_forced(tmp_path):
    target = tmp_path / "platforms" / "test" / "export.py"
    target.parent.mkdir(parents=True)
    target.write_text("# 旧内容\n", encoding="utf-8")
    write_skeleton(str(target), _code(), force=True)
    assert "run_standard_flow" in target.read_text(encoding="utf-8")


def test_write_skeleton_creates_missing_parent_dir(tmp_path):
    target = tmp_path / "a" / "b" / "export_skeleton.py"
    write_skeleton(str(target), _code())
    assert target.exists()
