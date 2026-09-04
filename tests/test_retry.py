from core.main_gui import run_with_retry


def test_retry_until_success():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            return "failed"
        return "success"

    result = run_with_retry(flaky, retry_times=3, retry_interval_s=0,
                            log=lambda m: None)
    assert result == "success"
    assert len(calls) == 3


def test_retry_gives_up_after_limit():
    calls = []

    def always_fail():
        calls.append(1)
        return "failed"

    result = run_with_retry(always_fail, retry_times=2, retry_interval_s=0,
                            log=lambda m: None)
    assert result == "failed"
    assert len(calls) == 3  # 1 次原始 + 2 次重试


def test_no_retry_when_zero():
    calls = []

    def once():
        calls.append(1)
        return "failed"

    result = run_with_retry(once, retry_times=0, retry_interval_s=0,
                            log=lambda m: None)
    assert result == "failed"
    assert len(calls) == 1