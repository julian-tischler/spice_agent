"""
Requirements that decide passive vs active: what parts exist (inductance
limits per technology), a supply-current budget, rail- and budget-aware
grade advice, a neutral prompt, and --passive-only.

Measured first (ngspice-47, 2026-11-23):

    series RLC 22 mH / 470 pF / 615 ohm, 50 kHz band-pass on smd_pcb (the
        live run's last design): 48978 Hz, 4978 Hz, -1.04 dB - and NOT
        BUILDABLE, 22 mH > the 10 mH SMD limit
    series RLC 10 mH / 1.013 nF / 300 ohm on smd_pcb: within the limit
    unity Sallen-Key, 1 mA budget: FET3 draws 1.40 mA per rail (over), GP1
        0.50 mA (within) - the same on ideal technology, from the catalogue
    two GP1 stages, 0.8 mA budget: 1.00 mA (over)
    passive RC on a powered spec with a budget: 0 mA
    the live 50 kHz MFB (31k, 165, 1n, 1n, 62k) on smd_pcb: U1 is the limit;
        on +/-12 V no faster grade is rated (RR10 is 2.7-5.5 V); on
        +/-2.5 V with GP1 the advice names OPAMP_RR10 (10 MHz)
"""

import pytest

from spice_agent.agent.analysis import analysis_for
from spice_agent.circuit import BREADBOARD, IDEAL, SMD_PCB
from spice_agent.simulation import DesignSpec, simulate_and_verify

V1 = {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"}
SUP = dict(supply_pos_v=12, supply_neg_v=-12)


def c(i, t, n, v):
    return {"id": i, "type": t, "nodes": n, "value": v}


def opamp(part, inp, inn, out="out", i="U1"):
    return {"id": i, "type": "opamp", "part": part,
            "pins": {"in+": inp, "in-": inn, "vcc": "vcc", "vee": "vee", "out": out}}


def run(parts, spec):
    return simulate_and_verify(
        {"title": "t", "components": [V1] + parts, "analysis": analysis_for(spec)}, spec)


def sallen_key(part):
    return [c("R1", "resistor", ["in", "n1"], "10k"), c("R2", "resistor", ["n1", "n2"], "10k"),
            c("C1", "capacitor", ["n1", "out"], "2.25n"),
            c("C2", "capacitor", ["n2", "0"], "1.125n"), opamp(part, "n2", "out")]


def mfb_50k(part):
    return [c("R1", "resistor", ["in", "n1"], "31k"), c("R2", "resistor", ["n1", "0"], "165"),
            c("C1", "capacitor", ["n1", "n2"], "1n"), c("C2", "capacitor", ["n1", "out"], "1n"),
            c("R3", "resistor", ["n2", "out"], "62k"), opamp(part, "0", "n2")]


# ---------------------------------------------------------------------
# What exists
# ---------------------------------------------------------------------

def test_inductance_limits_per_technology():
    assert SMD_PCB.max_practical_inductance == 10e-3
    assert BREADBOARD.max_practical_inductance == 100e-3
    assert IDEAL.max_practical_inductance == float("inf")


def test_unbuildable_design_fails_but_is_still_simulated():
    rlc = [c("L1", "inductor", ["in", "n1"], "22m"), c("C1", "capacitor", ["n1", "out"], "470p"),
           c("R1", "resistor", ["out", "0"], "615")]
    r = run(rlc, DesignSpec("bandpass", 5e4, bandwidth_hz=5e3, passband_gain_db=0,
                            technology="smd_pcb", **SUP))
    assert r["passed"] is False
    assert r["message"].startswith("NOT BUILDABLE - L1 = 22 mH exceeds the 10 mH "
                                   "available on smd_pcb")
    assert r["center_hz"] == pytest.approx(48978, rel=0.01)        # still measured
    assert r["bandwidth_hz"] == pytest.approx(4978, rel=0.02)
    assert r["parasitics"]["design_failures"] == ["availability"]  # never a parasitic


def test_inductor_within_the_limit_is_fine():
    rlc = [c("L1", "inductor", ["in", "n1"], "10m"),
           c("C1", "capacitor", ["n1", "out"], "1.013n"), c("R1", "resistor", ["out", "0"], "300")]
    r = run(rlc, DesignSpec("bandpass", 5e4, bandwidth_hz=5e3, technology="smd_pcb"))
    assert r["availability"]["ok"] is True


def test_ideal_technology_has_no_size_limit():
    rlc = [c("L1", "inductor", ["in", "n1"], "1"), c("C1", "capacitor", ["n1", "out"], "10.13p"),
           c("R1", "resistor", ["out", "0"], "30k")]
    r = run(rlc, DesignSpec("bandpass", 5e4, bandwidth_hz=5e3))
    assert r["availability"]["ok"] is True and "NOT BUILDABLE" not in r["message"]


# ---------------------------------------------------------------------
# Supply-current budget
# ---------------------------------------------------------------------

@pytest.mark.parametrize("tech", ["smd_pcb", "ideal"])
def test_budget_separates_the_grades(tech):
    """Ideal technology simulates the op-amp as ideal (it draws nothing), so
    the grade's catalogue current is charged instead - same verdict."""
    spec = DesignSpec("lowpass", 1e4, technology=tech, max_supply_current_a=1e-3, **SUP)
    fet = run(sallen_key("OPAMP_FET3"), spec)
    assert fet["passed"] is False
    assert fet["supply"]["pos_a"] == pytest.approx(1.4e-3, rel=0.01)
    assert fet["supply"]["neg_a"] == pytest.approx(1.4e-3, rel=0.01)
    assert fet["message"].startswith("OVER BUDGET - draws 1.40 mA from the +12 V rail "
                                     "(limit 1 mA)")
    assert "U1 (OPAMP_FET3) 1.4 mA" in fet["message"]
    gp = run(sallen_key("OPAMP_GP1"), spec)
    assert gp["passed"] is True
    assert gp["supply"]["pos_a"] == pytest.approx(0.5e-3, rel=0.01)


def test_op_amps_add_up():
    two = sallen_key("OPAMP_GP1") + [
        c("R5", "resistor", ["out", "n5"], "10k"), c("C5", "capacitor", ["n5", "0"], "1n"),
        opamp("OPAMP_GP1", "n5", "o2", out="o2", i="U2")]
    r = run(two, DesignSpec("lowpass", 1e4, technology="smd_pcb",
                            max_supply_current_a=0.8e-3, **SUP))
    assert r["passed"] is False
    assert r["supply"]["pos_a"] == pytest.approx(1.0e-3, rel=0.01)


def test_passive_design_draws_nothing():
    rc = [c("R1", "resistor", ["in", "out"], "1.59k"), c("C1", "capacitor", ["out", "0"], "10n")]
    r = run(rc, DesignSpec("lowpass", 1e4, max_supply_current_a=1e-3, **SUP))
    assert r["passed"] is True
    assert r["supply"]["pos_a"] == 0.0 and r["supply"]["neg_a"] == 0.0


def test_budget_validation():
    with pytest.raises(ValueError, match="needs supply rails"):
        DesignSpec("lowpass", 1e4, max_supply_current_a=1e-3)
    with pytest.raises(ValueError, match="must be positive"):
        DesignSpec("lowpass", 1e4, max_supply_current_a=0, **SUP)
    assert "at most 1 mA from each supply rail" in DesignSpec(
        "lowpass", 1e4, max_supply_current_a=1e-3, **SUP).describe()


# ---------------------------------------------------------------------
# Grade advice that respects rails and budget
# ---------------------------------------------------------------------

def test_no_faster_grade_on_these_rails_says_so():
    r = run(mfb_50k("OPAMP_FET3"), DesignSpec("bandpass", 5e4, bandwidth_hz=5e3,
                                              passband_gain_db=0, technology="smd_pcb", **SUP))
    assert "U1 (OPAMP_FET3) alone would make the design PASS" in r["message"]
    assert ("no grade faster than OPAMP_FET3 (3 MHz) is rated for 12 V / -12 V; rule of "
            "thumb: lower the gain or Q per stage") in r["message"]


def test_faster_grade_is_named_when_one_fits():
    r = run(mfb_50k("OPAMP_GP1"), DesignSpec("bandpass", 5e4, bandwidth_hz=5e3,
                                             passband_gain_db=0, technology="smd_pcb",
                                             supply_pos_v=2.5, supply_neg_v=-2.5))
    assert ("faster grades rated for 2.5 V / -2.5 V: OPAMP_RR10 (10 MHz)"
            in r["message"])


def test_budget_removes_grades_from_the_advice():
    from spice_agent.simulation.diagnosis import grade_advice

    spec = DesignSpec("bandpass", 5e4, bandwidth_hz=5e3, supply_pos_v=2.5,
                      supply_neg_v=-2.5, max_supply_current_a=0.6e-3)
    assert "no grade faster than OPAMP_GP1" in grade_advice("OPAMP_GP1", spec)
    assert "within 0.6 mA" in grade_advice("OPAMP_GP1", spec)


# ---------------------------------------------------------------------
# Neutral prompt and --passive-only
# ---------------------------------------------------------------------

def test_prompt_is_neutral_and_quotes_this_specs_numbers():
    from spice_agent.agent import build_system_prompt

    p = build_system_prompt(DesignSpec("bandpass", 5e4, bandwidth_hz=5e3, passband_gain_db=0,
                                       technology="smd_pcb", max_supply_current_a=1e-3, **SUP))
    assert "Design a filter meeting this spec" in p
    assert "Neither passive nor active is preferred" in p
    assert "Passive option:" in p and "Active option:" in p
    assert "Q = 10: about 31.8 mH" in p
    assert "inductors are available only up to 10 mH on smd_pcb (enforced)" in p
    assert "at most 1 mA may be drawn from each rail" in p
    assert "supply current 1.4 mA" in p                               # FET3 in the menu
    gain = build_system_prompt(DesignSpec("lowpass", 1e4, passband_gain_db=6, **SUP))
    assert "a gain of 6 dB needs an op-amp" in gain


def test_passive_only_spec():
    from spice_agent.agent import build_system_prompt
    from spice_agent.agent.loop import call_simulate_circuit_tool

    s = DesignSpec("lowpass", 1e4, allow_active=False, **SUP)
    assert not s.active_allowed and "passive parts only" in s.describe()
    p = build_system_prompt(s)
    assert "only (the user asked for passive parts only)" in p
    assert "Active option" not in p and "OPAMP_GP1" not in p
    r = call_simulate_circuit_tool({"title": "t", "components": [V1] + sallen_key("OPAMP_GP1")},
                                   s)
    assert r["simulation_failed"] is True and "passive parts only" in r["message"]


def test_cli_flags(monkeypatch):
    import spice_agent.agent.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "run_agent_loop", lambda spec, **kw: seen.setdefault("s", spec)
                        and cli.AgentRunResult(spec=spec, passed=False, iterations=0))
    cli.main(["--no-log", "--target", "1e4", "--supply", "12",
              "--max-supply-current", "1m", "--passive-only"])
    s = seen["s"]
    assert s.max_supply_current_a == pytest.approx(1e-3) and s.allow_active is False


def test_passive_gain_hint_names_only_passive_levers():
    """The passive-only live run (2026-11-23) was told about op-amp gain
    resistors it was not allowed to use. Measured: 10 mH / 1 nF / 308 ohm on
    smd_pcb peaks at -1.03 dB."""
    rlc = [c("L1", "inductor", ["in", "n1"], "10m"), c("C1", "capacitor", ["n1", "out"], "1n"),
           c("R1", "resistor", ["out", "0"], "308")]
    for spec in (DesignSpec("bandpass", 5e4, bandwidth_hz=5e3, passband_gain_db=0,
                            technology="smd_pcb", allow_active=False, **SUP),
                 DesignSpec("bandpass", 5e4, bandwidth_hz=5e3, passband_gain_db=0,
                            technology="smd_pcb")):                 # no rails at all
        r = run(rlc, spec)
        assert r["passband_gain_error_db"] == pytest.approx(-1.03, abs=0.05)
        assert "Rf/Rg" not in r["message"] and "MFB" not in r["message"]
        assert "with passive parts only the gain can be at most 0 dB" in r["message"]
        assert "winding resistance" in r["message"]
    active = run(rlc, DesignSpec("bandpass", 5e4, bandwidth_hz=5e3, passband_gain_db=0,
                                 technology="smd_pcb", **SUP))
    assert "1 + Rf/Rg" in active["message"]
