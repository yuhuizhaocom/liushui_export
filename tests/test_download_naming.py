"""下载文件命名: 前缀(平台_商户[_子商户]_起_止) + 原始文件名, 扩展名原样保留。

以前"统一命名"是 `平台_商户_起_止.扩展名`, 且扩展名过白名单 (.xlsx/.xls/.csv/.txt),
不在表里的一律强行改成 .xlsx —— 一个 .zip 压缩包于是顶着表格的名字进归档, 而校验对
读不出行的文件是宽松放行的, 交出去的文件名、内容、图标三者不一致。这一组测试钉住
"原始名与原始后缀都必须活着"。
"""
import os
import time
import zipfile

import pytest

import core.browser as bm
import core.config as cfg
from core.browser import BrowserManager

PLATFORM, MERCHANT = "微信支付", "旗舰店A"
START, END = "2026-09-01", "2026-09-02"
UUID = "3f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"


@pytest.fixture()
def mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "DOWNLOAD_DIR", str(tmp_path))
    b = BrowserManager(headless=True)          # 不 start(), 不碰浏览器
    b.set_export_context(PLATFORM, START, END, MERCHANT)
    return b, str(tmp_path)


def _name(browser, suggested, platform=PLATFORM, start=START, end=END):
    return browser._normalize_download_name(suggested, platform, start, end)


# ===== 前缀 + 原名 + 原扩展 =====

def test_prefix_is_prepended_and_everything_original_survives(mgr):
    b, _root = mgr
    assert _name(b, "资金账单.zip") == f"{PLATFORM}_{MERCHANT}_{START}_{END}_资金账单.zip"


@pytest.mark.parametrize("suggested,expect_tail", [
    ("交易明细.csv", "交易明细.csv"),
    ("流水.CSV", "流水.CSV"),            # 大小写原样: 悄悄改成小写也是一种改后缀
    ("账单.tar.gz", "账单.tar.gz"),      # 复合扩展名不许被截断
    ("无后缀报表", "无后缀报表"),
])
def test_original_extension_is_never_rewritten(mgr, suggested, expect_tail):
    b, _root = mgr
    name = _name(b, suggested)
    assert name.endswith("_" + expect_tail), name
    assert not name.endswith(".xlsx"), "白名单外的格式曾被强行改成 .xlsx"


def test_uuid_and_empty_names_fall_back_to_prefix_only(mgr):
    """UUID / 空名没有"原始文件名"可保留 —— 这类只能给前缀, 不硬编一个扩展名骗人。"""
    b, _root = mgr
    prefix = f"{PLATFORM}_{MERCHANT}_{START}_{END}"
    assert _name(b, UUID) == prefix
    assert _name(b, UUID + ".csv") == prefix + ".csv"     # UUID 名但带扩展: 扩展留着
    assert _name(b, "") == prefix


def test_date_segment_dropped_when_range_missing(mgr):
    b, _root = mgr
    assert _name(b, "资金账单.zip", start="", end="") == f"{PLATFORM}_{MERCHANT}_资金账单.zip"


# ===== 子商户: 只是文件名里多一档 =====

def test_sub_merchant_sits_between_merchant_and_dates(mgr):
    b, _root = mgr
    b.set_export_context(PLATFORM, START, END, MERCHANT, "华东分店")
    assert _name(b, "资金账单.zip") == \
        f"{PLATFORM}_{MERCHANT}_华东分店_{START}_{END}_资金账单.zip"


def test_without_sub_merchant_name_is_unchanged(mgr):
    b, _root = mgr
    b.set_export_context(PLATFORM, START, END, MERCHANT)      # 第 5 个参数不传
    assert _name(b, "资金账单.zip").startswith(f"{PLATFORM}_{MERCHANT}_{START}")


# ===== 幂等: 同一文件被再归一次不会叠两层前缀 =====

def test_normalizing_an_already_named_file_is_idempotent(mgr):
    b, _root = mgr
    once = _name(b, "资金账单.zip")
    assert _name(b, once) == once, "第二次归档叠前缀会让汇总目录里出现两份同名文件"


def test_sub_merchant_prefix_change_does_not_stack(mgr):
    b, _root = mgr
    plain = _name(b, "资金账单.zip")
    b.set_export_context(PLATFORM, START, END, MERCHANT, "华东分店")
    assert _name(b, plain).endswith(plain), "换上下文后旧名仍被当作原始文件名保留"


# ===== 归档落盘真的用新名字 =====

def test_zip_lands_with_its_own_name_and_extension(mgr):
    b, root = mgr
    src = os.path.join(root, "资金账单.zip")
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("明细.csv", "订单号,金额\nA1,10.00\n")
    final = b._finalize_download(src)
    assert os.path.basename(final) == f"{PLATFORM}_{MERCHANT}_{START}_{END}_资金账单.zip"
    assert os.path.dirname(final) == b._task_base_dir()
    assert zipfile.is_zipfile(final)              # 内容没被动过


def test_history_archive_keeps_extension_and_adds_timestamp(mgr):
    b, root = mgr
    expected = f"{PLATFORM}_{MERCHANT}_{START}_{END}_资金账单.csv"
    old = os.path.join(b._task_base_dir(), expected)
    os.makedirs(os.path.dirname(old), exist_ok=True)
    with open(old, "w", encoding="utf-8") as f:
        f.write("订单号,金额\nOLD,1.00\n")
    t = time.time() - 86400
    os.utime(old, (t, t))
    new = os.path.join(root, "资金账单.csv")
    with open(new, "w", encoding="utf-8") as f:
        f.write("订单号,金额\nNEW,2.00\n")
    final = b._finalize_download(new)
    assert final == old                            # 顶层换成本次最新文件
    his = os.listdir(os.path.join(b._task_base_dir(), "历史"))
    assert len(his) == 1 and his[0].startswith(expected[:-4] + "_") and his[0].endswith(".csv")


def test_tricky_merchant_name_stays_one_file_name_component(mgr):
    """`sanitize_name` 只换 `<>:"/\\|?*` 与控制字符, `..` 是原样留下的 —— 真正要钉住的
    不变量是"文件名里不能再有路径分隔符", 而不是"看不见两个点"。
    商户名本身走的是 GUI 那道清洗 (`_add_merchant` 拒掉 `..`/空名), 这里只验文件名一侧。
    """
    b, root = mgr
    b.set_export_context(PLATFORM, START, END, "隔壁.店")
    name = _name(b, "资金账单.zip")
    assert "/" not in name and "\\" not in name
    assert os.path.basename(name) == name                 # 仍是单个文件名, 不是路径
    assert name.startswith(f"{PLATFORM}_隔壁.店_")
    b.set_export_context(PLATFORM, START, END, MERCHANT)
    src = os.path.join(root, "资金账单.zip")
    with open(src, "w", encoding="utf-8") as f:
        f.write("订单号,金额\nA1,10.00\n")
    final = b._finalize_download(src)
    assert os.path.normpath(os.path.dirname(final)) == os.path.normpath(b._task_base_dir())


# ===== original 档维持原状 =====

def test_original_mode_still_only_prefixes_the_merchant(mgr, monkeypatch):
    monkeypatch.setattr(cfg, "load_settings", lambda: {"download_name_mode": "original"})
    b, _root = mgr
    b.set_export_context("有赞", START, END, MERCHANT)
    assert b._normalize_download_name("交易明细.csv", "有赞", START, END) == f"{MERCHANT}_交易明细.csv"


def test_original_mode_uuid_falls_back_to_unified(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "load_settings", lambda: {"download_name_mode": "original"})
    b = BrowserManager(headless=True)
    b.set_export_context("有赞", START, END, MERCHANT)
    assert b._normalize_download_name(UUID, "有赞", START, END) == \
        f"有赞_{MERCHANT}_{START}_{END}"


# ===== 子商户那一级目录 =====

def test_task_dir_is_unchanged_without_a_sub_merchant(mgr):
    """没配子商户的商户绝不能多出一层空目录 —— 老用户的归档形状不许变。"""
    b, root = mgr
    assert b._task_base_dir() == os.path.join(root, PLATFORM, MERCHANT, f"{START}_{END}")


def test_sub_merchant_adds_exactly_one_directory_level(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "DOWNLOAD_DIR", str(tmp_path))
    b = BrowserManager(headless=True)
    b.set_export_context(PLATFORM, START, END, MERCHANT, "8234000540")
    assert b._task_base_dir() == os.path.join(str(tmp_path), PLATFORM, MERCHANT,
                                              "8234000540", f"{START}_{END}")
    # 名字和文件名两处用的是同一份清洗后的值, 不会出现"目录一个名、文件名另一个名"
    assert b._export_sub_merchant == "8234000540"
    assert b._normalize_download_name("对账单.csv", PLATFORM, START, END) == \
        f"{PLATFORM}_{MERCHANT}_8234000540_{START}_{END}_对账单.csv"


def test_sub_merchant_cannot_escape_the_merchant_dir(tmp_path, monkeypatch):
    """子商户名会直接成为一级目录名: 传进来的脏东西必须在设上下文时就被吃掉。"""
    monkeypatch.setattr(bm, "DOWNLOAD_DIR", str(tmp_path))
    b = BrowserManager(headless=True)
    b.set_export_context(PLATFORM, START, END, MERCHANT, "../../恶意的")
    base = b._task_base_dir()
    assert os.sep not in b._export_sub_merchant.replace(str(tmp_path), ""), b._export_sub_merchant
    assert base.startswith(os.path.join(str(tmp_path), PLATFORM, MERCHANT)), base
    assert ".." not in base.split(os.sep)[-3:], base
