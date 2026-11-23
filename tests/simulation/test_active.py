"""
Active parts, stage 1: op-amps from the model library, harness-owned supply
rails, the DC operating-point (bias) check and the passband-gain target.

The model chooses an op-amp GRADE (GP1, FET3, RR10), as it chooses a value
for a passive; the technology decides the physics. Ideal technology
simulates every op-amp as the ideal one, a real technology uses the grade's
macromodel. Hence most "real op-amp" fixtures below run on smd_pcb.

Measured first (ngspice-47, 2026-11-23):

    follower -3 dB point = each model's GBW: IDEAL 998 MHz (ideal
        technology), on smd_pcb GP1 ~1 MHz, FET3 ~3 MHz, RR10 ~10 MHz
    unity-gain Sallen-Key LP (10k, 10k, 2.25n, 1.125n), GP1 grade, +/-12 V:
        ideal 9992 Hz, no supply current (ideal op-amp);
        smd_pcb 9983 Hz, output -0.001 V at DC, supply 0.5 mA
    capacitor-coupled follower (100n into in+, no resistor), GP1 grade:
        smd_pcb: output -10.53 V at DC - saturated at the -10.5 V limit;
        ideal: not saturated (no bias current) but still no DC path - fails
    MFB band-pass (7.96k, 162.4, 10n, 10n, 15.9k), f0 10 kHz, B 2 kHz, 0 dB:
        ideal (any grade) 10000 Hz / 1997 Hz; smd_pcb FET3 9772 / 1937 (PASS);
        smd_pcb GP1 9550 / 1788 (-10.6%, FAIL) - per component, U1 made
        ideal alone passes: its 1 MHz gain-bandwidth is the limit
"""

import pytest

from spice_agent.agent.analysis import analysis_for
from spice_agent.circuit import Circuit, Component
from spice_agent.models import OPAMPS, get_opamp
from spice_agent.simulation import (
    DesignSpec,
    metrics,
    simulate_and_verify,
    simulate_get_full_sweep,
)

V1 = {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"}
SUP = dict(supply_pos_v=12, supply_neg_v=-12)


def c(i, t, n, v):
    return {"id": i, "type": t, "nodes": n, "value": v}


def opamp(part, inp, inn, out="out", i="U1", vcc="vcc", vee="vee"):
    return {"id": i, "type": "opamp", "part": part,
            "pins": {"in+": inp, "in-": inn, "vcc": vcc, "vee": vee, "out": out}}


def run(parts, spec, **kw):
    return simulate_and_verify(
        {"title": "t", "components": [V1] + parts, "analysis": analysis_for(spec)}, spec, **kw)


SALLEN_KEY = [c("R1", "resistor", ["in", "n1"], "10k"),
              c("R2", "resistor", ["n1", "n2"], "10k"),
              c("C1", "capacitor", ["n1", "out"], "2.25n"),
              c("C2", "capacitor", ["n2", "0"], "1.125n"),
              opamp("OPAMP_GP1", "n2", "out")]


def mfb(part):
    return [c("R1", "resistor", ["in", "n1"], "7.96k"),
            c("R2", "resistor", ["n1", "0"], "162.4"),
            c("C1", "capacitor", ["n1", "n2"], "10n"),
            c("C2", "capacitor", ["n1", "out"], "10n"),
            c("R3", "resistor", ["n2", "out"], "15.9k"),
            opamp(part, "0", "n2")]


MFB_SPEC = DesignSpec("bandpass", 1e4, bandwidth_hz=2e3, passband_gain_db=0, **SUP)


# ---------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------

@pytest.mark.parametrize("name, gbw, tech", [("OPAMP_IDEAL", 1e9, "ideal"),
                                             ("OPAMP_GP1", 1e6, "smd_pcb"),
                                             ("OPAMP_FET3", 3e6, "smd_pcb"),
                                             ("OPAMP_RR10", 1e7, "smd_pcb")])
def test_each_model_runs_and_its_gbw_is_what_it_says(name, gbw, tech):
    """A unity follower's -3 dB point is the gain-bandwidth product. Real
    grades only simulate as themselves on a real technology."""
    sup = (2.5, -2.5) if name == "OPAMP_RR10" else (12, -12)
    wide = {"type": "ac", "sweep": "dec", "points": 100, "fstart": "1", "fstop": "1g",
            "output_node": "out"}
    df = simulate_get_full_sweep({"title": "f", "components": [V1, opamp(name, "in", "out")],
                                  "analysis": wide}, technology=tech,
                                 supply_pos=sup[0], supply_neg=sup[1])
    assert metrics.lowpass_cutoff(df) == pytest.approx(gbw, rel=0.02)
    assert metrics.gain_at(df, 1e3) == pytest.approx(0.0, abs=0.01)


def test_slew_rate_is_built_into_the_subckt():
    """Slew = Imax / C1 by construction (measured in stage 2's transient)."""
    import re
    m = get_opamp("OPAMP_FET3")
    text = m.subckt()
    imax = float(re.search(r"I = ([0-9.e+-]+)\*tanh", text).group(1))
    c1 = float(re.search(r"^C1 n1 0 ([0-9.e+-]+)$", text, re.M).group(1))
    assert imax / c1 / 1e6 == pytest.approx(13.0, rel=1e-3)


def test_library_metadata_is_complete():
    for m in OPAMPS.values():
        assert m.source and m.supply_max_v > m.supply_min_v >= 0
        assert m.name in m.summary()
    with pytest.raises(ValueError, match="Unknown op-amp part"):
        get_opamp("LM358")


# ---------------------------------------------------------------------
# Pins, rails, validation
# ---------------------------------------------------------------------

def test_opamp_pins_map_to_the_subckt_order():
    u = Component(**opamp("OPAMP_GP1", "n2", "out"))
    assert u.to_spice_line() == "XU1 n2 out vcc vee out OPAMP_GP1"
    assert u.nodes == ["n2", "out", "vcc", "vee", "out"]


@pytest.mark.parametrize("bad, match", [
    ({"id": "U1", "type": "opamp", "part": "OPAMP_GP1", "pins": {"in+": "a"}}, "missing"),
    ({"id": "U1", "type": "opamp", "pins": {}}, "needs a 'part'"),
    ({"id": "R1", "type": "opamp", "part": "OPAMP_GP1", "pins": {}}, "should start with"),
    ({"id": "R1", "type": "resistor", "nodes": ["a", "b"], "value": "1k",
      "part": "OPAMP_GP1"}, "not 'pins'/'part'"),
])
def test_bad_parts_are_rejected(bad, match):
    with pytest.raises(ValueError, match=match):
        Component(**bad)


def test_opamp_without_supplies_is_feedback_not_a_crash():
    from spice_agent.agent.loop import call_simulate_circuit_tool

    r = call_simulate_circuit_tool({"title": "t", "components": [V1] + SALLEN_KEY},
                                   DesignSpec("lowpass", 1e4))
    assert r["simulation_failed"] is True
    assert "need supply rails" in r["message"] and "passive parts only" in r["message"]


def test_the_model_cannot_drive_the_rails():
    from spice_agent.agent.loop import call_simulate_circuit_tool

    rail = c("V2", "voltage_source", ["vcc", "0"], "DC 12")
    r = call_simulate_circuit_tool({"title": "t", "components": [V1, rail] + SALLEN_KEY},
                                   DesignSpec("lowpass", 1e4, **SUP))
    assert r["simulation_failed"] is True and "provided by the harness" in r["message"]


def test_supply_spec_validation():
    with pytest.raises(ValueError, match="together"):
        DesignSpec("lowpass", 1e4, supply_pos_v=12)
    with pytest.raises(ValueError, match="supply rails must satisfy"):
        DesignSpec("lowpass", 1e4, supply_pos_v=5, supply_neg_v=6)
    single = DesignSpec("lowpass", 1e4, supply_pos_v=5, supply_neg_v=0)
    assert single.powered and "supply rails 5 V / 0 V" in single.describe()


def test_netlist_has_rails_subckt_and_operating_point():
    circuit = Circuit.from_dict({"title": "t", "components": [V1] + SALLEN_KEY})
    netlist = circuit.to_netlist("o.txt", supply_pos=12, supply_neg=-12)
    assert "V_SUPPLY_POS vcc 0 DC 12" in netlist and "V_SUPPLY_NEG vee 0 DC -12" in netlist
    assert "op\nwrite o_op.raw all" in netlist
    # the grade is the design choice; the technology decides the physics
    assert "XU1 n2 out vcc vee out OPAMP_IDEAL" in netlist
    assert "OPAMP_GP1" not in netlist
    real = circuit.to_netlist("o.txt", technology="smd_pcb", supply_pos=12, supply_neg=-12)
    assert ".subckt OPAMP_GP1 inp inn vcc vee out" in real and "OPAMP_IDEAL" not in real
    passive = Circuit.from_dict({"title": "t", "components": [
        V1, c("R1", "resistor", ["in", "out"], "1k"), c("C1", "capacitor", ["out", "0"], "1n")]})
    assert "\nop\n" not in passive.to_netlist("o.txt")      # passive runs unchanged


def test_breadboard_gives_every_pin_a_lead():
    netlist = Circuit.from_dict({"title": "t", "components": [V1] + SALLEN_KEY}).to_netlist(
        "o.txt", technology="breadboard", supply_pos=12, supply_neg=-12)
    assert "XU1 U1_p0c U1_p1c U1_p2c U1_p3c U1_p4c OPAMP_GP1" in netlist
    assert "L_U1_p2 vcc U1_p2l" in netlist                    # the supply pin too


# ---------------------------------------------------------------------
# Operating point and bias
# ---------------------------------------------------------------------

def test_sallen_key_passes_with_a_healthy_bias_point():
    ideal = run(SALLEN_KEY, DesignSpec("lowpass", 1e4, **SUP))
    assert ideal["passed"] is True
    assert ideal["cutoff_hz"] == pytest.approx(9992, rel=0.005)
    assert ideal["bias"]["supply_current_a"] == pytest.approx(0.0, abs=1e-9)   # ideal op-amp
    r = run(SALLEN_KEY, DesignSpec("lowpass", 1e4, technology="smd_pcb", **SUP))
    assert r["passed"] is True
    assert r["cutoff_hz"] == pytest.approx(9983, rel=0.005)
    (u1,) = r["bias"]["parts"]
    assert u1["v_out"] == pytest.approx(0.0, abs=0.01) and not u1["saturated"]
    assert r["bias"]["supply_current_a"] == pytest.approx(0.5e-3, rel=0.01)    # the GP1 grade


CAP_COUPLED = [c("C1", "capacitor", ["in", "n1"], "100n"), opamp("OPAMP_GP1", "n1", "out")]


def test_capacitor_coupled_input_saturates_and_says_why():
    r = run(CAP_COUPLED, DesignSpec("highpass", 100, technology="smd_pcb", **SUP))
    assert r["passed"] is False
    (u1,) = r["bias"]["parts"]
    assert u1["saturated"] is True and u1["v_out"] == pytest.approx(-10.53, abs=0.05)
    assert r["message"].startswith("BIAS PROBLEM")
    assert "SATURATED" in r["message"]
    assert "in+ (node 'n1') has no DC path" in r["message"]


def test_missing_dc_path_fails_even_with_ideal_parts():
    """An ideal op-amp draws no bias current, so nothing saturates - but the
    node's DC level is undefined, and a built circuit would rail."""
    r = run(CAP_COUPLED, DesignSpec("highpass", 100, **SUP))
    assert r["passed"] is False
    (u1,) = r["bias"]["parts"]
    assert not u1["saturated"]
    assert "in+ (node 'n1') has no DC path" in r["message"]


def test_part_on_the_wrong_supply_is_caught():
    r = run([opamp("OPAMP_RR10", "in", "out")],
            DesignSpec("lowpass", 1e4, technology="smd_pcb", **SUP))
    assert r["passed"] is False
    assert "rated for 2.7-5.5 V total supply, the rails give 24 V" in r["message"]
    # The rating is a catalogue property of the chosen grade, so it binds on
    # ideal technology too: an ideal simulation of a part the rails would
    # destroy is still not a design.
    ideal = run([opamp("OPAMP_RR10", "in", "out")], DesignSpec("lowpass", 1e4, **SUP))
    assert ideal["bias"]["ok"] is False
    assert "rated for 2.7-5.5 V total supply" in ideal["bias"]["message"]


def test_supply_pins_must_go_to_the_rails():
    r = run([opamp("OPAMP_GP1", "in", "out", vee="0")], DesignSpec("lowpass", 1e4, **SUP))
    assert "not to the rails" in r["bias"]["message"]


def test_operating_point_is_logged(tmp_path):
    from spice_agent.agent.loop import call_simulate_circuit_tool

    capture: dict = {}
    call_simulate_circuit_tool({"title": "t", "components": [V1] + SALLEN_KEY},
                               DesignSpec("lowpass", 1e4, **SUP), capture=capture)
    assert capture["op"]["v(vcc)"] == pytest.approx(12.0)


def test_raw_reader(tmp_path):
    from spice_agent.simulation.raw import node_voltage, read_operating_point

    raw = tmp_path / "op.raw"
    raw.write_text("Title: t\nPlotname: Operating Point\nVariables:\n"
                   "\t0\tv(out)\tvoltage\n\t1\ti(v1)\tcurrent\n"
                   "Values:\n 0\t1.5e+00\n\t-2.0e-03\n")
    op = read_operating_point(raw)
    assert op == {"v(out)": 1.5, "i(v1)": -2e-3}
    assert node_voltage(op, "out") == 1.5 and node_voltage(op, "0") == 0.0


# ---------------------------------------------------------------------
# Gain target, active band-pass, op-amp limits
# ---------------------------------------------------------------------

def test_gain_target_is_checked_only_when_set():
    assert "passband_gain_error_db" not in run(SALLEN_KEY, DesignSpec("lowpass", 1e4, **SUP))
    ok = run(SALLEN_KEY, DesignSpec("lowpass", 1e4, passband_gain_db=0, **SUP))
    assert ok["passed"] is True
    off = run(SALLEN_KEY, DesignSpec("lowpass", 1e4, passband_gain_db=6, **SUP))
    assert off["passed"] is False
    assert off["passband_gain_error_db"] == pytest.approx(-6.0, abs=0.05)
    assert "1 + Rf/Rg" in off["message"] and "R3/(2*R1)" in off["message"]
    assert "not by the frequency-setting parts" not in off["message"]
    assert "see the diagnosis" not in off["message"]                 # ideal technology


MFB_SMD = DesignSpec("bandpass", 1e4, bandwidth_hz=2e3, passband_gain_db=0,
                     technology="smd_pcb", **SUP)


def test_ideal_technology_makes_every_grade_ideal():
    for part in ("OPAMP_GP1", "OPAMP_FET3"):
        r = run(mfb(part), MFB_SPEC)
        assert r["passed"] is True
        assert r["center_hz"] == pytest.approx(10000, rel=0.005)
        assert r["bandwidth_hz"] == pytest.approx(1997, rel=0.01)


def test_mfb_bandpass_passes_with_a_fast_enough_grade():
    r = run(mfb("OPAMP_FET3"), MFB_SMD)
    assert r["passed"] is True
    assert r["center_hz"] == pytest.approx(9772, rel=0.01)
    assert r["bandwidth_hz"] == pytest.approx(1937, rel=0.02)
    assert r["peak_gain_db"] == pytest.approx(0.0, abs=0.1)


def test_slow_grade_is_named_as_the_limit():
    r = run(mfb("OPAMP_GP1"), MFB_SMD)
    assert r["passed"] is False
    assert r["bandwidth_error_pct"] == pytest.approx(-10.6, abs=0.5)
    d = r["parasitics"]
    assert d["check"] == "bandwidth" and d["ideal_parts_passed"] is True
    top = d["per_component"][0]
    assert top["label"] == "U1 (OPAMP_GP1)" and top["passes_if_ideal"] is True
    assert "ideal op-amps included" in r["message"]
    assert "U1 (OPAMP_GP1) alone would make the design PASS" in r["message"]
    assert "faster grades rated for 12 V / -12 V: OPAMP_FET3 (3 MHz)" in r["message"]


def test_mfb_on_a_breadboard():
    r = run(mfb("OPAMP_FET3"), DesignSpec("bandpass", 1e4, bandwidth_hz=2e3,
                                          passband_gain_db=0, technology="breadboard", **SUP))
    assert r["passed"] is True
    assert r["bandwidth_hz"] == pytest.approx(1941, rel=0.02)


# ---------------------------------------------------------------------
# Agent-facing: schema, prompt, repeats, CLI, export
# ---------------------------------------------------------------------

def test_schema_offers_the_library_parts():
    from spice_agent.agent.tool_schema import CIRCUIT_JSON_SCHEMA

    from spice_agent.models import SELECTABLE_OPAMPS

    item = CIRCUIT_JSON_SCHEMA["properties"]["components"]["items"]
    assert "opamp" in item["properties"]["type"]["enum"]
    assert item["properties"]["part"]["enum"] == sorted(SELECTABLE_OPAMPS)
    assert "OPAMP_IDEAL" not in item["properties"]["part"]["enum"]
    assert item["properties"]["pins"]["required"] == ["in+", "in-", "vcc", "vee", "out"]


def test_prompt_lists_parts_only_when_powered():
    from spice_agent.agent import build_system_prompt

    passive = build_system_prompt(DesignSpec("lowpass", 1e4))
    assert "resistors, capacitors and inductors only (no supply rails" in passive
    assert "OPAMP_GP1" not in passive and "Active option" not in passive
    active = build_system_prompt(DesignSpec("lowpass", 1e4, **SUP))
    assert "node 'vcc' = 12 V" in active and "OPAMP_GP1" in active
    assert "OPAMP_IDEAL" not in active
    assert "every op-amp is simulated as an ideal op-amp" in active
    real = build_system_prompt(DesignSpec("lowpass", 1e4, technology="breadboard", **SUP))
    assert "simulated as the grade you choose, built as breadboard" in real
    assert "Sallen-Key" in active
    assert "every op-amp input needs a DC path" in active


def test_design_key_keeps_opamp_pin_order():
    from spice_agent.agent.loop import design_key

    a = {"components": [opamp("OPAMP_GP1", "n2", "out")]}
    b = {"components": [opamp("OPAMP_GP1", "out", "n2")]}        # inputs swapped
    assert design_key(a) != design_key(b)
    assert design_key(a) == design_key({"components": [opamp("OPAMP_GP1", "n2", "out", i="X7")]})


def test_cli_supply_and_gain_flags(monkeypatch):
    import spice_agent.agent.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "run_agent_loop", lambda spec, **kw: seen.setdefault("s", spec)
                        and cli.AgentRunResult(spec=spec, passed=False, iterations=0))
    cli.main(["--no-log", "--target", "1e4", "--supply", "12", "--gain", "6"])
    s = seen.pop("s")
    assert (s.supply_pos_v, s.supply_neg_v, s.passband_gain_db) == (12, -12, 6)
    cli.main(["--no-log", "--target", "1e4", "--supply-pos", "5", "--supply-neg", "0"])
    assert (seen["s"].supply_pos_v, seen["s"].supply_neg_v) == (5, 0)
    with pytest.raises(SystemExit):
        cli.main(["--no-log", "--supply", "12", "--supply-pos", "5", "--supply-neg", "0"])


def test_export_carries_model_and_rails_and_skips_the_schematic(tmp_path):
    from spice_agent.export import export

    cir, asc = tmp_path / "d.cir", tmp_path / "d.asc"
    info = export({"title": "t", "components": [V1] + SALLEN_KEY,
                   "analysis": analysis_for(DesignSpec("lowpass", 1e4, **SUP))},
                  asc_path=asc, cir_path=cir, technology="breadboard",
                  supply_pos=12, supply_neg=-12)
    text = cir.read_text()
    assert ".subckt OPAMP_GP1" in text and "V_SUPPLY_POS vcc 0 DC 12" in text
    assert "XU1 " in text
    assert not asc.exists() and "op-amps" in info["asc_skipped"]



def test_the_model_cannot_choose_the_ideal_opamp():
    """Offered next to real grades, a model picks perfection (all four live
    runs of 2026-11-23 did); how ideal an op-amp is follows the technology."""
    from spice_agent.agent.loop import call_simulate_circuit_tool

    r = call_simulate_circuit_tool(
        {"title": "t", "components": [V1, opamp("OPAMP_IDEAL", "in", "out")]},
        DesignSpec("lowpass", 1e4, **SUP))
    assert r["simulation_failed"] is True
    assert "not grades you can choose" in r["message"] and "OPAMP_GP1" in r["message"]



# Iteration 2 of the live 50 kHz MFB run of 2026-11-23 (smd_pcb, FET3 grade):
# 530k feedback resistor. Measured: peak gain -3.56 dB vs 0 dB target, centre
# 47.9 kHz, bandwidth 5200 Hz. Ideal parts pass the gain but miss the centre
# frequency; the gain loss is the SMD board's 0.3 pF per node loading the
# 530k node (removing stray node capacitance wins back 3.0 dB).
MFB_50K = [c("R1", "resistor", ["in", "n1"], "265k"),
           c("R2", "resistor", ["n1", "0"], "1k"),
           c("C1", "capacitor", ["n1", "n2"], "120p"),
           c("C2", "capacitor", ["n1", "out"], "120p"),
           c("R3", "resistor", ["n2", "out"], "530k"),
           opamp("OPAMP_FET3", "0", "n2")]


def test_gain_loss_from_stray_capacitance_is_explained():
    r = run(MFB_50K, DesignSpec("bandpass", 5e4, bandwidth_hz=5e3, passband_gain_db=0,
                                technology="smd_pcb", **SUP))
    assert r["passed"] is False
    assert r["passband_gain_error_db"] == pytest.approx(-3.56, abs=0.1)
    d = r["parasitics"]
    assert d["parasitic_failures"] == ["passband gain"] and d["design_failures"] == []
    assert d["ideal_only_failures"] == ["frequency"]
    m = r["message"]
    assert "on a real board parasitics can also erode it - see the diagnosis" in m
    assert ("The passband gain failure is caused by the construction parasitics: "
            "ideal parts pass it (they would miss frequency instead") in m
    assert "Ideal parts would still fail a check" not in m
    assert d["limiting_parasitics"][0]["parasitic"] == "stray node capacitance"
    assert "Lower the impedance level" in m and "hundreds of kilohms" in m
