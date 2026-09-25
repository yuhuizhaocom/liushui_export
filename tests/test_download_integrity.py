"""下载兜底与内容校验: 不能把截图/非表格当成对账单交出去。

回归的是一整条链路: screenshot() 把 PNG 写到 downloads 根目录, 而兜底扫描认领
"最新出现的文件", 归档时扩展名又被强制改成 .xlsx, 于是 _validate_download 按扩展名
看不出真相、行数解析返回 None 就放行 → 日志报 success, 交出去的是一张截图。
"""
import os
import time

import pytest

import core.browser as bm
from core.browser import BrowserManager

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 2000          # 文件头是真的 PNG 签名


@pytest.fixture()
def mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "DOWNLOAD_DIR", str(tmp_path))
    b = BrowserManager(headless=True)             # 不 start(), 不碰浏览器
    b.set_export_context("有赞", "2026-09-01", "2026-09-02", "旗舰店A")
    return b, str(tmp_path)


def _touch(root, name, data, age=3):
    p = os.path.join(root, name)
    with open(p, "wb") as f:
        f.write(data.encode("utf-8") if isinstance(data, str) else data)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def test_fallback_ignores_screenshot_but_takes_statement(mgr):
    b, root = mgr
    _touch(root, "查询结果_20260924_181000.png", PNG)
    good = _touch(root, "3f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8",
                  "订单号,金额\nA1,10.00\nA2,20.00\n")
    assert b._find_new_candidate(root) == good


def test_validate_rejects_image_even_when_named_xlsx(mgr):
    b, root = mgr
    p = _touch(root, "有赞_旗舰店A_2026-09-01_2026-09-02.xlsx", PNG)
    assert b._validate_download(p) is False


def test_validate_rejects_fake_xlsx_without_zip_signature(mgr):
    b, root = mgr
    # 内容刻意不含 <html>/错误文案, 确保拦下它的是"扩展名 .xlsx 但不是 zip"这条
    p = _touch(root, "假表格.xlsx", "订单号,金额\n" + "A1,10.00\n" * 200)
    assert b._validate_download(p) is False


def test_validate_accepts_real_csv(mgr):
    b, root = mgr
    p = _touch(root, "流水.csv", "订单号,金额\nA1,10.00\nA2,20.00\nA3,30.00\n")
    assert b._validate_download(p) is True


def test_image_detection_is_by_content_not_extension(tmp_path):
    jpg = tmp_path / "随便叫什么.txt"
    jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"1" * 100)
    assert BrowserManager._is_image_file(str(jpg)) is True
    csv = tmp_path / "真账单.png"            # 扩展名骗人也不该被认成图片
    csv.write_bytes("订单号,金额\nA,1\n".encode("utf-8"))
    assert BrowserManager._is_image_file(str(csv)) is False


def test_missing_file_is_not_an_image(tmp_path):
    assert BrowserManager._is_image_file(str(tmp_path / "不存在.png")) is False


UUID = "3f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"


def test_orphan_does_not_land_in_the_next_merchants_folder(mgr):
    """上一轮残留的 UUID 文件不能冒充下一个商户的账单。"""
    b, root = mgr
    stale = _touch(root, UUID, "订单号,金额\nOLD,1.00\n")
    logs = []
    b.log_callback = logs.append
    b.begin_wait_download()                       # 此时上下文已经是"旗舰店A"
    assert not os.path.exists(stale)
    task_dir = b._task_base_dir()
    assert not any(UUID in f for _d, _s, fs in os.walk(task_dir) for f in fs), \
        "残留文件被归进了当前商户的目录"
    pending = os.path.join(root, b.ORPHAN_DIR_NAME, UUID)
    assert os.path.isfile(pending)                # 归属不明 -> 单独放, 等人工核对
    assert any("待确认" in str(line) for line in logs)


def test_orphan_keeps_its_own_name(mgr):
    """归位路径不经过内容校验 —— 所以更不能把它改名成某个商户的账单名。"""
    b, root = mgr
    data = "\x00\x01\x02 乱码".encode("utf-8") * 50
    _touch(root, UUID, data)
    b._carry_over_orphans()
    moved = os.path.join(root, b.ORPHAN_DIR_NAME, UUID)
    assert open(moved, "rb").read() == data       # 原样保留, 连文件名都不动


def test_named_files_and_screenshots_left_alone(mgr):
    b, root = mgr
    keep = _touch(root, "查询结果_20260924_181000.png", PNG)
    other = _touch(root, "自己起的名字.csv", "a,b\n1,2\n")
    b._carry_over_orphans()
    assert os.path.isfile(keep) and os.path.isfile(other)
