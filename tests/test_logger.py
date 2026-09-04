from core import logger as lm


def test_warning_line_contains_level_tag():
    got = []
    line = lm.log("disk full", level="warning", callback=got.append)
    assert "[WARN]" in line
    assert "[WARN]" in got[0]


def test_error_line_contains_level_tag():
    got = []
    lm.log("boom", level="error", callback=got.append)
    assert "[ERROR]" in got[0]


def test_info_line_has_no_tag():
    got = []
    lm.log("plain", level="info", callback=got.append)
    assert "[WARN]" not in got[0] and "[ERROR]" not in got[0]