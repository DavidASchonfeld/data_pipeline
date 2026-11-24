# Tests for the bounded retry helper. A fake sleep records waits, so the tests run instantly.

from __future__ import annotations

import pytest

from agent_data.engine._shared.retry import backoff_delay, call_with_retry


class _Flaky:
    """Fails with the given errors in order, then returns 'ok'."""

    def __init__(self, *failures: BaseException) -> None:
        self.failures = list(failures)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "ok"


def test_succeeds_after_transient_failures():
    waits: list[float] = []
    fn = _Flaky(TimeoutError(), ConnectionError())
    result = call_with_retry(fn, what="test", retry_on=(TimeoutError, ConnectionError), attempts=3, sleep=waits.append)
    assert result == "ok"
    assert fn.calls == 3
    assert len(waits) == 2


def test_gives_up_after_the_last_attempt_and_reraises_the_original():
    fn = _Flaky(TimeoutError("1"), TimeoutError("2"), TimeoutError("3"))
    with pytest.raises(TimeoutError, match="3"):
        call_with_retry(fn, what="test", retry_on=(TimeoutError,), attempts=3, sleep=lambda _: None)
    assert fn.calls == 3


def test_non_retryable_error_is_raised_immediately():
    fn = _Flaky(ValueError("bad request"))
    with pytest.raises(ValueError):
        call_with_retry(fn, what="test", retry_on=(TimeoutError,), attempts=5, sleep=lambda _: None)
    assert fn.calls == 1


@pytest.mark.parametrize("kwargs", [{"attempts": 0, "retry_on": (TimeoutError,)}, {"attempts": 2, "retry_on": ()}])
def test_rejects_meaningless_settings(kwargs):
    with pytest.raises(ValueError):
        call_with_retry(lambda: "ok", what="test", sleep=lambda _: None, **kwargs)


def test_backoff_grows_but_never_exceeds_the_cap():
    for attempt in range(1, 10):
        delay = backoff_delay(attempt, base_delay=0.5, max_delay=4.0)
        assert 0 <= delay <= min(4.0, 0.5 * 2 ** (attempt - 1))
