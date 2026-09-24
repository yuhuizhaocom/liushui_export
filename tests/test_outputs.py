"""core/outputs.py: 导出文件汇总与目录名清理。

这些逻辑原来长在 LiushuiApp 里, 只有开着 GUI 才跑得动, 所以一直没有测试; 搬出来
后测试用例照着原实现的行为写, 保证"搬家"没改行为。
"""
import os

from core.outputs import (copy_to_summary_dir, find_output_files, sanitize_name,
                          task_folder)


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
