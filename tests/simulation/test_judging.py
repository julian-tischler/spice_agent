"""
What is judged, and how: the judged window, passband peaking, the
harness's source/load terminations, and the per-component diagnosis.

Fixtures are the circuits of the live breadboard runs of 2026-11-19 (and
textbook designs), measured first (2026-11-19):

    notch 50 kHz, R 1k + series L 100u / C 100n     49.5 dB deep, at 50.1 kHz;
        rejected live only because 100 MHz sat at -18.8 dB vs -0.4 dB at 1 Hz
    notch 50 kHz, R 10 + L 150u / C 68n             11 dB deep, ideal too
    bandpass 100 kHz/20 kHz, L 100u, C 25n, R 12.6   bandwidth +26.5% on
        breadboard; L1 made ideal alone recovers 25.1 % and passes
    highpass 10 kHz C-L-C (160n/0.8m/160n) + 100R    cutoff 9762 Hz against
        the passband in the window (10366 Hz when referenced to 100 MHz)
    L-C lowpass 1.125m / 225n                        no load: +63 dB peak;
        50 ohm load: Butterworth, 9992 Hz, 0.0 dB peaking
    C-L-C 318.3n / 1.5915m / 318.3n (g = 1, 2, 1, R0 = 50 ohm)
        50/50 ohm terminated: 9992 Hz, flat, passband -6.02 dB;
        unterminated: +52.7 dB peak
"""

import pytest

from spice_agent.agent.analysis import analysis_for
from spice_agent.circuit import Circuit
from spice_agent.simulation import DesignSpec, simulate_and_verify

V1 = {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"}


def c(i, t, n, v):
    return {"id": i, "type": t, "nodes": n, "value": v}


def run(parts, spec, **kw):
    circuit = {"title": "t", "components": [V1] + parts, "analysis": analysis_for(spec)}
    return simulate_and_verify(circuit, spec, **kw)


NOTCH_GOOD = [c("R1", "resistor", ["in", "out"], "1k"),
              c("L1", "inductor", ["out", "n1"], "100u"),
              c("C1", "capacitor", ["n1", "0"], "100n")]
NOTCH_SHALLOW = [c("R1", "resistor", ["in", "out"], "10"),
                 c("L1", "inductor", ["out", "n1"], "150u"),
                 c("C1", "capacitor", ["n1", "0"], "68n")]
BANDPASS_WIDE = [c("L1", "inductor", ["in", "n1"], "100u"),
                 c("C1", "capacitor", ["n1", "out"], "25n"),
                 c("R1", "resistor", ["out", "0"], "12.6")]
HIGHPASS = [c("C1", "capacitor", ["in", "n1"], "160n"),
            c("L1", "inductor", ["n1", "0"], "0.8m"),
            c("C2", "capacitor", ["n1", "out"], "160n"),
            c("R1", "resistor", ["out", "0"], "100")]
LC = [c("L1", "inductor", ["in", "out"], "1.125m"),
      c("C1", "capacitor", ["out", "0"], "225n")]
CLC_R0_50 = [c("C1", "capacitor", ["in", "0"], "318.3n"),
             c("L1", "inductor", ["in", "out"], "1.5915m"),
             c("C2", "capacitor", ["out", "0"], "318.3n")]

NOTCH_BB = DesignSpec("notch", 5e4, min_notch_depth_db=30, technology="breadboard")
BANDPASS_BB = DesignSpec("bandpass", 1e5, bandwidth_hz=2e4, technology="breadboard")


# ---------------------------------------------------------------------
# The judged window
# ---------------------------------------------------------------------

def test_default_window_is_two_decades_and_the_sweep_covers_it():
    s = DesignSpec("lowpass", 5e4)
    assert s.judge_window() == (500.0, 5e6)
    assert analysis_for(s)["fstop"] == "5e+06"          # beyond the 1 MHz default
    assert DesignSpec("lowpass", 50).judge_window() == (0.5, 5000.0)
    assert analysis_for(DesignSpec("lowpass", 50))["fstart"] == "0.5"


def test_explicit_window_extends_the_sweep():
    s = DesignSpec("notch", 5e4, judge_hi_hz=1e9)
    assert s.judge_window() == (500.0, 1e9)
    assert analysis_for(s)["fstop"] == "1e+09"


@pytest.mark.parametrize("kw, match", [
    ({"judge_lo_hz": 6e4}, "must lie below the target"),
    ({"judge_hi_hz": 4e4}, "must lie above the target"),
    ({"judge_hi_hz": -1}, "must be positive"),
])
def test_window_validation(kw, match):
    with pytest.raises(ValueError, match=match):
        DesignSpec("notch", 5e4, **kw)


def test_notch_rejected_live_now_passes_and_reports_the_far_end():
    """The live notch's 100 MHz droop is physics (L1 acts as 15 pF up there),
    not something the spec asked about: reported, not failed."""
    r = run(NOTCH_GOOD, NOTCH_BB)
    assert r["passed"] is True
    assert r["notch_depth_db"] == pytest.approx(49.5, abs=1.0)
    (note,) = r["outside_window"]
    assert note["side"] == "above" and note["kind"] == "sags"
    assert note["at_hz"] == pytest.approx(1e8)
    assert "Not judged" in r["message"] and "cannot fail the design" in r["message"]


def test_asking_for_100_mhz_brings_the_old_strictness_back():
    r = run(NOTCH_GOOD, DesignSpec("notch", 5e4, min_notch_depth_db=30,
                                   technology="breadboard", judge_hi_hz=1e8))
    assert r["passed"] is False
    assert "gain differs between the low edge" in r["message"]


def test_failed_shape_still_reports_every_measurement():
    """No early return: a failing check must not hide how close the rest are."""
    r = run(NOTCH_GOOD, DesignSpec("notch", 5e4, min_notch_depth_db=30,
                                   technology="breadboard", judge_hi_hz=1e8))
    assert r["notch_hz"] == pytest.approx(50119, rel=0.01)
    assert r["notch_depth_db"] == pytest.approx(49.5, abs=1.0)
    assert "notch at 50118.7 Hz" in r["message"]


def test_highpass_passband_reference_is_inside_the_window():
    """Referenced to the 100 MHz sweep end (+1.5 dB there) this cutoff read
    10366 Hz; against the passband in the window it is 9762 Hz."""
    r = run(HIGHPASS, DesignSpec("highpass", 1e4, stopband_hz=1e3, stopband_atten_db=40,
                                 technology="breadboard"))
    assert r["passed"] is True
    assert r["cutoff_hz"] == pytest.approx(9762, rel=0.01)
    assert r["passband_gain_db"] == pytest.approx(0.0, abs=0.1)


# ---------------------------------------------------------------------
# Passband peaking
# ---------------------------------------------------------------------

def test_undamped_lc_fails_on_peaking():
    r = run(LC, DesignSpec("lowpass", 1e4))
    assert r["passed"] is False
    assert r["passband_peaking_db"] > 50
    assert "nothing damps it" in r["message"]
    assert r["peaking_excess_db"] == pytest.approx(r["passband_peaking_db"] - 3.0)


# A 141 ohm load gives this L-C a Q of 2: a well-defined +6.3 dB peak with
# its -3 dB point at 14.84 kHz (measured). The unloaded LC's peak is only
# limited by where the sweep samples it (+35..+71 dB), so it is no good for
# testing a limit.
Q2 = dict(load_ohm=141)


def test_peaking_limit_can_be_switched_off():
    r = run(LC, DesignSpec("lowpass", 1.5e4, max_peaking_db=None, **Q2))
    assert r["passed"] is True
    assert "passband peaks" not in r["message"]
    assert r["passband_peaking_db"] == pytest.approx(6.3, abs=0.2)     # still measured


def test_peaking_limit_is_adjustable():
    """Default 3 dB; a user who accepts the resonance raises the limit."""
    assert DesignSpec("lowpass", 1e4).max_peaking_db == 3.0
    assert run(LC, DesignSpec("lowpass", 1.5e4, **Q2))["passed"] is False
    assert run(LC, DesignSpec("lowpass", 1.5e4, max_peaking_db=10, **Q2))["passed"] is True
    with pytest.raises(ValueError, match="max_peaking_db"):
        DesignSpec("lowpass", 1e4, max_peaking_db=-1)


# ---------------------------------------------------------------------
# Terminations (fixed by the harness, never by the model)
# ---------------------------------------------------------------------

def test_defaults_add_nothing():
    s = DesignSpec("lowpass", 1e4)
    assert (s.source_ohm, s.load_ohm, s.terminated) == (0.0, None, False)
    netlist = Circuit.from_dict({"title": "t", "components": [V1] + LC}).to_netlist("o.txt")
    assert "R_SOURCE" not in netlist and "R_LOAD" not in netlist


def test_load_damps_the_lc_into_a_butterworth():
    r = run(LC, DesignSpec("lowpass", 1e4, load_ohm=50))
    assert r["passed"] is True
    assert r["cutoff_hz"] == pytest.approx(9992, rel=0.01)
    assert r["passband_peaking_db"] == pytest.approx(0.0, abs=0.1)


def test_doubly_terminated_ladder_from_the_hint_tables():
    """g = 1, 2, 1 with R0 = 50 ohm: flat, -6.02 dB passband (the 2:1
    source/load divider), judged relative to that level."""
    r = run(CLC_R0_50, DesignSpec("lowpass", 1e4, source_ohm=50, load_ohm=50))
    assert r["passed"] is True
    assert r["passband_gain_db"] == pytest.approx(-6.02, abs=0.05)
    assert r["passband_peaking_db"] == pytest.approx(0.0, abs=0.1)
    unterminated = run(CLC_R0_50, DesignSpec("lowpass", 1e4))
    assert unterminated["passed"] is False and unterminated["passband_peaking_db"] > 40


def test_terminations_are_in_the_netlist_and_stay_ideal():
    circuit = Circuit.from_dict({"title": "t", "components": [V1] + LC})
    netlist = circuit.to_netlist("o.txt", technology="breadboard",
                                 source_ohm=50, load_ohm=1000)
    assert "R_SOURCE src_ext in 50" in netlist
    assert "R_LOAD out 0 1000" in netlist
    assert "V1 src_ext 0 AC 1" in netlist
    assert "L_R_SOURCE" not in netlist and "L_R_LOAD" not in netlist   # no leads
    assert not [ln for ln in netlist.splitlines()
                if ln.startswith("C_stray") and "src_ext" in ln]       # off the board


@pytest.mark.parametrize("bad", [
    c("R_LOAD", "resistor", ["out", "0"], "50"),
    c("R_SOURCE", "resistor", ["in", "out"], "50"),
    c("R9", "resistor", ["src_ext", "in"], "50"),
])
def test_model_cannot_use_the_reserved_names(bad):
    from spice_agent.agent.loop import call_simulate_circuit_tool

    r = call_simulate_circuit_tool({"title": "t", "components": [V1] + LC + [bad]},
                                   DesignSpec("lowpass", 1e4))
    assert r["simulation_failed"] is True and "reserved" in r["message"]


def test_export_includes_the_terminations(tmp_path):
    from spice_agent.export import export

    cir = tmp_path / "d.cir"
    export({"title": "t", "components": [V1] + LC,
            "analysis": analysis_for(DesignSpec("lowpass", 1e4))},
           cir_path=cir, source_ohm=50, load_ohm=1000)
    text = cir.read_text()
    assert "R_SOURCE" in text and "R_LOAD" in text


def test_prompt_states_the_terminations():
    from spice_agent.agent import build_system_prompt

    unterminated = build_system_prompt(DesignSpec("lowpass", 1e4))
    assert "ideal voltage source (0 ohm)" in unterminated
    assert "Unity passband gain" in unterminated
    terminated = build_system_prompt(DesignSpec("lowpass", 1e4, source_ohm=50, load_ohm=50))
    assert "do NOT add them yourself" in terminated
    assert "50 ohm source resistance" in terminated
    assert "Unity passband gain" not in terminated


@pytest.mark.parametrize("kw", [{"source_ohm": -1}, {"load_ohm": 0}])
def test_termination_validation(kw):
    with pytest.raises(ValueError):
        DesignSpec("lowpass", 1e4, **kw)


# ---------------------------------------------------------------------
# Diagnosis: design vs construction, per component, levels
# ---------------------------------------------------------------------

def test_per_component_names_the_part_to_change():
    r = run(BANDPASS_WIDE, BANDPASS_BB)
    d = r["parasitics"]
    assert d["check"] == "bandwidth" and d["level"] == "full"
    top = d["per_component"][0]
    assert top["label"] == "L1 (100u)" and top["passes_if_ideal"] is True
    assert top["recovered"] == pytest.approx(25.1, abs=2.0)
    assert "L1 (100u) alone would make the design PASS" in r["message"]


def test_basic_level_skips_the_per_component_check():
    d = run(BANDPASS_WIDE, BANDPASS_BB, diagnose="basic")["parasitics"]
    assert d["level"] == "basic" and "per_component" not in d
    assert d["check"] == "bandwidth"


def test_off_level_skips_the_diagnosis():
    r = run(BANDPASS_WIDE, BANDPASS_BB, diagnose="off")
    assert "parasitics" not in r and "Diagnosis:" not in r["message"]


def test_design_failures_are_not_blamed_on_parasitics():
    d = run(NOTCH_SHALLOW, NOTCH_BB)["parasitics"]
    assert d["design_failures"] == ["notch depth"]
    assert d["parasitic_failures"] == []
    assert "limiting_parasitics" not in d and "per_component" not in d


def test_parallel_identical_parts_are_one_group():
    from spice_agent.simulation.diagnosis import component_groups

    caps = [c(f"C{i}", "capacitor", ["out", "0"], "30n") for i in range(1, 21)]
    groups = component_groups(Circuit.from_dict(
        {"title": "t", "components": [V1, c("L1", "inductor", ["in", "out"], "1m")] + caps}))
    assert len(groups) == 2
    assert groups[1]["label"] == "C1..C20 (20x 30n, out-0)"
