"""
The Claude headless backend, driven by a scripted runner in place of
`claude -p`. Needs ngspice (like the Gemini loop tests) but no network
and no Claude subscription.
"""

import json

import pytest

from spice_agent.agent import claude_loop
from spice_agent.agent.claude_loop import (
    ClaudeCliError,
    ClaudeUsageLimitError,
    build_command,
    run_claude_loop,
)
from spice_agent.simulation import DesignSpec

from _support.circuits import RC_LOWPASS

SPEC = DesignSpec("lowpass", 1590.0)
BAD = {"title": "too slow", "components": [
    {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
    {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "10k"},
    {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "100n"},
]}


def envelope(circuit=None, **extra):
    return {"is_error": False, "structured_output": circuit,
            "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.001,
            **extra}


class ScriptedRunner:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def __call__(self, cmd, prompt):
        self.calls.append((cmd, prompt))
        return self.payloads.pop(0)


def test_converges_and_resumes_session():
    runner = ScriptedRunner(envelope(BAD), envelope(RC_LOWPASS))
    run = run_claude_loop(SPEC, runner=runner, verbose=False)
    assert run.passed and run.iterations == 2
    first, second = (c[0] for c in runner.calls)
    assert "--session-id" in first and "--resume" in second
    assert first[first.index("--session-id") + 1] == second[second.index("--resume") + 1]
    assert "Simulation result" in runner.calls[1][1]       # result fed back


def test_command_is_locked_down_and_cheap():
    cmd = build_command("claude", model="haiku", effort="low", session_args=[])
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert cmd[cmd.index("--tools") + 1] == ""
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--setting-sources"):
        assert flag in cmd


def test_reads_circuit_from_result_text_when_no_structured_output():
    runner = ScriptedRunner({"is_error": False, "result": "```json\n"
                             + json.dumps(RC_LOWPASS) + "\n```"})
    assert run_claude_loop(SPEC, runner=runner, verbose=False).passed


def test_garbage_reply_is_fed_back_not_fatal():
    runner = ScriptedRunner({"is_error": False, "result": "sorry, no"}, envelope(RC_LOWPASS))
    run = run_claude_loop(SPEC, runner=runner, verbose=False)
    assert run.passed and run.iterations == 2
    assert "not a valid circuit" in runner.calls[1][1]


def test_budget_exhausted():
    runner = ScriptedRunner(envelope(BAD), envelope(BAD))
    run = run_claude_loop(SPEC, runner=runner, max_iterations=2, verbose=False)
    assert not run.passed and run.iterations == 2 and len(runner.calls) == 2


def test_usage_limit_stops_immediately_without_retry():
    runner = ScriptedRunner({"is_error": True, "result": "Claude usage limit reached"},
                            envelope(RC_LOWPASS))
    run = run_claude_loop(SPEC, runner=runner, verbose=False)
    assert not run.passed and len(runner.calls) == 1
    assert "limit" in run.final_text


def test_auth_error_raises():
    runner = ScriptedRunner({"is_error": True, "result": "Not logged in - run /login"})
    with pytest.raises(ClaudeCliError) as e:
        run_claude_loop(SPEC, runner=runner, verbose=False)
    assert not isinstance(e.value, ClaudeUsageLimitError)


def test_find_claude_prefers_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_BIN", "/x/claude")
    assert claude_loop.find_claude() == "/x/claude"


def test_feedback_puts_the_message_before_the_numbers():
    runner = ScriptedRunner(envelope(BAD), envelope(RC_LOWPASS))
    run_claude_loop(SPEC, runner=runner, verbose=False)
    fed_back = runner.calls[1][1]
    assert fed_back.index("Simulation result:") < fed_back.index("Measured data:")
    assert "multiply EVERY inductor and capacitor" in fed_back   # BAD misses the cutoff
    data = json.loads(fed_back.split("Measured data: ", 1)[1].split("\n")[0])
    assert data["passed"] is False and "message" not in data
