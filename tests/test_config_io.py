"""配置落盘的原子性: settings / scheduled_tasks / selection_state 每次都是整体重写,
写坏一次就等于把用户的配置和定时任务清空(三处读取都是 except → 用默认值/返回空)。
"""
import json
import os

import pytest

from core.config import write_json_atomic


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_round_trip_leaves_no_temp_file(tmp_path):
    p = str(tmp_path / "settings.json")
    write_json_atomic(p, {"show_browser": False, "retry_times": 2})
    assert _read(p) == {"show_browser": False, "retry_times": 2}
    assert not os.path.exists(p + ".tmp")


def test_creates_missing_parent_dirs(tmp_path):
    p = str(tmp_path / "a" / "b" / "tasks.json")
    write_json_atomic(p, {"version": 1, "jobs": []})
    assert _read(p) == {"version": 1, "jobs": []}


def test_failed_write_keeps_original_content(tmp_path):
    """序列化中途失败时, 原文件必须还是上一次的好数据。"""
    p = str(tmp_path / "settings.json")
    write_json_atomic(p, {"keep": "me"})
    with pytest.raises(TypeError):
        write_json_atomic(p, {"broken": object()})
    assert _read(p) == {"keep": "me"}
    assert not os.path.exists(p + ".tmp")


def test_overwrite_is_complete(tmp_path):
    p = str(tmp_path / "selection_state.json")
    write_json_atomic(p, {"platform": {"youzan": True}, "merchant": {}})
    write_json_atomic(p, {"platform": {"youzan": False}, "merchant": {"youzan": {}}})
    assert _read(p) == {"platform": {"youzan": False}, "merchant": {"youzan": {}}}


def test_partial_save_keeps_the_settings_that_were_not_touched(tmp_path, monkeypatch):
    """界面各处都是"点一下只写自己那一两个键": 合并必须以现有文件为底。

    旧实现是 `dict(DEFAULT_SETTINGS)` 再 update, 于是勾一下"失败重试"就把首次重试间隔
    写回 30, 动一下保活开关就把文件名模式/是否显示浏览器恢复默认 —— 用户改过的设置
    在毫无提示的情况下被抹平。
    """
    import core.config as cfg
    path = str(tmp_path / "settings.json")
    monkeypatch.setattr(cfg, "SETTINGS_FILE", path)
    cfg.save_settings({"download_name_mode": "original",
                       "keepalive_interval_min": 10,
                       "retry_interval_s": 5})
    cfg.save_settings({"retry_times": 0})
    saved = _read(path)
    assert saved["retry_times"] == 0
    assert saved["download_name_mode"] == "original"
    assert saved["keepalive_interval_min"] == 10
    assert saved["retry_interval_s"] == 5, "点勾不该动没传的那个键"


def test_partial_save_on_a_missing_file_still_writes_every_key(tmp_path, monkeypatch):
    import core.config as cfg
    path = str(tmp_path / "settings.json")
    monkeypatch.setattr(cfg, "SETTINGS_FILE", path)
    cfg.save_settings({"show_browser": False})
    saved = _read(path)
    assert set(saved) == set(cfg.DEFAULT_SETTINGS), "缺失的键仍要按默认补齐"
    assert saved["show_browser"] is False


def test_corrupt_file_falls_back_to_defaults_before_merging(tmp_path, monkeypatch):
    """坏文件读不出内容时按默认值兜底, 但传进来的键一定生效。"""
    import core.config as cfg
    path = str(tmp_path / "settings.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"retry_time')            # 截断的 JSON
    monkeypatch.setattr(cfg, "SETTINGS_FILE", path)
    cfg.save_settings({"retry_times": 4})
    saved = _read(path)
    assert saved["retry_times"] == 4 and saved["keepalive_interval_min"] == 30


def test_task_store_save_uses_atomic_path(tmp_path, monkeypatch):
    """TaskStore.save 走同一个写入器, 且坏数据不会清空已有任务文件。"""
    from core.scheduler import CronJob, TaskStore

    path = str(tmp_path / "scheduled_tasks.json")
    store = TaskStore(path)
    store.save([CronJob(job_id="j1", name="每日", cron="0 9 * * *",
                        platforms=["youzan"], merchants=["旗舰店A"])])
    assert len(TaskStore(path).load()) == 1

    def boom(*args, **kwargs):
        raise IOError("磁盘满了")
    monkeypatch.setattr("core.config.os.replace", boom)
    assert store.save([]) is False                 # 失败要问得出来, 但不许抛给业务
    assert "磁盘满了" in store.last_error
    assert len(TaskStore(path).load()) == 1        # 老任务还在
    assert not [f for f in os.listdir(tmp_path) if ".tmp" in f], "失败那次不许留垃圾"


def test_temp_name_is_unique_per_write(tmp_path, monkeypatch):
    """固定名 `path.tmp` 在两个写者手里会互相截断、互相占用(实测 48 次写 38 次抛错)。"""
    import core.config as cfg
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        return real_replace(src, dst)

    monkeypatch.setattr(cfg.os, "replace", spy)
    p = str(tmp_path / "settings.json")
    for i in range(5):
        write_json_atomic(p, {"jobs": [{"n": i}]})
    assert len(set(seen)) == 5, seen


def test_concurrent_writers_all_land(tmp_path):
    """多线程同写一个 json: 每次写都得真的成功(旧实现靠 except: pass 把失败咽掉)。"""
    import threading
    p = str(tmp_path / "scheduled_tasks.json")
    write_json_atomic(p, {"version": 1, "jobs": []})
    failures = []

    def writer(i):
        try:
            write_json_atomic(p, {"version": 1,
                                  "jobs": [{"name": "t%d" % i, "pad": "x" * 200000}]})
        except Exception as e:                     # 业务侧的 save 会咽掉, 这里要露出来
            failures.append(repr(e))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not failures, failures
    data = _read(p)
    assert len(data["jobs"]) == 1 and data["jobs"][0]["name"].startswith("t")
    assert not [f for f in os.listdir(tmp_path) if ".tmp" in f], "写完不该留暂存文件"
