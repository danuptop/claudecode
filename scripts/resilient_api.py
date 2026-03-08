#!/usr/bin/env python3
"""
Resilient API call wrapper for Report Base pipeline scripts.

Provides retry logic with exponential backoff, circuit breaking,
and clean fallback handling for all external API calls (DuckDuckGo,
Grok/X.AI, Clay, Icebreaker MCP).

Usage:
    from resilient_api import resilient_call, CircuitBreaker

    # Simple retry with fallback
    result = resilient_call(
        lambda: requests.get("https://api.x.ai/...", timeout=15),
        retries=2,
        fallback="Analysis unavailable — API timeout.",
    )

    # With circuit breaker (stops calling after repeated failures)
    grok_breaker = CircuitBreaker(name="grok", threshold=3, reset_after=300)
    result = resilient_call(
        lambda: requests.get("https://api.x.ai/...", timeout=15),
        retries=2,
        fallback="Analysis unavailable — API timeout.",
        circuit_breaker=grok_breaker,
    )

Deployment:
    Place at: /home/ubuntu/clawd/scripts/resilient_api.py
    Import from: hiring_intel_module.py, founder-intel-pipeline.py
"""

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("resilient_api")

# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """
    Simple circuit breaker to prevent hammering a failing API.

    After `threshold` consecutive failures, the circuit opens and all
    subsequent calls return the fallback immediately for `reset_after`
    seconds. After that, one "probe" call is allowed through — if it
    succeeds, the circuit closes; if it fails, it stays open.
    """

    def __init__(self, name: str, threshold: int = 3, reset_after: int = 300):
        self.name = name
        self.threshold = threshold
        self.reset_after = reset_after
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self._state = "closed"  # closed, open, half-open

    @property
    def state(self) -> str:
        if self._state == "open" and self.last_failure_time:
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.reset_after:
                self._state = "half-open"
        return self._state

    def record_success(self):
        self.failure_count = 0
        self._state = "closed"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.threshold:
            self._state = "open"
            logger.warning(
                f"Circuit breaker [{self.name}] OPEN after "
                f"{self.failure_count} failures. "
                f"Reset in {self.reset_after}s."
            )

    def is_allowed(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half-open":
            logger.info(f"Circuit breaker [{self.name}] half-open — allowing probe call.")
            return True
        return False


# ---------------------------------------------------------------------------
# Pre-configured circuit breakers for known services
# ---------------------------------------------------------------------------

# Shared breakers — import these in pipeline scripts
duckduckgo_breaker = CircuitBreaker(name="duckduckgo", threshold=3, reset_after=600)
grok_breaker = CircuitBreaker(name="grok-x-ai", threshold=3, reset_after=300)
clay_breaker = CircuitBreaker(name="clay", threshold=3, reset_after=300)
icebreaker_breaker = CircuitBreaker(name="icebreaker-mcp", threshold=2, reset_after=900)


# ---------------------------------------------------------------------------
# Resilient call wrapper
# ---------------------------------------------------------------------------


def resilient_call(
    fn: Callable[[], Any],
    retries: int = 2,
    base_delay: float = 2.0,
    timeout: Optional[float] = None,
    fallback: Any = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> Any:
    """
    Execute a function with retry logic and optional circuit breaking.

    Args:
        fn: The callable to execute. Should be a lambda or closure.
        retries: Number of retry attempts after the initial call.
        base_delay: Base delay in seconds for exponential backoff.
        timeout: Not enforced here — pass timeout to the underlying
                 requests call inside fn.
        fallback: Value to return if all retries fail.
        circuit_breaker: Optional CircuitBreaker instance.
        on_error: Optional callback for each error (for logging/metrics).

    Returns:
        The return value of fn(), or fallback if all attempts fail.
    """
    # Check circuit breaker
    if circuit_breaker and not circuit_breaker.is_allowed():
        logger.info(
            f"Circuit breaker [{circuit_breaker.name}] is open — "
            f"returning fallback immediately."
        )
        return fallback

    # Build tuple of retryable exception types
    _retryable = (ConnectionError, TimeoutError, OSError)
    try:
        import requests.exceptions as _rexc
        _retryable = (
            ConnectionError, TimeoutError, OSError,
            _rexc.ConnectionError, _rexc.Timeout, _rexc.ReadTimeout,
            _rexc.ConnectTimeout,
        )
    except ImportError:
        pass

    last_exception = None
    for attempt in range(retries + 1):
        try:
            result = fn()

            # If we get here, the call succeeded
            if circuit_breaker:
                circuit_breaker.record_success()

            return result

        except _retryable as e:
            last_exception = e
            if on_error:
                on_error(e)
            logger.warning(
                f"Attempt {attempt + 1}/{retries + 1} failed: "
                f"{type(e).__name__}: {e}"
            )
        except Exception as e:
            # Non-retryable error — fail immediately
            logger.error(f"Non-retryable error: {type(e).__name__}: {e}")
            if circuit_breaker:
                circuit_breaker.record_failure()
            if on_error:
                on_error(e)
            return fallback

        # Exponential backoff before retry
        if attempt < retries:
            delay = base_delay * (2 ** attempt)
            logger.info(f"Retrying in {delay}s...")
            time.sleep(delay)

    # All retries exhausted
    if circuit_breaker:
        circuit_breaker.record_failure()

    logger.error(
        f"All {retries + 1} attempts failed. Last error: {last_exception}. "
        f"Returning fallback."
    )
    return fallback


# ---------------------------------------------------------------------------
# Convenience wrappers for common API calls
# ---------------------------------------------------------------------------


def search_duckduckgo(query: str, timeout: int = 15) -> Optional[str]:
    """
    Search DuckDuckGo with resilient error handling.

    Returns HTML response text, or None on failure.
    """
    import requests

    def _call():
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        return resp.text

    return resilient_call(
        _call,
        retries=1,
        base_delay=3.0,
        fallback=None,
        circuit_breaker=duckduckgo_breaker,
    )


def call_grok(prompt: str, timeout: int = 30, api_key: Optional[str] = None) -> Optional[str]:
    """
    Call Grok API with resilient error handling.

    Returns response text, or None on failure.
    """
    import requests
    import os

    key = api_key or os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY")
    if not key:
        logger.warning("No Grok API key configured")
        return None

    def _call():
        resp = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "grok-2-latest",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    return resilient_call(
        _call,
        retries=2,
        base_delay=2.0,
        fallback=None,
        circuit_breaker=grok_breaker,
    )
