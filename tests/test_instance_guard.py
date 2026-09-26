"""统计行不许被并发吞掉 + 同一份工作空间别同时开两份程序。

两件事是同一个洞的两面:
- `record_stat` 以前无锁。实测同进程 10 线程各写 100 行 → 只落 964 行、**0 个坏行**,
  也就是整条静默消失: 看板分母凭空变小, 用户完全无从查起。
- 而跨进程更狠: 商户目录就是 Chromium 的 profile, 一个 profile 同一时刻只能被一个进程
  占用(12 章第 1 条), 双开时另一边只报"Sync API inside asyncio loop"这种莫名错。
  全仓以前**没有任何单实例判断**。

守卫的口径与依赖体检一致: **只问不拦**。锁可能是上次崩溃/被强杀留下的, 把它当"别人在跑"
就成了永远开不了 —— 所以判断不出来时一律按"没人占"放行。
"""
import json
import os
import threading

import pytest

import core.instance as inst
import core.logger as lg


# ===== stats.jsonl 的并发写 =====

def test_concurrent_record_stat_loses_no_line(tmp_path, monkeypatch):
    """12 线程 × 80 条 = 960 行, 少一条都算失败(不加锁实测会整条消失)。"""
    path = str(tmp_path / "stats.jsonl")
    monkeypatch.setattr(lg, "STATS_FILE", path)
    threads = [threading.Thread(
        target=lambda i=i: [lg.record_stat("银联", f"m{i}", "2026-09-01", "2026-09-02",
                                           "success", 1.0) for _ in range(80)])
        for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = open(path, encoding="utf-8").read().splitlines()
    assert len(lines) == 960, f"丢了 {960 - len(lines)} 条统计"
    assert not [l for l in lines if '"platform"' not in l or not l.endswith("}")]


def test_record_stat_never_raises(tmp_path, monkeypatch):
    """统计写不进去(目录不存在/只读)不许把导出流程带下去。"""
    monkeypatch.setattr(lg, "STATS_FILE", str(tmp_path / "没有这个目录" / "stats.jsonl"))
    lg.record_stat("银联", "甲", "2026-09-01", "2026-09-02", "success", 1.0)   # 不抛即通过


# ===== 单实例守卫 =====

def _write_lock(root, pid):
    with open(inst.lock_path(root), "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "app": "liushui_export"}, f)


def test_free_workspace_is_claimed_and_released(tmp_path):
    allowed, other = inst.claim(str(tmp_path))
    assert (allowed, other) == (True, None)
    assert json.load(open(inst.lock_path(str(tmp_path)), encoding="utf-8"))["pid"] == os.getpid()
    inst.release()
    assert not os.path.exists(inst.lock_path(str(tmp_path)))


def test_a_live_other_instance_is_reported_not_kicked_out(tmp_path, monkeypatch):
    """另一个进程还活着: 说出它的 pid, 但**不许覆盖它的锁文件**。"""
    _write_lock(tmp_path, 999998)
    monkeypatch.setattr(inst, "_pid_alive", lambda pid: True)
    allowed, other = inst.claim(str(tmp_path))
    assert allowed is True and other == 999998
    assert json.load(open(inst.lock_path(str(tmp_path)), encoding="utf-8"))["pid"] == 999998


def test_a_stale_lock_is_taken_over(tmp_path, monkeypatch):
    """上次崩溃留下的锁(进程号已经不在)必须能接管 —— 否则这台机器永远开不了工具。"""
    _write_lock(tmp_path, 999999)
    monkeypatch.setattr(inst, "_pid_alive", lambda pid: False)
    allowed, other = inst.claim(str(tmp_path))
    assert (allowed, other) == (True, None)
    assert json.load(open(inst.lock_path(str(tmp_path)), encoding="utf-8"))["pid"] == os.getpid()


@pytest.mark.parametrize("content", ["", "{不是 json", '{"pid": "abc"}', "{}"])
def test_a_broken_lock_file_does_not_block_startup(tmp_path, content):
    """锁文件被写坏/写成空的时候, 守卫不许变成"开不了"。"""
    with open(inst.lock_path(tmp_path), "w", encoding="utf-8") as f:
        f.write(content)
    allowed, other = inst.claim(str(tmp_path))
    assert (allowed, other) == (True, None)


def test_claim_survives_an_unwritable_lock_path(tmp_path, monkeypatch):
    """锁写不下去(只读介质、路径被占用成目录)时照常放行, 守卫不该变成开不了。"""
    blocked = tmp_path / "locks"        # 拿一个**目录**当锁路径: open(path,"w") 必失败
    blocked.mkdir()
    monkeypatch.setattr(inst, "lock_path", lambda root: str(blocked))
    assert inst.claim(str(tmp_path)) == (True, None)
    inst.release()                      # 没占住就别去删, 也不许抛


def test_release_does_not_touch_someone_elses_lock(tmp_path, monkeypatch):
    """只删自己写下的那份: 别人(另一进程号)的锁文件必须原样留着。"""
    _write_lock(tmp_path, 999997)
    monkeypatch.setattr(inst, "_pid_alive", lambda pid: False)
    inst.claim(str(tmp_path))                      # 我们接管, 锁里是本项目号
    _write_lock(tmp_path, 999997)                  # 假装另一个进程又把锁抢了回去
    inst.release()
    assert json.load(open(inst.lock_path(str(tmp_path)), encoding="utf-8"))["pid"] == 999997


def test_our_own_pid_counts_as_alive():
    assert inst._pid_alive(os.getpid()) is True
    assert inst._pid_alive("不合法") is False
    assert inst._pid_alive(None) is False
