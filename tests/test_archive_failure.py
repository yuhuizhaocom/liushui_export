"""同名重导时的"腾位"这一步: 归档没成不许删旧件, 删不掉不许把异常冒出去。

`_finalize_download` 里那段以前是:

    try: shutil.copy2(final_path, his_path)
    except Exception: pass          # ← 归档失败静默吞掉
    try: os.remove(final_path)      # ← 然后照样把唯一的原件删了
    except Exception: shutil.copy2(path, final_path)   # ← 这句自己没包 try

实测两个形状: ① 注入 copy2 抛错(深工作空间+长原始名超 260 字符、磁盘满、杀软拦都算)
→ 上一版内容在全树里再也搜不到, 日志一个字都没有, 而用户以为 历史/ 有备份;
② 顶层旧件被 Excel 占用 → PermissionError 直接冒出 wait_download, `_dl_capture_on`
卡在 True、本次成品留在 临时/, 下一次 begin_wait_download 的 rmtree 把它删掉 ——
明明下成功却报 failed, 越重试越丢。
"""
import os

import pytest

import core.browser as bm
from core.browser import BrowserManager

GOOD = "订单号,金额\nA001,1.00\nA002,2.00\n"
NEW = "订单号,金额\nB001,9.00\nB002,8.00\n"


@pytest.fixture()
def mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "DOWNLOAD_DIR", str(tmp_path))
    b = BrowserManager(headless=True)
    logs = []
    b.log_callback = lambda msg, level="info": logs.append(f"{level}|{msg}")
    b.set_export_context("有赞", "2026-09-01", "2026-09-02", "旗舰店A")
    return b, str(tmp_path), logs


def _put(root, name, text):
    p = os.path.join(root, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _files_with(root, text):
    hits = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(dp, f)
            try:
                if open(p, encoding="utf-8", errors="ignore").read() == text:
                    hits.append(os.path.relpath(p, root))
            except OSError:
                continue
    return hits


def test_archive_failure_keeps_the_previous_copy(mgr):
    """归档进 历史/ 失败 → 旧件必须留下, 本次成品换个名字落, 并且要说出口。"""
    b, root, logs = mgr
    top = b._finalize_download(_put(root, "bill.csv", GOOD))
    assert _files_with(root, GOOD) == [os.path.relpath(top, root)]

    real_copy2 = bm.shutil.copy2
    bm.shutil.copy2 = lambda *a, **k: (_ for _ in ()).throw(OSError("文件名太长(>260)"))
    try:
        got = b._accept_download(_put(root, "bill.csv", NEW))
    finally:
        bm.shutil.copy2 = real_copy2

    assert got and os.path.isfile(got), "本次成品不该因为归档失败就丢掉"
    assert _files_with(root, GOOD), "上一版必须还在盘上"
    assert BrowserManager.KEEP_BOTH_MARK in os.path.basename(got), got
    assert any("没能复制进 历史/" in l for l in logs), logs


def test_locked_old_file_does_not_raise_out_of_wait_download(mgr):
    """旧件被 Excel 占用: 异常不许冒出 wait_download, 两份文件都得在。"""
    b, root, logs = mgr
    top = b._finalize_download(_put(root, "bill.csv", GOOD))
    real_remove = bm.os.remove

    def rmp(path):
        if os.path.abspath(path) == os.path.abspath(top):
            raise PermissionError(13, "另一个程序正在使用此文件")
        return real_remove(path)

    bm.os.remove = rmp
    try:
        b.begin_wait_download()
        got = b._accept_download(_put(root, "bill.csv", NEW))
    finally:
        bm.os.remove = real_remove
    assert got and os.path.isfile(got), "被占用也不该把本次成品弄没"
    assert _files_with(root, GOOD), "旧件被占用时更不该被删掉"
    assert any("删不掉" in l for l in logs), logs


def test_any_finalize_accident_becomes_not_claimed(mgr, monkeypatch):
    """文件系统再怎么闹, _accept_download 只许返回 None, 不许把异常抛进导出流程。"""
    b, root, logs = mgr

    def boom(path):
        raise OSError("设备没准备好")
    monkeypatch.setattr(b, "_finalize_download", boom)
    assert b._accept_download(_put(root, "bill.csv", GOOD)) is None
    assert any("处理下载文件出错" in l for l in logs), logs


def test_happy_path_still_archives_and_replaces(mgr):
    """反向钉: 正常情形照旧 —— 旧版进 历史/, 顶层换成本次这份, 不多留"原件保留"。"""
    b, root, logs = mgr
    top = b._finalize_download(_put(root, "bill.csv", GOOD))
    got = b._accept_download(_put(root, "bill.csv", NEW))
    assert got == top, (got, top)
    assert open(got, encoding="utf-8").read() == NEW
    his = os.path.join(os.path.dirname(top), "历史")
    assert os.listdir(his), "旧版应能在 历史/ 里找到"
    assert open(os.path.join(his, os.listdir(his)[0]), encoding="utf-8").read() == GOOD
    assert not any(BrowserManager.KEEP_BOTH_MARK in f for f in os.listdir(os.path.dirname(top)))
