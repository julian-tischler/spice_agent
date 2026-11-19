"""
Parasitics diagnosis (spice_agent.simulation.diagnosis): when a design
fails on a non-ideal technology, the result says whether ideal parts would
have passed, which parasitic costs the most, and which parts self-resonate
inside the stopband.

Reference circuit: the textbook 3rd-order L-C-L ladder for 10 kHz, judged
40 dB from 100 kHz all the way to 100 MHz. That range is wider than the
default judged window (two decades: up to 1 MHz), so these specs ask for it
explicitly - judge_hi_hz / stopband_end_hz - which is exactly how a user
who cares about 100 MHz says so. Peaking is switched off here: driven from
an ideal source this ladder peaks +3.9 dB (it was designed for a source
resistance it does not get), which is a design issue tested elsewhere and
would otherwise be the check diagnosed first.

Margins are relative to the requirement (+ met by, - missed by), so the
stopband margin is the worst attenuation minus 40 dB. Measured (2026-11-12):
    ideal parts                         68.1 dB worst (PASS)
    breadboard, all parasitics          19.5 dB at 100 MHz (FAIL)
    breadboard, parasitic groups off    67.5 dB (losses kept)
    only input-output coupling           1.5 dB  -> the whole problem
    only inductor winding capacitance   61.5 dB  -> costs 6.0 dB
    only ESL / leads / node C           67.5 dB  -> cost nothing
    WITHOUT the coupling (rest on)      17.5 dB  -> removal misleads
Its 17.29 mH inductors self-resonate at 313 kHz (15 pF winding capacitance).
"""

import pytest

from spice_agent.agent.analysis import analysis_for
from spice_agent.circuit import BREADBOARD
from spice_agent.simulation import DesignSpec, simulate_and_verify

V1 = {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"}
LADDER = {"title": "3rd-order LC ladder, 10 kHz", "components": [
    V1,
    {"id": "L1", "type": "inductor", "nodes": ["in", "n1"], "value": "17.29m"},
    {"id": "C1", "type": "capacitor", "nodes": ["n1", "0"], "value": "34.56n"},
    {"id": "L2", "type": "inductor", "nodes": ["n1", "out"], "value": "17.29m"},
    {"id": "R1", "type": "resistor", "nodes": ["out", "0"], "value": "1k"}]}


def spec(technology):
    return DesignSpec("lowpass", 10_000, stopband_hz=100_000, stopband_atten_db=40,
                      stopband_end_hz=1e8, judge_hi_hz=1e8, max_peaking_db=None,
                      technology=technology)


def run(technology, **kw):
    s = spec(technology)
    return simulate_and_verify(dict(LADDER, analysis=analysis_for(s)), s, **kw)


@pytest.fixture(scope="module")
def breadboard():
    return run("breadboard")


def test_breadboard_failure_is_diagnosed(breadboard):
    assert breadboard["passed"] is False
    d = breadboard["parasitics"]
    assert d["technology"] == "breadboard"
    assert d["ideal_parts_passed"] is True
    assert d["ideal_stopband_atten_db"] == pytest.approx(68.1, abs=1.0)
    assert "Diagnosis:" in breadboard["message"]
    assert "With ideal parts this exact design would PASS" in breadboard["message"]


def test_limiting_parasitics_are_ranked_by_isolated_damage(breadboard):
    d = breadboard["parasitics"]
    assert d["check"] == "stopband" and d["unit"] == "dB"
    assert d["margin"] == pytest.approx(19.5 - 40, abs=1.0)
    assert d["parasitic_free_margin"] == pytest.approx(67.5 - 40, abs=1.0)
    top = d["limiting_parasitics"]
    assert [t["parasitic"] for t in top[:2]] == [
        "input-output stray coupling", "inductor self-resonance (winding capacitance)"]
    coupling, winding = top[:2]
    assert coupling["margin_with_only_it"] == pytest.approx(1.5 - 40, abs=1.0)
    assert winding["damage"] == pytest.approx(6.0, abs=1.0)
    for t in top:
        assert t["damage"] == pytest.approx(
            t["baseline_margin"] - t["margin_with_only_it"], abs=1e-6)
        assert t["advice"]
    assert "Biggest single cause: input-output stray coupling" in breadboard["message"]
    assert "from 67.5 dB worst attenuation (no parasitics) to 1.5 dB worst attenuation" \
        in breadboard["message"]


def test_removal_alone_would_have_misled(breadboard):
    """Why attribution is by isolation: taking the coupling away while the
    rest stays makes this ladder slightly WORSE, not better."""
    d = breadboard["parasitics"]
    coupling = d["limiting_parasitics"][0]
    assert coupling["margin_without_it"] < d["margin"]


def test_inductors_flagged_inside_the_stopband(breadboard):
    comps = {c["id"]: c for c in breadboard["parasitics"]["components"]}
    srf = BREADBOARD.inductor_srf_hz(17.29e-3)
    for lid in ("L1", "L2"):
        assert comps[lid]["self_resonance_hz"] == pytest.approx(srf, rel=1e-6)
        assert "inside the stopband" in comps[lid]["note"]
    assert "R1" not in comps                      # resistors are not listed


def test_ideal_technology_has_no_diagnosis():
    assert "parasitics" not in run("ideal")


def test_diagnosis_can_be_switched_off():
    r = run("breadboard", diagnose=False)
    assert r["passed"] is False and "parasitics" not in r
    assert "Diagnosis:" not in r["message"]


def test_passing_design_has_no_diagnosis():
    r = run("smd_pcb")                            # 48.9 dB at 46.8 MHz on smd_pcb
    assert r["passed"] is True and "parasitics" not in r


# Iteration 1 of the live claude-haiku run of 2026-11-12: a C-L-C ladder.
# Measured on breadboard, 100 kHz - 100 MHz: 14.8 dB worst, 46.0 dB with
# every parasitic group off. Every group ALONE costs ~0 dB, yet removing the
# winding capacitance wins back 21.4 dB and removing the leads 16.4 dB -
# the damage only exists in combination.
CLC = {"title": "C-L-C from the live run", "components": [
    V1,
    {"id": "C1", "type": "capacitor", "nodes": ["in", "0"], "value": "1.6u"},
    {"id": "L1", "type": "inductor", "nodes": ["in", "out"], "value": "320u"},
    {"id": "C2", "type": "capacitor", "nodes": ["out", "0"], "value": "1.6u"}]}


def test_interaction_damage_is_found_by_removal():
    s = spec("breadboard")
    r = simulate_and_verify(dict(CLC, analysis=analysis_for(s)), s)
    top = r["parasitics"]["limiting_parasitics"]
    assert [t["parasitic"] for t in top[:2]] == [
        "inductor self-resonance (winding capacitance)", "lead / interconnect inductance"]
    winding, leads = top[:2]
    assert winding["removal_gain"] == pytest.approx(21.4, abs=1.5)
    assert leads["removal_gain"] == pytest.approx(16.4, abs=1.5)
    for t in top:
        assert abs(t["damage"]) < 1.0                         # harmless alone
        assert t["impact"] == max(t["damage"], t["removal_gain"])
    assert "in combination with the other parasitics" in r["message"]



# Iteration 1 of the live lowpass run of 2026-11-19: a 10 ohm R-C-L-C design.
# Measured on breadboard: cutoff 6006 Hz (39.9% low) against 9991 Hz ideal;
# without inductor winding resistance 9844 Hz, fully lossless 9984 Hz. L1's
# winding resistance (4.35 ohm for 210 uH) is comparable to the 10 ohm the
# whole filter is built around. No parasitic PATH matters here - only losses.
LOW_Z = {"title": "R-C-L-C from the live run", "components": [
    V1,
    {"id": "R1", "type": "resistor", "nodes": ["in", "n1"], "value": "10"},
    {"id": "C1", "type": "capacitor", "nodes": ["n1", "0"], "value": "800n"},
    {"id": "L1", "type": "inductor", "nodes": ["n1", "out"], "value": "210u"},
    {"id": "C2", "type": "capacitor", "nodes": ["out", "0"], "value": "2.4u"}]}


def test_losses_are_named_in_a_low_impedance_design():
    s = DesignSpec("lowpass", 10_000, stopband_hz=100_000, stopband_atten_db=40,
                   technology="breadboard")
    r = simulate_and_verify(dict(LOW_Z, analysis=analysis_for(s)), s)
    d = r["parasitics"]
    assert r["cutoff_hz"] == pytest.approx(6006, rel=0.01)
    assert d["check"] == "frequency" and d["ideal_parts_passed"] is True
    (losses,) = d["limiting_parasitics"]
    assert losses["parasitic"].startswith("losses")
    assert losses["damage"] == pytest.approx(39.8, abs=1.0)
    assert d["lossless_margin"] == pytest.approx(9.9, abs=0.5)
    l1 = next(c for c in d["components"] if c["id"] == "L1")
    assert l1["series_resistance_ohm"] == pytest.approx(4.35, abs=0.01)
    assert "Winding resistance as built: L1 (210u) 4.35 ohm" in r["message"]
    assert "L1 (210u) alone would make the design PASS" in r["message"]
