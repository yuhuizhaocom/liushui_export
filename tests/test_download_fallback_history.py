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
