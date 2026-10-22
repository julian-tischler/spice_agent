"""
The LLM design agent.

    rate_limit.py   pacing and retry around the model API
    analysis.py     which AC sweep to use for a spec
    tool_schema.py  the JSON schema the model fills in
    prompts.py      the design brief it receives
    loop.py         run_agent_loop - propose, simulate, feed back
    cli.py          argument parsing and post-run export

Import from the package; the split above is an implementation detail.
"""

from spice_agent.agent.analysis import (
    AC_ANALYSIS,
    PARASITIC_FSTOP,
    analysis_for,
)
from spice_agent.agent.cli import export_design, main, parse_args, run_circuit_json
from spice_agent.agent.loop import (
    DEFAULT_MAX_ITERATIONS,
    MODEL_NAME,
    AgentRunResult,
    call_simulate_circuit_tool,
    run_agent_loop,
)
from spice_agent.agent.prompts import (
    TOPOLOGY_HINTS,
    build_system_prompt,
    build_technology_note,
)
from spice_agent.agent.rate_limit import RateLimiter, RetryPolicy
from spice_agent.agent.tool_schema import SIMULATE_CIRCUIT_TOOL

__all__ = [
    "AC_ANALYSIS",
    "DEFAULT_MAX_ITERATIONS",
    "MODEL_NAME",
    "PARASITIC_FSTOP",
    "SIMULATE_CIRCUIT_TOOL",
    "TOPOLOGY_HINTS",
    "AgentRunResult",
    "RateLimiter",
    "RetryPolicy",
    "analysis_for",
    "build_system_prompt",
    "build_technology_note",
    "call_simulate_circuit_tool",
    "export_design",
    "main",
    "parse_args",
    "run_agent_loop",
    "run_circuit_json",
]
