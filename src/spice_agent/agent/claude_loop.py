"""
The same design loop as loop.py, driven by Claude Code's headless mode
(`claude -p`) so it runs on a Claude Pro/Max subscription instead of the
Gemini API.

Differences from the Gemini loop:
  * No native function calling. The model is forced to answer with JSON
    matching the circuit schema (--json-schema), and the harness runs the
    simulation itself, exactly as call_simulate_circuit_tool does.
  * One conversation across iterations via --session-id / --resume, so
    each turn only sends the new simulation result (prompt-cached).

Usage-limit hygiene (subscription quotas are shared with interactive use):
  * cheap model (haiku) at low effort by default
  * every built-in tool, MCP server, skill, hook and settings file is
    switched off and the call runs from an empty temp dir, so Claude Code
    does not load CLAUDE.md or tool definitions into each request
  * a hard cap on calls (max_iterations)
  * token / cost use is tallied and printed
  * a usage-limit or auth error aborts the run immediately - no retry loop
    that would burn what is left of the quota
"""

import glob
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field

from spice_agent.agent.loop import (
    MAX_CONSECUTIVE_REPEATS,
    AgentRunResult,
    RepeatGuard,
    call_simulate_circuit_tool,
    feedback_text,
    repeated_result,
)
from spice_agent.agent.prompts import build_system_prompt
from spice_agent.agent.tool_schema import CIRCUIT_JSON_SCHEMA
from spice_agent.runlog import RunRecorder
from spice_agent.simulation import DesignSpec


CLAUDE_MODEL = "haiku"          # alias; any `claude --model` value works
CLAUDE_EFFORT = "low"
DEFAULT_CLAUDE_ITERATIONS = 5
CALL_TIMEOUT_S = 180

CIRCUIT_SCHEMA = CIRCUIT_JSON_SCHEMA

_LIMIT_MARKERS = ("usage limit", "rate limit", "limit reached", "quota",
                  "overloaded", "429")
_AUTH_MARKERS = ("not logged in", "/login", "authentication", "invalid api key",
                 "oauth")


class ClaudeCliError(RuntimeError):
    """The claude CLI failed in a way retrying will not fix."""


class ClaudeUsageLimitError(ClaudeCliError):
    """Subscription usage / rate limit hit. Stop and wait for the reset."""


@dataclass
class UsageTally:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0       # notional API-equivalent; not billed on a subscription

    def add(self, payload: dict) -> None:
        u = payload.get("usage") or {}
        self.calls += 1
        self.input_tokens += (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
        self.output_tokens += u.get("output_tokens") or 0
        self.cache_read_tokens += u.get("cache_read_input_tokens") or 0
        self.cost_usd += payload.get("total_cost_usd") or 0.0

    def summary(self) -> str:
        return (f"{self.calls} call(s), {self.input_tokens:,} input + "
                f"{self.output_tokens:,} output tokens "
                f"({self.cache_read_tokens:,} cached), ~${self.cost_usd:.4f} API-equivalent")


def _usage_of(payload: dict) -> dict:
    u = payload.get("usage") or {}
    return {
        "input_tokens": (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
        "output_tokens": u.get("output_tokens") or 0,
        "cache_read_tokens": u.get("cache_read_input_tokens") or 0,
        "cost_usd": payload.get("total_cost_usd") or 0.0,
    }


def find_claude() -> str:
    """CLAUDE_BIN, then PATH, then the binary bundled with the VS Code extension."""
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    pattern = os.path.join(os.path.expanduser("~"), ".vscode", "extensions",
                           "anthropic.claude-code-*", "resources", "native-binary",
                           "claude*")
    hits = sorted(h for h in glob.glob(pattern) if h.lower().endswith(("claude", "claude.exe")))
    if hits:
        return hits[-1]
    raise ClaudeCliError(
        "Could not find the claude CLI. Install Claude Code or set CLAUDE_BIN "
        "to the executable's path.")


def build_command(binary: str, *, model: str, effort: str, session_args: list[str]) -> list[str]:
    return [
        binary, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(CIRCUIT_SCHEMA),
        "--model", model,
        "--effort", effort,
        "--tools", "",                 # no Bash/Read/Edit/... - pure text in, JSON out
        "--strict-mcp-config",         # ignore configured MCP servers
        "--disable-slash-commands",    # no skills
        "--setting-sources", "",       # no user/project settings or hooks
        *session_args,
    ]


def _run_cli(cmd: list[str], prompt: str) -> dict:
    """Default runner: pipe the prompt on stdin, return the parsed JSON envelope."""
    with tempfile.TemporaryDirectory(prefix="spice_agent_claude_") as cwd:
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  encoding="utf-8", timeout=CALL_TIMEOUT_S, cwd=cwd)
        except subprocess.TimeoutExpired as e:
            raise ClaudeCliError(f"claude -p timed out after {CALL_TIMEOUT_S}s") from e

    raw = proc.stdout.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"is_error": True, "result": raw or proc.stderr.strip()}
    if proc.returncode != 0 and not payload.get("is_error"):
        payload["is_error"] = True
        payload.setdefault("result", proc.stderr.strip())
    return payload


def _check_error(payload: dict) -> None:
    if not payload.get("is_error"):
        return
    text = str(payload.get("result") or payload.get("error") or payload)
    low = text.lower()
    if any(m in low for m in _LIMIT_MARKERS):
        raise ClaudeUsageLimitError(
            f"Claude usage/rate limit reached - stop and retry after the reset: {text}")
    if any(m in low for m in _AUTH_MARKERS):
        raise ClaudeCliError(f"Claude CLI is not authenticated (run `claude` and /login): {text}")
    raise ClaudeCliError(f"claude -p failed: {text}")


def _extract_circuit(payload: dict) -> dict:
    """Pull the circuit dict out of the envelope (structured output, else result text)."""
    circuit = payload.get("structured_output")
    if circuit is None:
        text = str(payload.get("result") or "").strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            circuit = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError(f"model did not return circuit JSON: {text[:200]!r}")
    if not isinstance(circuit, dict) or "components" not in circuit:
        raise ValueError("model JSON has no 'components' list")
    return circuit


FIRST_TURN_SUFFIX = (
    "\n\nYou have no tools. Reply with ONLY the circuit as JSON matching the "
    "provided schema; it will be simulated for you and the result sent back."
)


def run_claude_loop(
    spec: DesignSpec | None = None,
    *,
    model_name: str = CLAUDE_MODEL,
    effort: str = CLAUDE_EFFORT,
    max_iterations: int = DEFAULT_CLAUDE_ITERATIONS,
    runner=None,
    binary: str | None = None,
    verbose: bool = True,
    recorder: RunRecorder | None = None,
    diagnose: str = "full",
) -> AgentRunResult:
    """
    Run the design loop for `spec` through `claude -p`. Returns an
    AgentRunResult (same shape as run_agent_loop). `runner(cmd, prompt)`
    is injectable for tests and must return the parsed JSON envelope.
    """
    if spec is None:
        spec = DesignSpec()
    log = print if verbose else (lambda *a, **k: None)
    recorder = recorder or RunRecorder.disabled()
    runner = runner or _run_cli
    binary = binary or (find_claude() if runner is _run_cli else "claude")

    session_id = str(uuid.uuid4())
    guard = RepeatGuard()
    tally = UsageTally()
    run = AgentRunResult(spec=spec, passed=False, iterations=0)
    system_prompt = build_system_prompt(spec)
    recorder.set_system_prompt(system_prompt + FIRST_TURN_SUFFIX)
    prompt = system_prompt + FIRST_TURN_SUFFIX

    try:
        for iteration in range(1, max_iterations + 1):
            run.iterations = iteration
            log(f"\n{'='*60}\nIteration {iteration}  [{spec.describe()}]  (claude/{model_name})\n{'='*60}")

            session_args = (["--session-id", session_id] if iteration == 1
                            else ["--resume", session_id])
            cmd = build_command(binary, model=model_name, effort=effort,
                                session_args=session_args)
            t0 = time.monotonic()
            payload = runner(cmd, prompt)
            tally.add(payload)
            recorder.record_model_call(
                iteration, raw=payload, prompt=prompt, duration_s=time.monotonic() - t0,
                command={"backend": "claude", "argv": cmd, "stdin": "prompt.txt"},
                usage=_usage_of(payload))
            _check_error(payload)

            try:
                args = _extract_circuit(payload)
            except ValueError as e:
                log(f"Bad model output: {e}")
                result = {"passed": False, "simulation_failed": True,
                          "message": f"Your reply was not a valid circuit: {e}. "
                                     "Reply with only the circuit JSON."}
                run.history.append({"iteration": iteration, "circuit": None, "result": result})
                recorder.record_simulation(iteration, None, result)
                run.final_result = result
                prompt = feedback_text(result)
                continue

            log(f"Model proposed circuit: {json.dumps(args, indent=2)}")
            capture: dict = {}
            earlier = guard.check(args)
            if earlier:
                result = repeated_result(earlier, guard)
            else:
                result = call_simulate_circuit_tool(args, spec, capture=capture,
                                                    diagnose=diagnose)
                guard.remember(iteration, args, result)
            log(f"Simulation result: {result}")
            recorder.record_circuit(iteration, args)
            recorder.record_simulation(iteration, capture, result)

            run.history.append({"iteration": iteration, "circuit": args, "result": result})
            run.final_result = result
            run.final_circuit = args

            if result.get("passed"):
                run.passed = True
                log(f"\nPASS: spec met after {iteration} iteration(s).")
                break
            if guard.stalled:
                run.stalled = True
                run.final_text = (f"stalled: an already-simulated design was resubmitted "
                                  f"{MAX_CONSECUTIVE_REPEATS} times in a row")
                log(f"\nSTALLED: {run.final_text}")
                break
            prompt = feedback_text(result) + "\nPropose a corrected circuit (JSON only)."
        else:
            log(f"\nFAIL: iteration budget ({max_iterations}) exhausted without meeting spec.")
    except ClaudeUsageLimitError as e:
        log(f"\nSTOPPED: {e}")
        run.final_text = str(e)
    finally:
        log(f"Claude usage: {tally.summary()}")

    return run
