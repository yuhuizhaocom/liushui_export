"""兜底扫描(路 2)的候选口径: 不许把本轮自己归档出来的"上一版"当成本次下载。

复现的是同名重导这一最常见的场合: 平台每次给的原始文件名相同 → _finalize_download
在校验**之前**就把顶层旧版复制进 平台/商户/区间/历史/。本轮拿到的若是会话过期后
的登录页, 校验失败、文件被删, 而紧接着同一个 while 循环按"与开始快照的差集"扫描
整棵 downloads —— 刚躺进 历史/ 的那一份就成了"新出现的文件": 被搬回顶层、通过全部
校验、日志"下载完成并校验通过"、stats 记 success, 交出去的却是上一版的账单。
"""
import os
import time

import pytest

import core.browser as bm
from core.browser import BrowserManager

GOOD = "订单号,金额\nA001,1.00\nA002,2.00\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 2000      # 文件头是真的 PNG 签名
LOGIN_PAGE = "<!DOCTYPE html>\n<html>\n<body>请登录后台</body>\n</html>\n"
NEW = "订单号,金额\nB001,9.00\n"


@pytest.fixture()
def mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "DOWNLOAD_DIR", str(tmp_path))
    b = BrowserManager(headless=True)             # 不 start(), 不碰浏览器
    b.set_export_context("有赞", "2026-09-01", "2026-09-02", "旗舰店A")
    return b, str(tmp_path)


def _write(root, name, text):
    p = os.path.join(root, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _one_fallback_round(b, root, incoming_text):
    """跑一遍 wait_download 里"路 1 没成 → 路 2 扫兜底"的那一轮(不靠墙钟睡眠)。

    与生产的差别只有: 本轮的文件由测试直接丢在下载根目录, 而不是等浏览器写。
    返回 (路 2 认领到的路径或 None, 快照差集里的候选数)。
    """
    b.begin_wait_download()
    known = b._dir_snapshot(root)          # = wait_download 开头那一句
    fp = _write(root, "bill.csv", incoming_text)
    if b._accept_download(fp):             # 路 1: 归档 + 校验
        raise AssertionError("这份内容居然通过了校验, 用例前提不成立")
    claimed = None
    for cand in b._new_download_candidates(root, known):
        if b._is_stable(cand):
            claimed = b._accept_download(cand)
            if claimed:
                break
    return claimed, len(b._new_download_candidates(root, known))


def test_history_copy_is_not_claimed_as_this_round(mgr):
    """回归: 本轮校验失败后, 兜底扫描不许认领 历史/ 里的上一版。"""
    b, root = mgr
    prev_top = b._finalize_download(_write(root, "bill.csv", GOOD))
    assert os.path.isfile(prev_top)

    claimed, n_cands = _one_fallback_round(b, root, LOGIN_PAGE)

    assert claimed is None, f"本轮没拿到真账单, 却把某份文件当成品交出去了: {claimed}"
    assert n_cands == 0, f"兜底扫描还看见了 {n_cands} 个候选, 都是本轮自己造出来的"
    # 上一版必须还在(它现在躺在 历史/ 里), 而不是被搬回顶层冒充本次
    archived = os.path.join(os.path.dirname(prev_top), "历史")
    assert os.path.isdir(archived) and os.listdir(archived), "上一版该留在 历史/ 里备查"
    kept = os.path.join(archived, os.listdir(archived)[0])
    assert open(kept, encoding="utf-8").read() == GOOD


def test_genuine_new_download_is_still_claimed(mgr):
    """反向钉: 收窄候选口径不能把真下载挡掉(附加检查不许拦住业务)。"""
    b, root = mgr
    b._finalize_download(_write(root, "bill.csv", GOOD))     # 顶层先有一份上一版
    b.begin_wait_download()
    known = b._dir_snapshot(root)
    _write(root, "statement.csv", NEW)                       # 本轮浏览器落地的真账单
    cands = b._new_download_candidates(root, known)
    assert [os.path.basename(p) for p in cands] == ["statement.csv"], cands
    got = b._accept_download(cands[0])
    assert got and os.path.isfile(got)
    assert open(got, encoding="utf-8").read() == NEW, "认领到的必须是本轮这份"


@pytest.mark.parametrize("lag,expected", [
    (0.5, True),      # Windows 实测: 先取 time.time() 再写文件, mtime 常常**更早**(212/400 次)
    (2.5, False),     # 本轮开始之前就有了的文件(比如上一版), 不该当候选
])
def test_mtime_filter_has_slack_so_a_real_download_is_never_dropped(mgr, lag, expected):
    """时刻那道过滤必须带富余量, 方向是"宁可放行交给校验", 不能反过来挡掉业务。

    这条用例是踩出来的: 没有富余量时 `_new_download_candidates` 会**间歇性**看不见
    本轮刚落地的那份文件(mtime 比 t0 早几毫秒就被判成"上一轮残留"), 表现为兜底失灵。
    """
    b, root = mgr
    b.begin_wait_download()
    known = b._dir_snapshot(root)
    fp = _write(root, "statement.csv", NEW)
    stale = b._dl_capture_t0 - lag
    os.utime(fp, (stale, stale))
    names = [os.path.basename(p) for p in b._new_download_candidates(root, known)]
    assert ("statement.csv" in names) is expected, names


def test_unreadable_mtime_does_not_hide_the_candidate(mgr, monkeypatch):
    """读不到时间(文件正被写/被占用)时按"不挡"处理, 交给后面的校验决定。"""
    b, root = mgr
    b.begin_wait_download()
    known = b._dir_snapshot(root)
    _write(root, "statement.csv", NEW)
    monkeypatch.setattr(BrowserManager, "_mtime_of", staticmethod(lambda p: 0.0))
    assert [os.path.basename(p) for p in b._new_download_candidates(root, known)] == \
        ["statement.csv"]


def test_screenshot_written_this_round_is_left_alone(mgr):
    """回归: 本轮写的取证截图以前会被兜底"认领"走 —— 改名挪出 snapshots、判成图片、删掉。

    `snapshot()` 在建目录失败时还会退到 downloads **根目录**, 那正是最早那次
    "一张页面截图被改名成 .xlsx 报 success"事故的位置, 所以根目录也要按图片跳。
    """
    b, root = mgr
    b.begin_wait_download()
    known = b._dir_snapshot(root)

    class _Page:
        def screenshot(self, path=None, **kw):
            with open(path, "wb") as f:
                f.write(PNG_BYTES)

    b.page = _Page()
    shot = b.snapshot("FAIL_下载现场")
    assert shot and os.path.isfile(shot)
    assert b._new_download_candidates(root, known) == [], "本轮自己截的图不该出现在候选里"
    assert os.path.isfile(shot), "取证截图必须留在原地"

    root_shot = os.path.join(root, "manual_20260926_233000.png")
    with open(root_shot, "wb") as f:
        f.write(PNG_BYTES)
    assert b._new_download_candidates(root, known) == [], "根目录的 PNG 也不该被认领"
    assert os.path.isfile(root_shot)


def test_exclusion_list_covers_every_self_written_dir(mgr):
    """结构钉: downloads 树里凡是本轮自己建的目录, 名字必须来自那四个类常量。

    这条防的是"以后又加一层自写目录、忘了同步排除名单" —— 那会原样复刻 54e1002
    认回上一版、这条提交前认走截图这两次事故。
    """
    names = set(BrowserManager._INTERNAL_DOWNLOAD_DIRS)
    assert names == {BrowserManager.TEMP_DIR_NAME, BrowserManager.ARCHIVE_DIR_NAME,
                     BrowserManager.ORPHAN_DIR_NAME, BrowserManager.SNAPSHOT_DIR_NAME}
    b, root = mgr
    assert os.path.basename(b._task_tmp_dir()) in names
    assert os.path.basename(os.path.join(b._task_base_dir(),
                                         BrowserManager.SNAPSHOT_DIR_NAME)) in names


def test_root_scan_uses_the_same_slack_as_the_fallback_scan(mgr):
    """路 1(`_find_new_candidate`)与路 2 必须用同一个富余量。

    不齐的后果: 事件已经触发但没能移动进临时目录时, 路 1 对这个文件"装作看不见"
    (mtime 比 t0 早 0.x 秒), 于是 `_wait_root_download` 一路找到 deadline 才由路 2 兜住。
    """
    b, root = mgr
    b.begin_wait_download()
    fresh = _write(root, "statement.csv", NEW)
    now = time.time()
    b._dl_capture_t0 = now - 30
    os.utime(fresh, (now - 30.5, now - 30.5))       # Windows 实测会有的形状: 比 t0 还早
    assert b._find_new_candidate(root) == fresh, "路 1 不该因为 0.5 秒的时钟抖动漏掉它"
    assert [os.path.basename(p) for p in b._new_download_candidates(root, set())] == \
        ["statement.csv"], "路 2 的判定必须与路 1 一致"


def test_a_file_from_a_previous_round_is_not_a_candidate(mgr):
    """带富余量不等于放弃这条判据: 上一轮的文件两条路都不许认。"""
    b, root = mgr
    b.begin_wait_download()
    stale = _write(root, "old.csv", GOOD)
    now = time.time()
    os.utime(stale, (now - 300, now - 300))
    b._dl_capture_t0 = now
    assert b._find_new_candidate(root) is None
    assert b._new_download_candidates(root, set()) == []


def test_settle_window_is_not_the_same_thing_as_the_slack(mgr):
    """`_WRITE_SETTLE_S`(还在不在落盘)和富余量(属不属于本轮)是两件事, 不许合并。"""
    b, root = mgr
    b.begin_wait_download()
    hot = _write(root, "hot.csv", GOOD)
    b._dl_capture_t0 = time.time() - 30             # 时刻上属于本轮
    assert b._find_new_candidate(root) is None, "刚改过的文件要视为还在写"
    os.utime(hot, (time.time() - 5, time.time() - 5))
    assert b._find_new_candidate(root) == hot, "写完 5 秒后就该被认出来"


@pytest.mark.parametrize("rel,expected", [
    (os.path.join("有赞", "旗舰店A", "2026-09-01_2026-09-02", "历史", "a.csv"), True),
    (os.path.join("有赞", "旗舰店A", "2026-09-01_2026-09-02", "临时", "a.csv"), True),
    (os.path.join("待确认", "a.csv"), True),
    (os.path.join("有赞", "旗舰店A", "历史商户", "2026-09-01_2026-09-02", "a.csv"), False),
    ("历史.csv", False),
])
def test_internal_dir_detection(mgr, rel, expected):
    """内部目录按**路径分段**认, 别把叫"历史商户"的商户目录、"历史.csv"这种名字误伤。"""
    b, root = mgr
    assert bm.BrowserManager._is_internal_download_path(root, os.path.join(root, rel)) is expected
