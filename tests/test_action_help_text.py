"""「使用说明」的文案要跟得交流程改动, 也要把界面上的东西说全。

第九轮把首次登录从"登完回来点确定"改成"关掉浏览器窗口就代表登完", 文案却还停在旧说法上 ——
用户照旧说法在没登完时点掉窗口, 正是那次改造要治的病, 所以文案本身要有测试钉着。

后来发现问题比那句文案大: 整段说明是塞进 `messagebox.showinfo` 的一串参数, 那种框不能滚动,
长了只看得见开头几行, 而界面上十来个按钮(检查更新/子商户/定时任务/录制→脚本/稳定性看板/
工作空间/打开下载文件夹/清空屏…)一个字都没提。现在文案是 `dialogs.build_help_text`
(纯函数, 这里逐条钉), 窗口是 `dialogs.show_help`(可滚动、能整段复制、能打开随包的说明文档)。
"""
import io
import os
import re
import types

import pytest

from core import main_gui
from core.dialogs import HELP_SECTIONS, build_help_text, show_help
from core.main_gui import LiushuiApp

# 「使用说明」弹窗自身不用在文里点名自己
_SELF = "使用说明"


def _text():
    return build_help_text(version="0.0.7", data_root="D:/流水导出工作空间")


def _bar_labels():
    """从源码里摘主界面按钮区那几个名字 —— 加按钮忘了写说明时, 这条会红。"""
    src = io.open(main_gui.__file__, encoding="utf-8").read()
    start = src.index("others = [")
    end = src.index("for t, cmd, col, r, c in others")
    return [m for m in re.findall(r'\("([^"]+)",\s*self\._', src[start:end]) if m != _SELF]


def test_close_window_is_the_login_done_signal():
    text = _text()
    assert "把那个浏览器窗口关掉" in text
    assert "不用在页面里找按钮" in text
    # 旧说法不许回来: 它害人在还没登完时就把窗口点掉
    assert '点"确定"' not in text


@pytest.mark.parametrize("label", _bar_labels() + [
    "开始导出", "全选", "刷新商户", "子商户", "打开", "删", "清空屏", "复制日志",
    "登录提示窗",
])
def test_everything_on_the_screen_is_explained(label):
    assert label in _text(), "使用说明里没提到「%s」这个按钮" % label


def test_the_two_safety_nets_are_still_there():
    """开跑前的登录预检、跑起来后的"中止"都已进主流程, 文案里必须各有一句。"""
    text = _text()
    assert "中止" in text
    assert "登录" in text and "检查" in text


def test_waiting_for_a_human_is_explained():
    """扫码那扇窗: 用户必须知道"不点『我已完成』它就不会继续点下载"。"""
    text = _text()
    assert "我已完成" in text
    assert "这一家先放弃" in text
    assert "10 分钟" in text


def test_where_the_files_land():
    """账单落点与那几个特殊目录: 出事时用户要找的就是这几条路径。"""
    text = _text()
    for needle in ("downloads/平台/商户", "历史/", "snapshots/", "待确认/",
                   "browser_data", "汇总", "工作空间"):
        assert needle in text, "没说清 %s" % needle


def test_update_section_covers_the_gates():
    text = _text()
    assert "检查更新" in text
    # 三道闸门的方向性承诺: 只换程序文件、不碰账单与登录态、依赖不一致就不就地换
    assert "程序文件" in text and "重启" in text
    assert "全量版" in text and "只读" in text
    assert "内网" in text


def test_current_version_and_workspace_are_shown():
    text = _text()
    assert "v0.0.7" in text, "标题行要把当前版本带出来, 用户报问题时照这一行说"
    assert "D:/流水导出工作空间" in text


def test_sections_are_not_empty_and_have_titles():
    for title, lines in HELP_SECTIONS:
        assert title and lines
        assert all(line.strip() for line in lines), "%s 里有空行" % title
    assert "**" not in _text(), "纯文本窗口里不该出现 markdown 星号"


# ===== 窗口本身 =====

@pytest.fixture(scope="module")
def root():
    """一个模块共用的隐藏根窗口。

    反复 `tk.Tk()` 在这台机器上会偶发 `invalid command name "tcl_findLibrary"`(同一个解释器
    里第二次建根窗口时踩到), 建一次复用就没这问题 —— 用例只造自己的 Toplevel, 不各自开根。
    """
    tk = pytest.importorskip("tkinter")
    try:
        r = tk.Tk()
    except Exception as e:                      # 无显示环境 / tk 装得不全
        pytest.skip("无法创建 Tk 根窗口: %s" % e)
    r.withdraw()                                # 不在桌面上闪窗口
    yield r
    r.destroy()


def _widgets(win, cls):
    """自己递归找控件类 —— ScrolledText 本身就是个 Frame, 层级与索引都猜不得。"""
    out, stack = [], list(win.winfo_children())
    while stack:
        w = stack.pop()
        if w.winfo_class() == cls:
            out.append(w)
        stack.extend(w.winfo_children())
    return out


def test_show_help_renders_the_whole_text(root):
    """弹窗必须装得下全文(可滚动), 而不是像 messagebox 那样只露出开头几行。"""
    text = _text()
    win = show_help(root, text)
    try:
        viewers = [v for v in _widgets(win, "Text") if v.cget("state") == "disabled"]
        assert viewers, "没找到只读的正文区"
        assert viewers[0].get("1.0", "end-1c").strip() == text.strip()
        assert win.title() == "使用说明"
    finally:
        win.destroy()


def test_show_help_button_wiring(root):
    """底部两个按钮: 复制全文 + 打开完整说明(没给回调时不该硬塞一个)。"""
    clicked = []
    win = show_help(root, "正文", on_open_manual=lambda: clicked.append(1))
    try:
        buttons = _widgets(win, "Button")
        labels = [b.cget("text") for b in buttons]
        assert "复制全文" in labels and "打开完整使用说明" in labels and "关闭" in labels
        for b in buttons:
            if b.cget("text") == "打开完整使用说明":
                b.invoke()
        assert clicked == [1]
    finally:
        win.destroy()


def test_show_help_without_manual_callback(root):
    win = show_help(root, "正文")
    try:
        labels = [b.cget("text") for b in _widgets(win, "Button")]
        assert "打开完整使用说明" not in labels
    finally:
        win.destroy()


def test_action_help_wires_the_built_text(monkeypatch):
    """按钮点开的就是 build_help_text 的那份文案, 而且带着当前版本号。"""
    seen = {}
    monkeypatch.setattr(main_gui, "show_help",
                        lambda root, text, on_open_manual=None: seen.update(
                            text=text, cb=on_open_manual))

    class _App:
        root = object()
        opened = 0

        def _open_manual_doc(self):
            _App.opened += 1

    app = _App()
    LiushuiApp._action_help(app)
    assert "【怎么开始】" in seen["text"]
    assert "v%s" % main_gui.APP_VERSION in seen["text"]
    seen["cb"]()
    assert _App.opened == 1, "底部按钮要接到打开文档那一步"


def test_open_manual_doc_reports_instead_of_going_silent(tmp_path, monkeypatch):
    """《使用说明.md》不在或打不开时必须写一行日志, 不能点了没反应。"""
    logged = []
    app = types.SimpleNamespace(_append_log=logged.append)
    monkeypatch.setattr(main_gui.workspace, "ROOT_DIR", str(tmp_path))   # 这里没有说明文档
    LiushuiApp._open_manual_doc(app)
    assert logged and "没能打开完整使用说明" in logged[0]
    assert "使用说明.md" in logged[0]
