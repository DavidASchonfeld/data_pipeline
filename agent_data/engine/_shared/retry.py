# Bounded retry with exponential backoff and jitter, for external calls that have no retry of their own.
# Rule: never wrap a client that already retries (the Anthropic/OpenAI SDKs, a requests Session with a retry
# adapter, the Snowflake connector) — stacking two retry layers multiplies attempts and hides real outages.

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from agent_data.engine._shared.logging import get_logger, log_event

T = TypeVar("T")

_log = get_logger(__name__)

# Sensible defaults: 3 tries, waits of up to ~0.5s then ~1s, never more than 8s between tries.
DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 0.5
DEFAULT_MAX_DELAY_SECONDS = 8.0


def call_with_retry(
    fn: Callable[[], T],
    *,
    what: str,
    retry_on: tuple[type[BaseException], ...],
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_MAX_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    # Run fn(); if it raises one of retry_on, wait and try again, up to `attempts` times in total.
    # Errors not in retry_on are raised at once (retrying a bad request just repeats the failure).
    # After the last attempt the original exception is re-raised so the caller can wrap it in its layer's error.
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    if not retry_on:
        raise ValueError("retry_on must list at least one exception type to retry")

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            if attempt == attempts:
                log_event(_log, "retry_exhausted", logging.WARNING, what=what, attempts=attempts,
                          error=type(exc).__name__)
                raise
            delay = backoff_delay(attempt, base_delay, max_delay)
            log_event(_log, "retrying", logging.WARNING, what=what, attempt=attempt, of=attempts,
                      error=type(exc).__name__, wait_seconds=round(delay, 2))
            sleep(delay)
    raise AssertionError("unreachable: the loop always returns or raises")  # keeps type checkers happy


def backoff_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    # "Full jitter": a random wait between 0 and base*2^(attempt-1), capped — spreads out retries from many callers.
    ceiling = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return random.uniform(0, ceiling)
