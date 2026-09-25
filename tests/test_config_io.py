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
    store.save([])                                   # save 内部吞异常
    assert len(TaskStore(path).load()) == 1          # 老任务还在
    assert not os.path.exists(path + ".tmp")
