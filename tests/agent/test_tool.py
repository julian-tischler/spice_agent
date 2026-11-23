"""
The simulate_circuit tool and the prompt that asks for it.

These check the harness's judgement, not the model's: given a known
circuit and a spec, does the tool return the right verdict, and does a
bad circuit come back as actionable feedback rather than an exception?
"""

import pytest

from spice_agent.agent import build_system_prompt, call_simulate_circuit_tool
from spice_agent.simulation import DesignSpec

from _support.circuits import (
    AC,
    DIVIDER,
    RC_HIGHPASS,
    RC_LOWPASS,
    RLC_BANDPASS,
    RLC_NOTCH,
    RLC_NOTCH_LOSSY,
    TWIN_T_NOTCH,
    _with_values,
)


# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "spec, circuit, expect_pass",
    [
        # right circuit for the right spec
        (DesignSpec("lowpass", 1590), RC_LOWPASS, True),
        (DesignSpec("highpass", 1590), RC_HIGHPASS, True),
        (DesignSpec("bandpass", 5030), RLC_BANDPASS, True),
        (DesignSpec("bandpass", 5030, bandwidth_hz=1590), RLC_BANDPASS, True),
        (DesignSpec("notch", 5030), RLC_NOTCH, True),
        (DesignSpec("notch", 1590, min_notch_depth_db=40), TWIN_T_NOTCH, True),
        # right topology, wrong frequency
        (DesignSpec("lowpass", 5000), RC_LOWPASS, False),
        (DesignSpec("highpass", 500), RC_HIGHPASS, False),
        (DesignSpec("bandpass", 1000), RLC_BANDPASS, False),
        (DesignSpec("bandpass", 5030, bandwidth_hz=500), RLC_BANDPASS, False),
        (DesignSpec("notch", 2000), RLC_NOTCH, False),
        # right frequency, too shallow a notch (the branch loss limits it)
        (DesignSpec("notch", 5030, min_notch_depth_db=40), RLC_NOTCH_LOSSY, False),
        (DesignSpec("notch", 5030, min_notch_depth_db=40), RLC_NOTCH, True),
        # wrong topology for the spec
        (DesignSpec("lowpass", 1590), RC_HIGHPASS, False),
        (DesignSpec("highpass", 1590), RC_LOWPASS, False),
        (DesignSpec("bandpass", 1590), RC_LOWPASS, False),
        (DesignSpec("notch", 1590), RC_LOWPASS, False),
        # not a filter at all
        (DesignSpec("lowpass", 1590), DIVIDER, False),
        (DesignSpec("highpass", 1590), DIVIDER, False),
        (DesignSpec("bandpass", 1590), DIVIDER, False),
        (DesignSpec("notch", 1590), DIVIDER, False),
    ],
    ids=lambda x: x.describe() if isinstance(x, DesignSpec) else (x["title"] if isinstance(x, dict) else str(x)),
)
def test_tool_verdict(spec, circuit, expect_pass):
    result = call_simulate_circuit_tool(circuit, spec)
    assert result["simulation_failed"] is False
    assert result["passed"] is expect_pass, result["message"]
    assert result["filter_type"] == spec.filter_type
    assert "message" in result and result["message"]


def test_result_is_json_serializable():
    import json
    for spec, circuit in [
        (DesignSpec("lowpass", 1590), RC_LOWPASS),
        (DesignSpec("bandpass", 5030, bandwidth_hz=1590), RLC_BANDPASS),
        (DesignSpec("notch", 5030), RLC_NOTCH),
    ]:
        json.dumps(call_simulate_circuit_tool(circuit, spec))  # must not raise


def test_measured_metrics_are_reported():
    lp = call_simulate_circuit_tool(RC_LOWPASS, DesignSpec("lowpass", 1590))
    assert lp["cutoff_hz"] == pytest.approx(1591, rel=0.02)
    assert lp["passband_gain_db"] == pytest.approx(0.0, abs=0.05)

    bp = call_simulate_circuit_tool(RLC_BANDPASS, DesignSpec("bandpass", 5030))
    assert bp["center_hz"] == pytest.approx(5033, rel=0.02)
    assert bp["bandwidth_hz"] == pytest.approx(1591, rel=0.05)
    assert bp["peak_gain_db"] == pytest.approx(0.0, abs=0.1)

    nt = call_simulate_circuit_tool(RLC_NOTCH, DesignSpec("notch", 5030))
    assert nt["notch_hz"] == pytest.approx(5033, rel=0.02)
    assert nt["notch_depth_db"] > 25


def test_wrong_topology_feedback_names_the_problem():
    r = call_simulate_circuit_tool(RC_HIGHPASS, DesignSpec("lowpass", 1590))
    assert "highpass" in r["message"]
    r = call_simulate_circuit_tool(RC_LOWPASS, DesignSpec("highpass", 1590))
    assert "lowpass" in r["message"]
    r = call_simulate_circuit_tool(DIVIDER, DesignSpec("bandpass", 1590))
    assert "not band-pass shaped" in r["message"]


def test_invalid_circuit_becomes_feedback_not_exception():
    no_ground = {
        "title": "floating",
        "components": [
            {"id": "V1", "type": "voltage_source", "nodes": ["in", "ref"], "value": "AC 1"},
            {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "1k"},
        ],
    }
    r = call_simulate_circuit_tool(no_ground, DesignSpec("lowpass", 1590))
    assert r["passed"] is False
    assert r["simulation_failed"] is True
    assert "Circuit definition error" in r["message"]
    assert "ground" in r["message"]

    bad_prefix = _with_values(RC_LOWPASS)
    bad_prefix["components"][1]["id"] = "X1"
    r = call_simulate_circuit_tool(bad_prefix, DesignSpec("lowpass", 1590))
    assert r["simulation_failed"] is True
    assert "X1" in r["message"]


# ---------------------------------------------------------------------
# 1b. Prompt content per spec
# ---------------------------------------------------------------------

@pytest.mark.parametrize("ftype", ["lowpass", "highpass", "bandpass", "notch"])
def test_prompt_mentions_spec(ftype):
    spec = DesignSpec(ftype, 2500, tolerance_pct=5)
    p = build_system_prompt(spec)
    assert ftype in p
    assert "2500" in p
    assert "5%" in p
    assert "simulate_circuit" in p
