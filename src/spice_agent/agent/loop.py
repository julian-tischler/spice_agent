"""
The design loop: propose, simulate, feed the real result back, repeat.

Manual function calling rather than the SDK's automatic mode, so the
iteration budget, the logging and the failure handling stay explicit.
The client is injectable, which is what lets the whole loop be tested
against a scripted fake with no network.
"""

import json
import os
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from spice_agent.agent.analysis import analysis_for
from spice_agent.agent.prompts import build_system_prompt
from spice_agent.agent.rate_limit import RateLimiter, RetryPolicy
from spice_agent.agent.tool_schema import SIMULATE_CIRCUIT_TOOL
from spice_agent.simulation import DesignSpec, simulate_and_verify


MODEL_NAME = "gemini-3.5-flash"  # swap for a stronger model later if needed
DEFAULT_MAX_ITERATIONS = 6


def call_simulate_circuit_tool(args: dict, spec: DesignSpec) -> dict:
    """
    Executes the tool: attaches the fixed AC analysis block, runs the
    simulation, checks against the spec, returns the result dict.
    """
    circuit_json = {
        "title": args.get("title", "LLM-generated circuit"),
        "components": args["components"],
        "analysis": analysis_for(spec),
    }

    try:
        result = simulate_and_verify(circuit_json, spec)
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


def run_agent_loop(
    spec: DesignSpec | None = None,
    *,
    client=None,
    model_name: str = MODEL_NAME,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    retry_policy: RetryPolicy | None = None,
    verbose: bool = True,
) -> AgentRunResult:
    """
    Run the design loop for `spec`. Returns an AgentRunResult whether or
    not the spec was met.

    `client` is any object exposing
        client.models.generate_content(model=, contents=, config=)
    with the google-genai response shape; pass a fake in tests. Unless it
    already is a RateLimiter it gets wrapped in one using `retry_policy`
    (default RetryPolicy()), so every Gemini call is paced and retried.
    """
    if spec is None:
        spec = DesignSpec()

    log = print if verbose else (lambda *a, **k: None)

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

    contents = [
        types.Content(role="user", parts=[types.Part(text=build_system_prompt(spec))])
    ]
    run = AgentRunResult(spec=spec, passed=False, iterations=0)

    for iteration in range(1, max_iterations + 1):
        run.iterations = iteration
        log(f"\n{'='*60}\nIteration {iteration}  [{spec.describe()}]\n{'='*60}")

        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
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
        for fc in function_calls:
            args = dict(fc.args)
            log(f"Model proposed circuit: {json.dumps(args, indent=2)}")

            result = call_simulate_circuit_tool(args, spec)
            log(f"Simulation result: {result}")

            run.history.append({"iteration": iteration, "circuit": args, "result": result})
            run.final_result = result
            run.final_circuit = args

            function_response_parts.append(
                types.Part.from_function_response(
                    name=fc.name,
                    response={"result": result},
                )
            )

            if result.get("passed"):
                run.passed = True
                log(f"\nPASS: spec met after {iteration} iteration(s).")
                return run

        contents.append(types.Content(role="function", parts=function_response_parts))

    else:
        log(f"\nFAIL: iteration budget ({max_iterations}) exhausted without meeting spec.")

    return run
