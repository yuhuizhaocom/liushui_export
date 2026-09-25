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
