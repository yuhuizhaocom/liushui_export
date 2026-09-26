"""core/outputs.py: 导出文件汇总与目录名清理。

这些逻辑原来长在 LiushuiApp 里, 只有开着 GUI 才跑得动, 所以一直没有测试; 搬出来
后测试用例照着原实现的行为写, 保证"搬家"没改行为。
"""
import os

from core.outputs import (copy_to_summary_dir, find_output_files, sanitize_name,
                          summary_name, task_folder)


def _write(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def test_finds_new_structure_files(tmp_path):
    base = str(tmp_path)
    good = _write(os.path.join(base, "有赞", "旗舰店A", task_folder("2026-09-01", "2026-09-02"), "流水.csv"))
    partial = _write(os.path.join(base, "有赞", "旗舰店A", task_folder("2026-09-01", "2026-09-02"), "流水.csv.crdownload"))
    other_day = _write(os.path.join(base, "有赞", "旗舰店A", task_folder("2026-08-01", "2026-08-02"), "旧的.csv"))
    found = find_output_files(base, ["有赞"], "2026-09-01", "2026-09-02")
    assert found == [good]                      # 未下完的、别的日期区间的都不算


def test_finds_legacy_structure_and_dedups(tmp_path):
    """旧结构 平台/日期/文件 与新结构同时命中时, 同一个文件只留一份。"""
    base = str(tmp_path)
    legacy = _write(os.path.join(base, "天猫", task_folder("2026-09-01", "2026-09-02"), "a.csv"))
    found = find_output_files(base, ["天猫"], "2026-09-01", "2026-09-02")
    assert found == [legacy]
    assert len(found) == len(set(found))


def test_unknown_platform_dir_is_ignored(tmp_path):
    base = str(tmp_path)
    _write(os.path.join(base, "没这个平台", "商户", "2026-09-01_2026-09-02", "x.csv"))
    assert find_output_files(base, ["有赞"], "2026-09-01", "2026-09-02") == []


def test_copy_returns_summary_dir_and_logs(tmp_path):
    base = str(tmp_path)
    src = _write(os.path.join(base, "有赞", "旗舰店A", "2026-09-01_2026-09-02", "流水.csv"), "1,2,3")
    lines = []
    target = copy_to_summary_dir(base, ["有赞"], "2026-09-01", "2026-09-02",
                                 log=lines.append, now=None)
    assert target and os.path.basename(target).startswith("2026-09-01_2026-09-02_")
    copied = os.path.join(target, "流水.csv")
    assert os.path.isfile(copied)
    assert open(copied, encoding="utf-8").read() == "1,2,3"
    assert os.path.isfile(src)                       # 是复制不是移动, 原文件留着
    assert any("已将 1 个导出文件" in line for line in lines)


def test_copy_returns_none_when_nothing_to_summarize(tmp_path):
    lines = []
    assert copy_to_summary_dir(str(tmp_path), ["有赞"], "2026-09-01", "2026-09-02",
                               log=lines.append) is None
    assert any("未找到可汇总的导出文件" in line for line in lines)


def test_copy_skips_when_dates_missing(tmp_path):
    assert copy_to_summary_dir(str(tmp_path), ["有赞"], "", "", log=lambda m: None) is None


def test_log_is_optional(tmp_path):
    assert copy_to_summary_dir(str(tmp_path), ["有赞"], "2026-09-01", "2026-09-02") is None


def test_sanitize_name_strips_path_traversal():
    """点号不在清理范围内, 但 / \\ 会被换成 _, 所以拼不出穿越路径。"""
    assert sanitize_name("../../etc/passwd") == ".._.._etc_passwd"
    assert os.sep not in sanitize_name("../../etc/passwd")
    assert sanitize_name('店铺:A/B') == "店铺_A_B"


def test_sanitize_name_fallbacks_differ_by_caller():
    assert sanitize_name("   ") == ""                  # 商户名: 空串才能提示重填
    assert sanitize_name("   ", fallback="default") == "default"   # profile 目录


# ===== 子商户那一层 =====

def test_finds_files_under_a_sub_merchant_dir(tmp_path):
    """平台/商户/子商户/日期 是加了子商户之后的形状, 汇总必须找得到。"""
    base = str(tmp_path)
    folder = task_folder("2026-09-01", "2026-09-02")
    good = _write(os.path.join(base, "银联", "主账号甲", "8234000540", folder, "对账单.csv"))
    other = _write(os.path.join(base, "银联", "主账号甲", "8234000541", folder, "对账单.csv"))
    _write(os.path.join(base, "银联", "主账号甲", "8234000542", "2026-08-01_2026-08-02", "别的区间.csv"))
    assert find_output_files(base, ["银联"], "2026-09-01", "2026-09-02") == [good, other]


def test_does_not_dive_into_the_history_dir(tmp_path):
    """日期目录里的 历史/ 是同一任务的旧版本, 不能再被当成另一份成品列出来。"""
    base = str(tmp_path)
    folder = task_folder("2026-09-01", "2026-09-02")
    fresh = _write(os.path.join(base, "银联", "甲", folder, "对账单.csv"))
    old = _write(os.path.join(base, "银联", "甲", folder, "历史", "20260901_101010_对账单.csv"))
    assert find_output_files(base, ["银联"], "2026-09-01", "2026-09-02") == [fresh]
    assert old not in find_output_files(base, ["银联"], "2026-09-01", "2026-09-02")


def test_search_stops_two_levels_below_the_platform(tmp_path):
    """层数封顶在 平台/商户/子商户/日期; 再深的同名目录不该被当成成品。"""
    base = str(tmp_path)
    folder = task_folder("2026-09-01", "2026-09-02")
    deep = _write(os.path.join(base, "银联", "甲", "乙", "丙", folder, "深处.csv"))
    assert find_output_files(base, ["银联"], "2026-09-01", "2026-09-02") == []
    assert os.path.isfile(deep)


def test_summary_dir_does_not_drop_a_same_named_file(tmp_path):
    """两个子商户的原始文件名一模一样时, 汇总目录里不能默默覆盖掉一份。

    统一命名模式下文件名自带商户段, 撞不上; 会撞的是"原始文件名"模式。
    """
    base = str(tmp_path)
    folder = task_folder("2026-09-01", "2026-09-02")
    a = _write(os.path.join(base, "银联", "甲", "8234000540", folder, "交易明细.xlsx"), "A")
    b = _write(os.path.join(base, "银联", "甲", "8234000541", folder, "交易明细.xlsx"), "B")
    target = copy_to_summary_dir(base, ["银联"], "2026-09-01", "2026-09-02")
    names = sorted(os.listdir(target))
    assert len(names) == 2 and os.path.isfile(os.path.join(target, "交易明细.xlsx")), names
    copied = {open(os.path.join(target, n), encoding="utf-8").read() for n in names}
    assert copied == {"A", "B"}, (names, a, b)


def test_summary_name_prefers_the_owner_segment():
    """撞名时加的前缀是"商户/子商户"那一级, 不是日期目录(前缀成一串日期等于没加)。"""
    day = task_folder("2026-09-01", "2026-09-02")
    used = set()
    top = summary_name("/x/银联/主账号甲/8234000540/%s/交易明细.xlsx" % day, used)
    assert top == "交易明细.xlsx"
    again = summary_name("/x/银联/主账号甲/8234000541/%s/交易明细.xlsx" % day, used)
    assert again.startswith("8234000541_交易明细.xlsx"), again
    # 老结构(平台/商户/日期)也一样: 前缀取商户
    used = {"流水.csv"}
    assert summary_name("/x/有赞/旗舰店A/%s/流水.csv" % day, used) == "旗舰店A_流水.csv"

