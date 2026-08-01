from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from policy_analysis.auth.service import LoginRateLimiter
from policy_analysis.core.errors import APIError


class ManualMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class NoItemsOrderedDict(OrderedDict):
    def items(self):
        raise AssertionError("限速清理不得扫描全部 items")


def _limiter(clock: ManualMonotonic, *, capacity: int) -> LoginRateLimiter:
    return LoginRateLimiter(
        attempts=3,
        window_seconds=60,
        max_active_keys=capacity,
        monotonic=clock,
    )


def _fail_once(limiter: LoginRateLimiter, key: tuple[str, str]) -> None:
    with limiter.guard(key):
        limiter.ensure_allowed(key)
        limiter.record_failure(key)


def test_active_key_capacity_fails_closed_and_releases_oldest_after_window() -> None:
    clock = ManualMonotonic()
    limiter = _limiter(clock, capacity=2)
    first = ("198.51.100.1", "first")
    second = ("198.51.100.1", "second")
    third = ("198.51.100.1", "third")
    _fail_once(limiter, first)
    _fail_once(limiter, second)

    with limiter.guard(third), pytest.raises(APIError) as limited:
        limiter.ensure_allowed(third)

    assert limited.value.status_code == 429
    assert limited.value.code == "LOGIN_RATE_LIMITED"
    assert "2" not in limited.value.message
    assert len(limiter._failures) == 2

    clock.advance(61)
    _fail_once(limiter, third)
    assert len(limiter._failures) == 1


def test_ensure_reserves_capacity_and_abandoned_empty_key_expires() -> None:
    clock = ManualMonotonic()
    limiter = _limiter(clock, capacity=1)
    abandoned = ("198.51.100.1", "abandoned")
    replacement = ("198.51.100.1", "replacement")

    with limiter.guard(abandoned):
        limiter.ensure_allowed(abandoned)

    with limiter.guard(replacement), pytest.raises(APIError):
        limiter.ensure_allowed(replacement)

    clock.advance(61)
    with limiter.guard(replacement):
        limiter.ensure_allowed(replacement)
    assert len(limiter._failures) == 1


def test_cleanup_never_uses_full_mapping_items_scan() -> None:
    clock = ManualMonotonic()
    limiter = _limiter(clock, capacity=4)
    key = ("198.51.100.1", "reader")
    _fail_once(limiter, key)
    limiter._failures = NoItemsOrderedDict(limiter._failures)

    with limiter.guard(key):
        limiter.ensure_allowed(key)


def test_failure_history_per_key_never_exceeds_attempt_limit() -> None:
    clock = ManualMonotonic()
    limiter = _limiter(clock, capacity=1)
    key = ("198.51.100.1", "reader")

    for _ in range(100):
        limiter.record_failure(key)

    assert len(limiter._failures[key].failures) == 3


def test_concurrent_distinct_keys_cannot_exceed_capacity() -> None:
    clock = ManualMonotonic()
    limiter = _limiter(clock, capacity=3)
    worker_count = 12
    barrier = Barrier(worker_count)

    def attempt(index: int) -> int:
        key = ("198.51.100.1", f"account-{index}")
        barrier.wait()
        try:
            _fail_once(limiter, key)
        except APIError as error:
            return error.status_code
        return 401

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        statuses = list(executor.map(attempt, range(worker_count)))

    assert statuses.count(401) == 3
    assert statuses.count(429) == worker_count - 3
    assert len(limiter._failures) == 3
