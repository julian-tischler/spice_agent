"""
Pacing and retry around the model API.

All of these run on a virtual clock, so a test of a 60-second backoff
takes no wall time. The case that matters most is the last pair: a
per-minute throttle asking for seconds must be retried, while an
exhausted daily quota asking for hours must not be - retrying the latter
burns the entire budget on waits that cannot succeed.
"""

import pytest
from google.genai import errors

from spice_agent.agent import RateLimiter, RetryPolicy

from _support.circuits import RC_LOWPASS
from _support.fake_gemini import (
    FakeGeminiClient,
    FakeTime,
    _err,
    _gen,
    _limiter,
    _quota_err,
)


def test_retries_503_then_succeeds():
    ft = FakeTime()
    client = FakeGeminiClient([_err(503), _err(503), RC_LOWPASS])
    lim = _limiter(client, RetryPolicy(min_interval_s=0, initial_backoff_s=5,
                                       backoff_multiplier=2, jitter_frac=0), ft)
    resp = _gen(lim)
    assert resp.candidates[0].content.parts[0].function_call.name == "simulate_circuit"
    assert lim.requests == 3
    assert lim.retries == 2
    assert ft.sleeps == [5.0, 10.0]          # exponential, no jitter


def test_gives_up_after_max_retries():
    ft = FakeTime()
    client = FakeGeminiClient([_err(503)] * 10)
    lim = _limiter(client, RetryPolicy(min_interval_s=0, max_retries=3,
                                       initial_backoff_s=1, jitter_frac=0), ft)
    with pytest.raises(errors.ServerError):
        _gen(lim)
    assert lim.requests == 4                 # 1 + 3 retries
    assert len(ft.sleeps) == 3


def test_429_is_retried_but_400_is_not():
    ft = FakeTime()
    client = FakeGeminiClient([_err(429), RC_LOWPASS])
    lim = _limiter(client, RetryPolicy(min_interval_s=0, initial_backoff_s=1, jitter_frac=0), ft)
    _gen(lim)
    assert lim.retries == 1

    client = FakeGeminiClient([_err(400), RC_LOWPASS])
    lim = _limiter(client, RetryPolicy(min_interval_s=0), ft)
    with pytest.raises(errors.ClientError):
        _gen(lim)
    assert lim.retries == 0
    assert lim.requests == 1


def test_non_api_errors_propagate_untouched():
    ft = FakeTime()
    client = FakeGeminiClient([RuntimeError("boom")])
    lim = _limiter(client, RetryPolicy(min_interval_s=0), ft)
    with pytest.raises(RuntimeError, match="boom"):
        _gen(lim)
    assert lim.retries == 0


def test_backoff_is_capped():
    ft = FakeTime()
    client = FakeGeminiClient([_err(503)] * 4 + [RC_LOWPASS])
    lim = _limiter(client, RetryPolicy(min_interval_s=0, max_retries=4, initial_backoff_s=10,
                                       backoff_multiplier=10, max_backoff_s=25, jitter_frac=0), ft)
    _gen(lim)
    assert ft.sleeps == [10.0, 25.0, 25.0, 25.0]


def test_daily_quota_429_is_not_retried():
    """A quota 429 asks for ~11 hours. Backing off in seconds can never
    succeed, so it must fail fast instead of burning the retry budget."""
    ft = FakeTime()
    client = FakeGeminiClient([_quota_err("41225s")] * 6 + [RC_LOWPASS])
    lim = _limiter(client, RetryPolicy(min_interval_s=0, max_retries=5,
                                       initial_backoff_s=5, max_backoff_s=60), ft)
    with pytest.raises(errors.ClientError):
        _gen(lim)
    assert lim.requests == 1, "must not retry a hard quota limit"
    assert lim.retries == 0
    assert ft.sleeps == [], "must not sleep at all"


def test_short_server_delay_is_still_retried():
    """A per-minute throttle asks for a few seconds — that IS transient."""
    ft = FakeTime()
    client = FakeGeminiClient([_quota_err("20s"), RC_LOWPASS])
    lim = _limiter(client, RetryPolicy(min_interval_s=0, initial_backoff_s=5,
                                       max_backoff_s=60, jitter_frac=0), ft)
    _gen(lim)
    assert lim.retries == 1
    assert ft.sleeps == [20.0], "should honour the server's own delay"


def test_retry_after_header_is_honoured():
    ft = FakeTime()
    client = FakeGeminiClient([_err(503, headers={"retry-after": "7"}), RC_LOWPASS])
    lim = _limiter(client, RetryPolicy(min_interval_s=0, initial_backoff_s=60, jitter_frac=0), ft)
    _gen(lim)
    assert ft.sleeps == [7.0]


def test_jitter_stays_within_bounds():
    ft = FakeTime()
    client = FakeGeminiClient([_err(503)] * 5 + [RC_LOWPASS])
    lim = _limiter(client, RetryPolicy(min_interval_s=0, max_retries=5, initial_backoff_s=10,
                                       backoff_multiplier=1, jitter_frac=0.25), ft)
    _gen(lim)
    assert len(ft.sleeps) == 5
    assert all(7.5 <= s <= 12.5 for s in ft.sleeps)


def test_pacing_enforces_min_interval():
    ft = FakeTime()
    client = FakeGeminiClient([RC_LOWPASS] * 3)
    lim = _limiter(client, RetryPolicy(min_interval_s=2.0), ft)
    _gen(lim)                                # t=1000, first call never waits
    ft.now += 0.5
    _gen(lim)                                # only 0.5s passed -> sleep 1.5
    ft.now += 5.0
    _gen(lim)                                # 5s passed -> no sleep
    assert ft.sleeps == [1.5]
    assert lim.requests == 3
