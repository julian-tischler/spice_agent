"""
Pacing and retrying calls to the model API.

Two separate concerns live here. Pacing keeps a fast design loop from
bursting against a per-minute quota. Retrying handles the transient
failures (429 and 5xx) that would otherwise abandon a whole design run
over one bad response.

The distinction that matters: a per-minute throttle asks you to wait
seconds and is worth retrying, while an exhausted daily quota asks for
hours and never is. The server says which in its own RetryInfo, so that
is what decides.

Nothing here knows anything about circuits.
"""

import random
import time
from dataclasses import dataclass

from google.genai import errors


@dataclass
class RetryPolicy:
    min_interval_s: float = 2.0        # pacing between consecutive requests
    max_retries: int = 5               # attempts = 1 + max_retries
    initial_backoff_s: float = 5.0
    backoff_multiplier: float = 2.0
    max_backoff_s: float = 60.0
    jitter_frac: float = 0.25          # +/- fraction of the backoff, breaks lockstep
    retry_status_codes: tuple[int, ...] = (429, 500, 502, 503, 504)


class RateLimiter:
    """
    Wraps any client exposing `client.models.generate_content(...)` (the
    real genai.Client or a test fake) and adds pacing + retry. Exposes the
    same `.models.generate_content` interface so callers don't change.

    `sleep` and `clock` are injectable so tests can run without waiting.
    """

    def __init__(self, client, policy: RetryPolicy | None = None, *,
                 sleep=None, clock=None, log=None):
        self._client = client
        self.policy = policy or RetryPolicy()
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._log = log or (lambda *a, **k: None)
        self._last_call_t: float | None = None
        self.models = self          # so `.models.generate_content` resolves
        # Stats, mainly for tests / diagnostics
        self.requests = 0           # attempts actually sent
        self.retries = 0
        self.total_sleep_s = 0.0

    # -- helpers ------------------------------------------------------

    def _wait(self, seconds: float):
        if seconds > 0:
            self.total_sleep_s += seconds
            self._sleep(seconds)

    def _pace(self):
        """Enforce min_interval_s since the previous request was sent."""
        if self._last_call_t is not None:
            elapsed = self._clock() - self._last_call_t
            self._wait(self.policy.min_interval_s - elapsed)

    @staticmethod
    def _server_retry_delay_s(exc: Exception) -> float | None:
        """
        How long the server itself says to wait, if it said so at all.

        Two places carry it: a Retry-After header, and Google's
        google.rpc.RetryInfo entry inside the error body. The second one
        matters because a daily quota 429 reports a delay of many hours -
        retrying that on a seconds-scale backoff can never succeed.
        """
        headers = getattr(getattr(exc, "response", None), "headers", None)
        if headers:
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra is not None:
                try:
                    return float(ra)
                except (TypeError, ValueError):
                    pass

        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            # Accept either the full {"error": {...}} body or just the inner dict.
            inner = details.get("error", details)
            for entry in (inner.get("details") or []):
                if not isinstance(entry, dict):
                    continue
                if entry.get("@type", "").endswith("google.rpc.RetryInfo"):
                    raw = str(entry.get("retryDelay", "")).rstrip("s")
                    try:
                        return float(raw)
                    except ValueError:
                        pass
        return None

    def _is_retryable(self, exc: Exception) -> bool:
        if not (isinstance(exc, errors.APIError)
                and getattr(exc, "code", None) in self.policy.retry_status_codes):
            return False
        # If the server asks for longer than we are ever willing to wait,
        # this is an exhausted quota rather than a transient throttle.
        # Backing off would only waste the whole retry budget.
        wanted = self._server_retry_delay_s(exc)
        if wanted is not None and wanted > self.policy.max_backoff_s:
            self._log(
                f"Gemini returned {getattr(exc, 'code', '?')} and asks to retry in "
                f"{wanted:.0f}s, beyond the {self.policy.max_backoff_s:.0f}s backoff "
                f"cap - treating as a hard quota limit and not retrying."
            )
            return False
        return True

    def _backoff(self, attempt: int, exc: Exception) -> float:
        """Seconds to wait before retry number `attempt` (1-based)."""
        wanted = self._server_retry_delay_s(exc)
        if wanted is not None:
            return min(wanted, self.policy.max_backoff_s)
        p = self.policy
        base = min(p.initial_backoff_s * p.backoff_multiplier ** (attempt - 1), p.max_backoff_s)
        jitter = base * p.jitter_frac * random.uniform(-1, 1)
        return max(0.0, base + jitter)

    # -- the wrapped call ---------------------------------------------

    def generate_content(self, **kwargs):
        attempt = 0
        while True:
            self._pace()
            self._last_call_t = self._clock()
            self.requests += 1
            try:
                return self._client.models.generate_content(**kwargs)
            except Exception as exc:
                if not self._is_retryable(exc) or attempt >= self.policy.max_retries:
                    raise
                attempt += 1
                self.retries += 1
                delay = self._backoff(attempt, exc)
                self._log(
                    f"Gemini returned {getattr(exc, 'code', '?')} "
                    f"({getattr(exc, 'status', type(exc).__name__)}); "
                    f"retry {attempt}/{self.policy.max_retries} in {delay:.1f}s"
                )
                self._wait(delay)

# Fixed AC analysis for every simulation. Not exposed to the model as a
# tool parameter — the harness enforces it, the model only sees the sweep
# range in the prompt so it knows where its target must land.
