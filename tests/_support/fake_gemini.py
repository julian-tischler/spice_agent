"""
Scripted stand-ins for the Gemini client and for the clock.

The design loop takes an injectable client, which is what lets the whole
thing be driven by a fixed list of replies with no network: canned
circuits, plain text, or an exception to simulate an API failure.
FakeTime does the same for pacing - sleeping advances a virtual clock
instead of blocking, so retry and backoff tests run in milliseconds.
"""

from google.genai import errors, types

from spice_agent.agent import RateLimiter, RetryPolicy

# Loop tests with a fake model should not pace or back off in real time.
NO_WAIT = RetryPolicy(min_interval_s=0, initial_backoff_s=0, max_backoff_s=0)


class FakeGeminiClient:
    """
    Stand-in for genai.Client that replays a fixed list of turns.
    Each turn is either a circuit dict (-> emitted as a simulate_circuit
    function call), a str (-> emitted as plain text, i.e. the model
    stopped calling tools), or an Exception (-> raised, to simulate an
    API error). Records every `contents` it was given so tests can
    inspect the feedback the harness sent back.
    """

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = []
        self.models = self  # so client.models.generate_content(...) resolves

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        if not self._turns:
            raise AssertionError("FakeGeminiClient ran out of scripted turns")
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, str):
            content = types.Content(role="model", parts=[types.Part(text=turn)])
            text = turn
        else:
            content = types.Content(
                role="model",
                parts=[types.Part.from_function_call(name="simulate_circuit", args=turn)],
            )
            text = None
        return _FakeResponse(content, text)


class _FakeResponse:
    def __init__(self, content, text):
        self.candidates = [_FakeCandidate(content)]
        self.text = text


class _FakeCandidate:
    def __init__(self, content):
        self.content = content


def _last_function_response(client: FakeGeminiClient) -> dict:
    """The result dict the harness fed back before the most recent call."""
    contents = client.calls[-1]["contents"]
    fn_turn = contents[-1]
    assert fn_turn.role == "function"
    return fn_turn.parts[0].function_response.response["result"]


def _err(code: int, headers: dict | None = None) -> errors.APIError:
    """Build a google-genai APIError of the right class for `code`."""
    body = {"error": {"code": code, "message": "high demand", "status": "UNAVAILABLE"}}
    cls = errors.ServerError if code >= 500 else errors.ClientError
    e = cls(code, body, None)
    if headers is not None:
        class _Resp:
            pass
        r = _Resp()
        r.headers = headers
        e.response = r
    return e


class FakeTime:
    """Virtual clock: sleep() advances it instead of blocking."""
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def _limiter(client, policy, ft: FakeTime) -> RateLimiter:
    return RateLimiter(client, policy, sleep=ft.sleep, clock=ft.clock)


def _gen(limiter):
    return limiter.models.generate_content(model="m", contents=[], config=None)


def _quota_err(retry_delay_s: str) -> errors.APIError:
    """A 429 shaped like Gemini's daily-quota response, which carries a
    google.rpc.RetryInfo asking for a very long wait."""
    body = {"error": {
        "code": 429,
        "message": "You exceeded your current quota",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo",
             "retryDelay": retry_delay_s},
        ],
    }}
    return errors.ClientError(429, body, None)
