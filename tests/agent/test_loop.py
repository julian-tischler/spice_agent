"""
The design loop itself, plus the live runs against a real model.

The offline tests drive run_agent_loop with a scripted client, so they
check iteration handling, the feedback actually sent back, budget
exhaustion and recovery from a broken proposal - all without a network.

The live tests (-m live) need GEMINI_API_KEY. They append every run to
out/runs/live/live_runs.jsonl, because a live run costs quota and produces a real
design; keeping only PASSED/FAILED would throw away the most interesting
output in the suite.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from spice_agent.agent import (
    MODEL_NAME,
    AgentRunResult,
    RetryPolicy,
    run_agent_loop,
)
from spice_agent.simulation import DesignSpec

from _support.fake_gemini import (
    NO_WAIT,
    FakeGeminiClient,
    FakeTime,
    _err,
    _limiter,
)
from _support.circuits import (
    AC,
    DIVIDER,
    RC_HIGHPASS,
    RC_LOWPASS,
    RLC_BANDPASS,
    RLC_NOTCH,
    TWIN_T_NOTCH,
    _with_values,
)


def _last_function_response(client: FakeGeminiClient) -> dict:
    """The result dict the harness fed back before the most recent call."""
    contents = client.calls[-1]["contents"]
    fn_turn = contents[-1]
    assert fn_turn.role == "function"
    return fn_turn.parts[0].function_response.response["result"]


def test_loop_converges_on_second_try():
    # First proposal is a decade off, second is right.
    client = FakeGeminiClient([
        _with_values(RC_LOWPASS, C1="1u"),   # fc ~ 159 Hz -> FAIL
        RC_LOWPASS,                          # fc ~ 1591 Hz -> PASS
    ])
    run = run_agent_loop(DesignSpec("lowpass", 1590), client=client, retry_policy=NO_WAIT, verbose=False)

    assert isinstance(run, AgentRunResult)
    assert run.passed is True
    assert run.iterations == 2
    assert len(run.history) == 2
    assert run.history[0]["result"]["passed"] is False
    assert run.final_result["passed"] is True
    assert run.final_circuit == RC_LOWPASS

    # The second model call must have received the real measured cutoff
    # of the first (failed) attempt as function-response feedback.
    fb = _last_function_response(client)
    assert fb["passed"] is False
    assert fb["cutoff_hz"] == pytest.approx(159, rel=0.05)
    assert "FAIL" in fb["message"]

    # The very first call carries the spec in the prompt.
    first_prompt = client.calls[0]["contents"][0].parts[0].text
    assert "lowpass" in first_prompt and "1590" in first_prompt


@pytest.mark.parametrize(
    "spec, bad, good",
    [
        (DesignSpec("highpass", 1590), _with_values(RC_HIGHPASS, R1="10k"), RC_HIGHPASS),
        (DesignSpec("bandpass", 5030), _with_values(RLC_BANDPASS, C1="1u"), RLC_BANDPASS),
        (DesignSpec("notch", 5030), _with_values(RLC_NOTCH, L1="1m"), RLC_NOTCH),
    ],
    ids=["highpass", "bandpass", "notch"],
)
def test_loop_handles_every_filter_type(spec, bad, good):
    client = FakeGeminiClient([bad, good])
    run = run_agent_loop(spec, client=client, retry_policy=NO_WAIT, verbose=False)
    assert run.passed is True
    assert run.iterations == 2
    assert run.history[0]["result"]["passed"] is False
    assert run.history[1]["result"]["passed"] is True


def test_loop_exhausts_budget():
    wrong = _with_values(RC_LOWPASS, C1="1u")
    client = FakeGeminiClient([wrong] * 3)
    run = run_agent_loop(DesignSpec("lowpass", 1590), client=client, retry_policy=NO_WAIT,
                         max_iterations=3, verbose=False)
    assert run.passed is False
    assert run.iterations == 3
    assert len(run.history) == 3
    assert len(client.calls) == 3


def test_loop_stops_when_model_gives_up_with_text():
    client = FakeGeminiClient([
        _with_values(RC_LOWPASS, C1="1u"),
        "I cannot meet this spec with passive components.",
    ])
    run = run_agent_loop(DesignSpec("lowpass", 1590), client=client, retry_policy=NO_WAIT, verbose=False)
    assert run.passed is False
    assert run.iterations == 2
    assert run.final_text.startswith("I cannot")
    assert len(run.history) == 1


def test_loop_recovers_from_invalid_circuit():
    no_ground = {
        "title": "floating",
        "components": [
            {"id": "V1", "type": "voltage_source", "nodes": ["in", "ref"], "value": "AC 1"},
            {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "1k"},
            {"id": "C1", "type": "capacitor", "nodes": ["out", "ref"], "value": "100n"},
        ],
    }
    client = FakeGeminiClient([no_ground, RC_LOWPASS])
    run = run_agent_loop(DesignSpec("lowpass", 1590), client=client, retry_policy=NO_WAIT, verbose=False)
    assert run.passed is True
    assert run.iterations == 2
    fb = _last_function_response(client)
    assert fb["simulation_failed"] is True
    assert "ground" in fb["message"]


def test_loop_uses_requested_model_name():
    client = FakeGeminiClient([RC_LOWPASS])
    run_agent_loop(DesignSpec("lowpass", 1590), client=client, retry_policy=NO_WAIT,
                   model_name="some-model", verbose=False)
    assert client.calls[0]["model"] == "some-model"


def test_loop_survives_transient_503():
    """End-to-end: the design loop converges even though the model API
    fails once between the two proposals."""
    ft = FakeTime()
    client = FakeGeminiClient([
        _with_values(RC_LOWPASS, C1="1u"),   # FAIL
        _err(503),                           # transient outage
        RC_LOWPASS,                          # PASS
    ])
    lim = _limiter(client, RetryPolicy(min_interval_s=2, initial_backoff_s=5, jitter_frac=0), ft)
    run = run_agent_loop(DesignSpec("lowpass", 1590), client=lim, verbose=False)
    assert run.passed is True
    assert run.iterations == 2               # the 503 is not a design iteration
    assert lim.requests == 3
    assert lim.retries == 1
    assert 5.0 in ft.sleeps


def test_loop_wraps_plain_client_in_rate_limiter(monkeypatch):
    """A bare client passed to run_agent_loop gets pacing for free."""
    # time.sleep is called inside the rate limiter, so that is where it
    # must be patched - not on the agent package.
    from spice_agent.agent import rate_limit

    client = FakeGeminiClient([_with_values(RC_LOWPASS, C1="1u"), RC_LOWPASS])
    slept = []
    monkeypatch.setattr(rate_limit.time, "sleep", lambda s: slept.append(s))
    # min_interval is large but the fake replies instantly (ngspice takes
    # well under a second), so the second call must wait most of it.
    run = run_agent_loop(DesignSpec("lowpass", 1590), client=client, verbose=False,
                         retry_policy=RetryPolicy(min_interval_s=30))
    assert run.passed
    assert len(slept) == 1 and 25 < slept[0] <= 30


live = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set — skipping live Gemini agent runs",
)


# Where live runs are recorded. A live run costs API calls and produces a
# real design, so throwing that away and keeping only PASSED/FAILED wastes
# the most interesting output of the whole suite. Every run is appended
# here as one JSON object per line, including the circuits proposed on
# every iteration, so a run can be inspected long after pytest exits.
LIVE_LOG = Path(os.environ.get(
    "SPICE_AGENT_LIVE_LOG",
    # tests/agent/test_loop.py -> repo root -> out/runs/live/
    Path(__file__).resolve().parents[2] / "out" / "runs" / "live" / "live_runs.jsonl",
))


def record_live_run(run) -> dict:
    """Append one run to LIVE_LOG and return the record."""
    record = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": MODEL_NAME,
        "spec": run.spec.to_dict(),
        "spec_text": run.spec.describe(),
        "passed": run.passed,
        "iterations": run.iterations,
        "final_circuit": run.final_circuit,
        "final_result": run.final_result,
        "final_text": run.final_text,
        "history": run.history,
    }
    LIVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LIVE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def summarize_live_run(run) -> str:
    """One readable block per run, shown with `pytest -s`."""
    lines = [f"\n[{run.spec.describe()}]"]
    for h in run.history:
        parts = " + ".join(
            f"{c['id']}={c['value']}" for c in h["circuit"]["components"]
            if c["type"] != "voltage_source"
        )
        verdict = "PASS" if h["result"].get("passed") else "fail"
        lines.append(f"  iter {h['iteration']} [{verdict}] {parts}")
        lines.append(f"           {h['result'].get('message', '')}")
    if run.final_text:
        lines.append(f"  model stopped with text: {run.final_text[:200]}")
    lines.append(f"  -> {'PASSED' if run.passed else 'FAILED'} "
                 f"after {run.iterations} iteration(s); logged to {LIVE_LOG}")
    return "\n".join(lines)


@live
@pytest.mark.live
@pytest.mark.parametrize(
    "spec",
    [
        DesignSpec("lowpass", 1000),
        DesignSpec("highpass", 3000),
        DesignSpec("bandpass", 10000),
        DesignSpec("notch", 2000),
        # The only live case with a stopband term, so the only one a
        # 1st-order design cannot satisfy. This is the case that actually
        # exercises the feedback loop rather than confirming a one-shot.
        DesignSpec("lowpass", 1000, stopband_hz=10000, stopband_atten_db=40),
    ],
    ids=lambda s: s.filter_type + ("_stopband" if s.stopband_hz else ""),
)
def test_live_agent_converges(spec):
    run = run_agent_loop(spec, verbose=False)
    record_live_run(run)
    print(summarize_live_run(run))

    assert run.passed, (
        f"Agent did not meet spec [{spec.describe()}] in {run.iterations} "
        f"iterations. Last result: {run.final_result}. Text: {run.final_text}"
    )
    assert run.final_result["error_pct"] == pytest.approx(0, abs=spec.tolerance_pct)
    if spec.stopband_hz is not None:
        assert run.final_result["stopband_atten_db"] >= spec.stopband_atten_db


# ---------------------------------------------------------------------
# What the model is sent (model_feedback / feedback_text)
# ---------------------------------------------------------------------

def test_feedback_is_rounded_but_the_log_keeps_full_precision():
    client = FakeGeminiClient([_with_values(RC_LOWPASS, C1="1u"), RC_LOWPASS])
    run = run_agent_loop(DesignSpec("lowpass", 1590), client=client,
                         retry_policy=NO_WAIT, verbose=False)
    sent = _last_function_response(client)["cutoff_hz"]
    logged = run.history[0]["result"]["cutoff_hz"]
    assert sent == float(f"{logged:.4g}")
    assert sent == pytest.approx(logged, rel=1e-3)


def test_model_feedback_drops_what_the_message_already_says():
    from spice_agent.agent.loop import feedback_text, model_feedback

    result = {"passed": False, "message": "FAIL. Diagnosis: ideal would pass.",
              "cutoff_hz": 8834.024632825794,
              "stopband_profile": [{"hz": 1e5, "atten_db": 38.94}],
              "parasitics": {"summary": "ideal would pass", "ideal_parts_passed": True,
                             "limiting_parasitics": [{"gain_db": 21.123456}]}}
    fb = model_feedback(result)
    assert fb["cutoff_hz"] == 8834.0
    assert "stopband_profile" not in fb
    assert "summary" not in fb["parasitics"]
    assert fb["parasitics"]["limiting_parasitics"][0]["gain_db"] == 21.12
    assert result["cutoff_hz"] == 8834.024632825794          # original untouched

    text = feedback_text(result)
    assert text.startswith("Simulation result: FAIL. Diagnosis")
    data = json.loads(text.split("Measured data: ", 1)[1])
    assert "message" not in data and data["cutoff_hz"] == 8834.0


def test_gemini_loop_stalls_on_repeated_designs():
    wrong = _with_values(RC_LOWPASS, C1="1u")
    client = FakeGeminiClient([wrong, wrong, wrong, RC_LOWPASS])
    run = run_agent_loop(DesignSpec("lowpass", 1590), client=client, retry_policy=NO_WAIT,
                         verbose=False)
    assert not run.passed and run.stalled
    assert len(client.calls) == 3
    assert run.history[1]["result"]["repeated_design"] is True
    assert "REPEATED DESIGN" in _last_function_response(client)["message"]
