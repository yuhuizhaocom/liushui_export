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


def test_partial_download_is_not_carried_over(mgr):
    """回归: 归位那一路以前不过滤中间态后缀, 于是会去搬浏览器还在写的文件 ——
    Windows 上必然失败, 而失败会被外层 except 吞掉、整轮归位就此中断,
    结果连本该搬走的残留文件也留在根目录。"""
    b, root = mgr
    partial = _touch(root, UUID + ".crdownload", "半截文件")
    _touch(root, UUID, "订单号,金额\nOLD,1.00\n")
    b._carry_over_orphans()
    assert os.path.isfile(partial), "还在下的半成品不该搬"
    assert os.path.isfile(os.path.join(root, b.ORPHAN_DIR_NAME, UUID)), "下完的残留要搬走"


def test_one_locked_orphan_does_not_abort_the_round(mgr, monkeypatch):
    b, root = mgr
    logs = []
    b.log_callback = logs.append
    stuck = _touch(root, "aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa", "x")
    movable = _touch(root, "bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb", "y")
    real_move = bm.shutil.move

    def move(src, dst):
        if src == stuck:
            raise OSError("另一个程序正在使用此文件")
        return real_move(src, dst)

    monkeypatch.setattr(bm.shutil, "move", move)
    b._carry_over_orphans()
    assert os.path.isfile(stuck)
    assert os.path.isfile(os.path.join(root, b.ORPHAN_DIR_NAME, os.path.basename(movable))), \
        "搬不动那一个不能连累其它残留文件"
    assert any("留到下次" in line for line in logs)
    assert any("没搬动" in line for line in logs)


def test_root_fallback_archives_exactly_once(mgr, monkeypatch):
    """回归: 兜底那一路(_wait_root_download)以前自己归档一次, `_accept_download`
    又归档一次。`unified` 命名幂等所以看不出来, 保留原文件名那档会多叠一层商户前缀。
    """
    import core.config as cfg
    b, root = mgr
    monkeypatch.setattr(cfg, "load_settings", lambda: {"download_name_mode": "original"})
    src = _touch(root, "流水明细.csv", "订单号,金额\nA1,10.00\nA2,20.00\n", age=3)
    b._dl_capture_on = True
    b._dl_capture_t0 = time.time() - 10
    found = b._wait_root_download(root, time.time() + 1)
    assert found == src, "只该把根目录里的原始文件交出去, 归档留给 _accept_download"
    final = b._accept_download(found)
    task_dir = b._task_base_dir()
    assert os.path.dirname(final) == task_dir
    assert os.path.basename(final) == "旗舰店A_流水明细.csv"
    assert not any("旗舰店A_旗舰店A" in f for f in os.listdir(task_dir))


def test_zip_is_a_legitimate_statement(mgr):
    """微信支付"账单打包完成"给的是压缩包: 现在扩展名原样保留, 必须照样认。"""
    import zipfile
    b, root = mgr
    p = os.path.join(root, "微信支付_旗舰店A_2026-09-01_2026-09-02_资金账单.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("明细.csv", "订单号,金额\n" + "A1,10.00\n" * 100)
    assert b._validate_download(p) is True


def test_extensionless_archive_is_still_accepted(mgr):
    """没扩展名但内容确实是 zip: 不能因为"名字看不出来"就把能用的账单判成失败。"""
    import zipfile
    b, root = mgr
    p = os.path.join(root, "微信支付_旗舰店A_2026-09-01_2026-09-02")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("明细.csv", "订单号,金额\n" + "A1,10.00\n" * 100)
    assert b._validate_download(p) is True


def test_pdf_and_random_binary_are_rejected(mgr):
    """回归: 以前不认识的后缀会被强行改成 .xlsx, "内容不是 PK"那条顺带把 PDF 挡住。
    扩展名保留后这道门得自己站, 否则一份 PDF 也会当成成品交出去。"""
    b, root = mgr
    pdf = _touch(root, "微信支付_旗舰店A_2026-09-01_2026-09-02_回单.pdf",
                 b"%PDF-1.7" + b"x" * 3000)
    assert b._validate_download(pdf) is False
    blob = _touch(root, "微信支付_旗舰店A_2026-09-01_2026-09-02_未知", b"\x7fELF" + b"y" * 3000)
    assert b._validate_download(blob) is False


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


CSV_WITH_KEYWORD_CELLS = (
    "订单号,交易状态,金额,备注\n"
    "A001,支付成功,120.00,\n"
    "A002,退款,-30.00,客户申请操作失败,已线下处理\n"
    "A003,支付成功,88.00,请登录后台查看明细\n")


def test_valid_statement_with_error_words_in_cells_passes(mgr):
    """回归: 备注/状态列里出现"操作失败""请登录"是真账单, 以前整文件子串命中就把
    文件判无效并删除, 用户看到"下载成功但文件没了"。"""
    b, root = mgr
    p = _touch(root, "有赞_流水.csv", CSV_WITH_KEYWORD_CELLS)
    assert b._validate_download(p) is True


def test_html_error_page_still_rejected(mgr):
    b, root = mgr
    p = _touch(root, "错误页.csv", "<html><body>系统繁忙,请稍后重试</body></html>" * 40)
    assert b._validate_download(p) is False


# 真实登录页是带换行的 HTML; 上面那条恰好是单行, 数出 0 行才被文案门挡住, 测不到这个洞
LOGIN_PAGE_CSV = ("<html>\n<head><title>商户登录</title></head>\n<body>\n"
                  "<script>location.href='/newlogin'</script>\n"
                  "<p>您的会话已过期,请重新登录</p>\n</body>\n</html>\n")


def test_multiline_html_named_csv_is_rejected(mgr):
    """回归: 会话过期时后台不把登录页返回成 302, 而是 200 + Content-Disposition: xxx.csv。
    按逗号数行能数出好几行(HTML 标签本身就占行), 于是"先数行"那道门把登录页放行了 ——
    交出去的是一份网页, 记进 stats 的却是 success。"""
    b, root = mgr
    p = _touch(root, "有赞_旗舰店A_2026-09-01_2026-09-02_流水.csv", LOGIN_PAGE_CSV)
    assert b._count_rows(p) > 0, "先确认它确实数得出行, 否则这条测试测不到问题"
    assert b._validate_download(p) is False


def test_uppercase_markup_named_csv_is_rejected(mgr):
    """<!DOCTYPE HTML>/<HTML> 这种大写写法也要认得(关键字匹配以前大小写敏感)。"""
    b, root = mgr
    p = _touch(root, "流水.csv",
               "<!DOCTYPE HTML>\n<HTML>\n<BODY>your session is expired</BODY>\n</HTML>\n")
    assert b._validate_download(p) is False


def test_html_after_bom_and_blank_lines_is_rejected(mgr):
    b, root = mgr
    body = "﻿\n  \n<html>\n<body>" + "登录" * 500 + "</body>\n</html>\n"
    assert b._validate_download(_touch(root, "流水.txt", body)) is False


def test_keyword_matching_ignores_case(mgr):
    b, _root = mgr
    assert b._match_error_keyword("<!DOCTYPE HTML>") == "<!doctype"
    assert b._match_error_keyword("系统繁忙") == "系统繁忙"   # 中文不受 lower() 影响
    assert b._match_error_keyword("一切正常") is None
    assert b._match_error_keyword("") is None


def test_web_page_head_recognition_is_by_content(tmp_path):
    """只看文件头, 不看正文里有没有 html 字样 —— 备注里带链接的真账单不该被判死。"""
    ok = tmp_path / "真账单.csv"
    ok.write_text("订单号,金额,备注\nA,1,详情见 https://example.com/html/detail\n",
                  encoding="utf-8")
    assert BrowserManager._looks_like_web_page(str(ok)) is False
    bad = tmp_path / "登录页.csv"
    bad.write_bytes("﻿\n\n<HTML>\n<BODY>x</BODY>\n</HTML>\n".encode("utf-8"))
    assert BrowserManager._looks_like_web_page(str(bad)) is True
    empty = tmp_path / "空.csv"
    empty.write_bytes(b"")
    assert BrowserManager._looks_like_web_page(str(empty)) is False
    assert BrowserManager._looks_like_web_page(str(tmp_path / "不存在.csv")) is False


def test_excel_xml_workbook_is_not_a_web_page(mgr):
    """Excel 2003 的 XML 表格存成 .xls 真有人用来发账单: <?xml 开头不能一起打死。"""
    b, root = mgr
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<x:Workbook xmlns:x="urn:schemas-microsoft-com:office:excel">\n'
            + '<x:Row><x:Cell><x:Data>10.00</x:Data></x:Cell></x:Row>\n' * 120
            + '</x:Workbook>\n')
    p = _touch(root, "银联对账单.xls", body)
    assert BrowserManager._looks_like_web_page(p) is False
    assert b._validate_download(p) is True


def test_header_only_table_still_rejected(mgr):
    b, root = mgr
    assert b._validate_download(_touch(root, "空表.csv", "订单号,金额\n")) is False


def test_xlsx_with_rows_and_keyword_cells_passes(mgr):
    import zipfile
    b, root = mgr
    p = os.path.join(root, "快手_账单.xlsx")
    rows = "".join('<row r="%d"><c t="inlineStr"><is><t>操作失败</t></is></c></row>'
                   for i in range(1, 40))
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("xl/worksheets/sheet1.xml", "<sheetData>%s</sheetData>" % rows)
        zf.writestr("pad", "x" * 2000)
    assert b._validate_download(p) is True


def test_legacy_xls_still_accepted_when_rows_unparseable(mgr):
    """.xls 读不出行数时维持原有的宽松判定(不能因为新增顺序而开始误拒)。"""
    b, root = mgr
    p = _touch(root, "银联对账单.xls", b"\xd0\xcf\x11\xe0" + b"x" * 2000)
    assert b._count_rows(p) is None
    assert b._validate_download(p) is True


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
