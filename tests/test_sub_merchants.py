"""子商户清单: 存储、清洗、以及"页面上读回的是不是我们要的那一家"的比对。

比对这一半是这次改动里唯一"错了就会把 A 的账单记到 B 名下"的地方, 所以判据要钉死:
归一化只用于比对(用户录的原文才是目录名), 短号必须**独立出现**才算命中 ——
"8234" 匹配 "8234000540" 是绝对不许发生的。
"""
import io
import json
import os

import pytest

import core.submerchants as sm


# ===== 归一化与比对 =====

@pytest.mark.parametrize("text,expected", [
    ("商户号：8234000540", "8234000540"),
    ("８２３４", "8234"),                      # 全角数字必须落成对应半角数字, 不是别的数字
    ("  8234-000540 ", "8234000540"),
    ("子商户（华东）", "华东"),
    ("商户名称 旗舰店A", "旗舰店a"),
    ("", ""),
])
def test_normalize_strips_labels_punct_and_fullwidth(text, expected):
    assert sm.normalize(text) == expected


def test_matches_exact_after_normalize():
    assert sm.matches("8234000540", "商户号：8234000540")
    assert sm.matches("旗舰店A（华东）", " 旗舰店A 华东 ")


def test_matches_allows_standalone_containment():
    """页面上常常多带前后文字, 只要我们的值是独立出现的就算切到了那一家。"""
    assert sm.matches("8234000540", "当前商户 8234000540 已激活")
    assert sm.matches("8234000540 已激活", "8234000540")


def test_short_number_does_not_match_a_longer_one():
    """录错一个短号就想蒙混过关? 数字前后粘着别的数据不算独立出现。"""
    assert not sm.matches("8234", "8234000540")
    assert not sm.matches("8234000540", "98234000540")
    assert not sm.matches("234000", "8234000540")


def test_empty_on_either_side_is_not_a_match():
    """读回空 = 没读到, 不是"匹配"。平台没实现读回时上层会另走"未校验"那条路。"""
    assert not sm.matches("8234000540", "")
    assert not sm.matches("", "8234000540")
    assert not sm.matches("商户号:", "8234000540")   # 归一后期望值为空


# ===== 清单清洗 =====

def test_clean_list_splits_and_dedupes():
    raw = "8234000540\n8234000541、8234000540，8234000542；"
    assert sm.clean_list(raw) == ["8234000540", "8234000541", "8234000542"]


def test_clean_list_kills_path_traversal():
    """这些名字会变成 downloads/平台/商户/ 下的一层目录名。"""
    got = sm.clean_list(["..", ".", "a/../../b", r"..\..\x", "....", "_ _", "  ", "正常名"])
    assert ".." not in got and "." not in got and "...." not in got
    assert got == ["a_.._.._b", ".._.._x", "_ _", "正常名"], got


def test_entry_key_uses_the_same_sanitize_as_profile_dirs():
    from core.outputs import sanitize_name
    assert sm.entry_key("yinlian", "旗舰店/华东") == "yinlian/" + sanitize_name("旗舰店/华东")
    assert sm.entry_key("", "x") == "/x"


# ===== 存取 =====

def test_roundtrip_and_delete_by_empty(tmp_path):
    path = str(tmp_path / "sub_merchants.json")
    ok, cleaned, why = sm.save_one("yinlian", "主账号甲", "8234000540\n8234000541", path=path)
    assert ok and cleaned == ["8234000540", "8234000541"] and why == ""
    assert sm.get("yinlian", "主账号甲", path=path) == (cleaned, "")
    assert sm.count_for("yinlian", "主账号甲", path=path) == 2
    # 空清单 = 删掉这一条, 而不是留下一个空数组让界面显示"已配 0 个"
    assert sm.save_one("yinlian", "主账号甲", "", path=path)[0] is True
    assert json.load(io.open(path, encoding="utf-8")) == {}
    assert sm.get("yinlian", "主账号甲", path=path) == ([], "")


def test_saving_one_merchant_keeps_the_others(tmp_path):
    path = str(tmp_path / "sub_merchants.json")
    sm.save_one("yinlian", "甲", "A1", path=path)
    sm.save_one("yinlian", "乙", "B1", path=path)
    assert sm.get("yinlian", "甲", path=path)[0] == ["A1"]
    assert sm.get("yinlian", "乙", path=path)[0] == ["B1"]


def test_broken_file_degrades_to_empty_with_a_note(tmp_path):
    """读不出清单最多是"这一家按没配子商户处理", 不能把整批账单挡掉。"""
    path = tmp_path / "sub_merchants.json"
    path.write_text("{这不是 json", encoding="utf-8")
    store, note = sm.load(path=str(path))
    assert store == {} and "读不出来" in note
    path.write_text('["一个数组"]', encoding="utf-8")
    store, note = sm.load(path=str(path))
    assert store == {} and "格式不对" in note
    # get 把同一句话原样带出去: 界面/日志要能说出"为什么当成没配", 不能静默变空
    subs, got_note = sm.get("yinlian", "甲", path=str(path))
    assert subs == [] and "格式不对" in got_note
    assert sm.load(path=str(tmp_path / "不存在.json")) == ({}, "")


def test_save_failure_reports_why(tmp_path, monkeypatch):
    monkeypatch.setattr("core.config.write_json_atomic",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("磁盘满了")))
    ok, cleaned, why = sm.save_one("yinlian", "甲", "A1", path=str(tmp_path / "x.json"))
    assert ok is False and cleaned == ["A1"] and "磁盘满了" in why
