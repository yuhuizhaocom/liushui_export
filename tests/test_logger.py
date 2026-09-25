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


def _write_stats(path, count, newline="\n"):
    """按 stats.jsonl 的格式造 count 条记录。"""
    with open(path, "w", encoding="utf-8", newline="") as f:
        for i in range(count):
            f.write('{"ts": "%05d", "platform": "p%d", "result": "success"}%s'
                    % (i, i % 3, newline))


def test_tail_lines_matches_plain_read(tmp_path):
    p = str(tmp_path / "stats.jsonl")
    _write_stats(p, 500)
    tail = lm._read_tail_lines(p, 20)
    whole = [l for l in open(p, encoding="utf-8").read().split("\n") if l.strip()][-20:]
    assert tail == whole


def test_tail_lines_across_tiny_blocks(tmp_path):
    """块边界是最容易出错的地方: 用极小 block 强制多轮拼接。"""
    p = str(tmp_path / "stats.jsonl")
    _write_stats(p, 137)
    for block in (1, 2, 7, 13, 64, 4096):
        assert lm._read_tail_lines(p, 30, block=block) == lm._read_tail_lines(p, 30), block


def test_tail_lines_handles_crlf_and_missing_trailing_newline(tmp_path):
    p = str(tmp_path / "crlf.jsonl")
    _write_stats(p, 40, newline="\r\n")
    assert len(lm._read_tail_lines(p, 10)) == 10
    assert lm._read_tail_lines(p, 10)[-1].endswith("}")      # 行尾 \r\n 已被剥掉

    p2 = str(tmp_path / "noeol.jsonl")
    _write_stats(p2, 5)
    raw = open(p2, encoding="utf-8").read().rstrip("\n")
    open(p2, "w", encoding="utf-8", newline="").write(raw)
    assert len(lm._read_tail_lines(p2, 10)) == 5             # 末行没有换行也要算进来


def test_load_stats_with_limit_matches_full_read(tmp_path, monkeypatch):
    p = str(tmp_path / "stats.jsonl")
    _write_stats(p, 300)
    monkeypatch.setattr(lm, "STATS_FILE", p)
    full = lm.load_stats()
    limited = lm.load_stats(limit=25)
    assert limited == full[:25]
    assert [r["ts"] for r in limited][:2] == ["00299", "00298"]   # 最新在前
    assert lm.load_stats(limit=1000) == full                 # 超过总行数也别炸


def test_load_stats_skips_corrupt_lines_with_limit(tmp_path, monkeypatch):
    p = str(tmp_path / "stats.jsonl")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write('{"ts": "00001", "result": "success"}\n')
        f.write('½Ê{ not json\n')
        f.write('{"ts": "00002", "result": "manual"}\n')
    monkeypatch.setattr(lm, "STATS_FILE", p)
    assert [r["ts"] for r in lm.load_stats(limit=5)] == ["00002", "00001"]


def test_summarize_stats_still_sees_everything(tmp_path, monkeypatch):
    """汇总要的是全部历史, 不能被 limit 影响。"""
    p = str(tmp_path / "stats.jsonl")
    _write_stats(p, 300)
    monkeypatch.setattr(lm, "STATS_FILE", p)
    total = sum(row[1] for row in lm.summarize_stats())
    assert total == 300