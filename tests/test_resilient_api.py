"""Tests for resilient_api.py pure functions."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from resilient_api import CircuitBreaker, resilient_call


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

def test_circuit_breaker_starts_closed():
    cb = CircuitBreaker(name="test", threshold=3, reset_after=60)
    assert cb.state == "closed"
    assert cb.is_allowed() is True


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(name="test", threshold=2, reset_after=60)
    cb.record_failure()
    assert cb.state == "closed"
    cb.record_failure()
    assert cb.state == "open"
    assert cb.is_allowed() is False


def test_circuit_breaker_resets_on_success():
    cb = CircuitBreaker(name="test", threshold=3, reset_after=60)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb.state == "closed"
    assert cb.failure_count == 0


# ---------------------------------------------------------------------------
# resilient_call
# ---------------------------------------------------------------------------

def test_resilient_call_success():
    result = resilient_call(lambda: "hello", retries=2, fallback="fail")
    assert result == "hello"


def test_resilient_call_returns_fallback_on_failure():
    def always_fail():
        raise ConnectionError("test error")

    result = resilient_call(always_fail, retries=0, base_delay=0.01, fallback="fallback_val")
    assert result == "fallback_val"


def test_resilient_call_retries_then_succeeds():
    call_count = [0]

    def fail_then_succeed():
        call_count[0] += 1
        if call_count[0] < 3:
            raise ConnectionError("temp fail")
        return "success"

    result = resilient_call(fail_then_succeed, retries=3, base_delay=0.01, fallback="fail")
    assert result == "success"
    assert call_count[0] == 3


def test_resilient_call_with_circuit_breaker_open():
    cb = CircuitBreaker(name="test", threshold=1, reset_after=600)
    cb.record_failure()  # Opens circuit
    assert cb.is_allowed() is False

    result = resilient_call(
        lambda: "should not run",
        retries=2,
        fallback="circuit_open",
        circuit_breaker=cb,
    )
    assert result == "circuit_open"


def test_resilient_call_non_retryable_error():
    def raise_value_error():
        raise ValueError("bad input")

    result = resilient_call(raise_value_error, retries=3, base_delay=0.01, fallback="non_retry")
    assert result == "non_retry"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
