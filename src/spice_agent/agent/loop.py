"""
The design loop: propose, simulate, feed the real result back, repeat.

Manual function calling rather than the SDK's automatic mode, so the
iteration budget, the logging and the failure handling stay explicit.
The client is injectable, which is what lets the whole loop be tested
against a scripted fake with no network.
"""

import json
import os
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from spice_agent.agent.analysis import analysis_for
from spice_agent.agent.prompts import build_system_prompt
from spice_agent.agent.rate_limit import RateLimiter, RetryPolicy
from spice_agent.agent.tool_schema import SIMULATE_CIRCUIT_TOOL
from spice_agent.models import SELECTABLE_OPAMPS
from spice_agent.hints import render
from spice_agent.runlog import RunRecorder
from spice_agent.simulation import DesignSpec, simulate_and_verify


def _raw_response(response):
    """The model's reply as JSON-able data, for the run log."""
    dump = getattr(response, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json", exclude_none=True)
        except Exception:
            pass
    cand = response.candidates[0].content if getattr(response, "candidates", None) else None
    return {"content": cand.model_dump(mode="json", exclude_none=True)
            if hasattr(cand, "model_dump") else repr(response),
            "text": getattr(response, "text", None)}


def _gemini_usage(response) -> dict | None:
    u = getattr(response, "usage_metadata", None)
    if u is None:
        return None
    return {"input_tokens": getattr(u, "prompt_token_count", None),
            "output_tokens": getattr(u, "candidates_token_count", None),
            "total_tokens": getattr(u, "total_token_count", None)}


FEEDBACK_SIG_FIGS = 4


def _round_sig(x: float, sig: int = FEEDBACK_SIG_FIGS) -> float:
    if x == 0 or x != x or x in (float("inf"), float("-inf")):
        return x
    return float(f"{x:.{sig}g}")


def _rounded(obj):
    if isinstance(obj, float):
        return _round_sig(obj)
    if isinstance(obj, dict):
        return {k: _rounded(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rounded(v) for v in obj]
    return obj


def model_feedback(result: dict) -> dict:
    """
    The result as the MODEL sees it. The run log keeps the full-precision
    result; the model gets floats rounded to 4 significant figures (nobody
    designs to 8834.024632825794 Hz) and no field that the message already
    spells out in words, so every iteration costs fewer tokens.
    """
    fb = _rounded(result)
    fb.pop("stopband_profile", None)          # rendered in the message
    if isinstance(fb.get("parasitics"), dict):
        fb["parasitics"] = {k: v for k, v in fb["parasitics"].items() if k != "summary"}
    return fb


def feedback_text(result: dict) -> str:
    """Plain-text feedback for a backend without function calling: the
    verdict in words first, the measured numbers after."""
    fb = model_feedback(result)
    message = fb.pop("message", "")
    return f"Simulation result: {message}\n\nMeasured data: {json.dumps(fb)}"


MODEL_NAME = "gemini-3.5-flash"  # swap for a stronger model later if needed
DEFAULT_MAX_ITERATIONS = 6


# Consecutive identical designs that end a run as "stalled": the first
# repeat is answered (not simulated) with a warning, the second ends it.
MAX_CONSECUTIVE_REPEATS = 2


def design_key(args: dict):
    """
    A design's identity for repeat detection: the components as (type,
    unordered nodes, numeric value), ignoring ids, order and title. Two
    proposals with the same key simulate identically. None if the proposal
    cannot be read (it then fails validation anyway).
    """
    from spice_agent.units import parse_spice_value

    try:
        parts = []
        for c in args["components"]:
            if "pins" in c:          # multi-pin: pin order matters (in+ vs in-)
                parts.append((c["type"], c.get("part"), tuple(sorted(c["pins"].items()))))
                continue
            value = c["value"]
            if c["type"] not in ("voltage_source", "current_source"):
                value = float(f"{parse_spice_value(value):.6g}")
            parts.append((c["type"], tuple(sorted(c["nodes"])), value))
        return tuple(sorted(parts, key=repr))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


class RepeatGuard:
    """Spots a proposal the loop has already simulated (see design_key)."""

    def __init__(self):
        self.seen: dict = {}
        self.consecutive = 0

    def check(self, args: dict) -> dict | None:
        """The earlier {iteration, result} for an identical design, or None."""
        key = design_key(args)
        earlier = self.seen.get(key) if key is not None else None
        self.consecutive = self.consecutive + 1 if earlier else 0
        return earlier

    def remember(self, iteration: int, args: dict, result: dict) -> None:
        key = design_key(args)
        if key is not None and key not in self.seen:
            self.seen[key] = {"iteration": iteration, "result": result}

    @property
    def stalled(self) -> bool:
        return self.consecutive >= MAX_CONSECUTIVE_REPEATS


def repeated_result(earlier: dict, guard: RepeatGuard) -> dict:
    """The answer to a resubmitted design: not simulated again."""
    msg = render("loop.repeated_design", n=earlier["iteration"],
                 message=earlier["result"].get("message", ""))
    if guard.consecutive >= MAX_CONSECUTIVE_REPEATS - 1:
        msg += (" One more identical submission ends the run."
                if not guard.stalled else " This was the second in a row: the run stops.")
    return {"passed": False, "simulation_failed": False, "repeated_design": True,
            "repeat_of": earlier["iteration"], "message": msg}


def call_simulate_circuit_tool(args: dict, spec: DesignSpec, capture: dict | None = None,
                               diagnose: str = "full") -> dict:
    """
    Executes the tool: attaches the fixed AC analysis block, runs the
    simulation, checks against the spec, returns the result dict.
    `capture` (optional) collects the simulation artifacts for run logging;
    `diagnose` is the parasitics-diagnosis level ("full", "basic", "off").
    """
    circuit_json = {
        "title": args.get("title", "LLM-generated circuit"),
        "components": args["components"],
        "analysis": analysis_for(spec),
    }
    if capture is not None:
        capture["circuit_json"] = circuit_json

    if (spec.powered and not spec.allow_active
            and any(isinstance(c, dict) and c.get("type") == "opamp"
                    for c in args["components"])):
        return {"passed": False, "simulation_failed": True,
                "message": render("loop.reject_passive_only")}
    ungraded = sorted({c.get("part") for c in args["components"]
                       if isinstance(c, dict) and c.get("type") == "opamp"
                       and c.get("part") not in SELECTABLE_OPAMPS})
    if ungraded:
        return {"passed": False, "simulation_failed": True,
                "message": render("loop.reject_ideal_opamp", parts=ungraded,
                                  grades=list(SELECTABLE_OPAMPS))}

    try:
        result = simulate_and_verify(circuit_json, spec, capture=capture, diagnose=diagnose)
    except (ValueError, KeyError, TypeError) as e:
        # Circuit.validate() or malformed JSON — turn this into feedback
        # the model can act on, rather than crashing the loop.
        result = {
            "passed": False,
            "simulation_failed": True,
            "message": f"Circuit definition error: {e}",
        }

    return result


@dataclass
class AgentRunResult:
    spec: DesignSpec
    passed: bool
    iterations: int
    final_result: dict | None = None      # last simulate_circuit result
    final_circuit: dict | None = None     # the tool args that produced it
    final_text: str | None = None         # model's text if it stopped calling tools
    history: list[dict] = field(default_factory=list)  # one entry per tool call
    stalled: bool = False                 # ended by repeated identical designs


def run_agent_loop(
    spec: DesignSpec | None = None,
    *,
    client=None,
    model_name: str = MODEL_NAME,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    retry_policy: RetryPolicy | None = None,
    verbose: bool = True,
    recorder: RunRecorder | None = None,
    diagnose: str = "full",
) -> AgentRunResult:
    """
    Run the design loop for `spec`. Returns an AgentRunResult whether or
    not the spec was met. `diagnose` is the parasitics-diagnosis level.

    `client` is any object exposing
        client.models.generate_content(model=, contents=, config=)
    with the google-genai response shape; pass a fake in tests. Unless it
    already is a RateLimiter it gets wrapped in one using `retry_policy`
    (default RetryPolicy()), so every Gemini call is paced and retried.
    """
    if spec is None:
        spec = DesignSpec()

    log = print if verbose else (lambda *a, **k: None)
    recorder = recorder or RunRecorder.disabled()

    if client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("Set the GEMINI_API_KEY environment variable first.")
        client = genai.Client(api_key=api_key)
    if not isinstance(client, RateLimiter):
        client = RateLimiter(client, retry_policy, log=log)

    tool = types.Tool(function_declarations=[SIMULATE_CIRCUIT_TOOL])
    config = types.GenerateContentConfig(
        tools=[tool],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    system_prompt = build_system_prompt(spec)
    recorder.set_system_prompt(system_prompt)
    contents = [types.Content(role="user", parts=[types.Part(text=system_prompt)])]
    run = AgentRunResult(spec=spec, passed=False, iterations=0)
    guard = RepeatGuard()

    for iteration in range(1, max_iterations + 1):
        run.iterations = iteration
        log(f"\n{'='*60}\nIteration {iteration}  [{spec.describe()}]\n{'='*60}")

        t0 = time.monotonic()
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )
        recorder.record_model_call(
            iteration, raw=_raw_response(response), usage=_gemini_usage(response),
            duration_s=time.monotonic() - t0,
            command={"backend": "gemini", "model": model_name},
        )

        candidate_content = response.candidates[0].content
        contents.append(candidate_content)

        function_calls = [
            part.function_call for part in candidate_content.parts
            if part.function_call is not None
        ]

        if not function_calls:
            # Model responded with text only — treat as final answer / give-up.
            run.final_text = response.text
            log("Model response (no tool call):")
            log(response.text)
            break

        # Handle each function call in this turn (usually just one)
        function_response_parts = []
        for call_no, fc in enumerate(function_calls, start=1):
            args = dict(fc.args)
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
            recorder.record_circuit(iteration, args, call=call_no)
            recorder.record_simulation(iteration, capture, result, call=call_no)

            run.history.append({"iteration": iteration, "circuit": args, "result": result})
            run.final_result = result
            run.final_circuit = args

            function_response_parts.append(
                types.Part.from_function_response(
                    name=fc.name,
                    response={"result": model_feedback(result)},
                )
            )

            if result.get("passed"):
                run.passed = True
                log(f"\nPASS: spec met after {iteration} iteration(s).")
                return run
            if guard.stalled:
                run.stalled = True
                run.final_text = (f"stalled: an already-simulated design was resubmitted "
                                  f"{MAX_CONSECUTIVE_REPEATS} times in a row")
                log(f"\nSTALLED: {run.final_text}")
                return run

        contents.append(types.Content(role="function", parts=function_response_parts))

    else:
        log(f"\nFAIL: iteration budget ({max_iterations}) exhausted without meeting spec.")

    return run
