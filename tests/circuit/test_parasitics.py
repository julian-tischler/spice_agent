"""
Tests for the parasitic model (spice_agent.circuit.parasitics) and its expansion
into netlists (circuit.expansion.expand_with_parasitics).

Three things are being pinned:

  1. the value parser and the profile arithmetic;
  2. that expansion is OPT-IN -- the ideal path must be byte-identical to
     what it was before parasitics existed, because every other test in
     the suite relies on exact ideal behaviour;
  3. that the parasitics actually bite, in the way they bite in reality:
     attenuation stops improving above an inductor's self-resonance, and
     the worst point in a stopband moves off the band edge.

Measured reference (the agent's own 3rd-order 10 kHz design, swept to
100 MHz) -- worst attenuation between 100 kHz and 100 MHz:

    ideal       60.0 dB  at 100 kHz      (monotonic, worst case at edge)
    smd_pcb     48.7 dB  at  47.9 MHz
    breadboard  20.1 dB  at  22.4 MHz

That the ideal model promises 240 dB at 100 MHz where the breadboard
build delivers 24 dB is the entire point of the feature.
"""

import math

import pytest

from spice_agent.simulation import metrics
from spice_agent.circuit import Circuit, expand_with_parasitics
from spice_agent.circuit.parasitics import (
    BREADBOARD,
    IDEAL,
    PROFILES,
    SMD_PCB,
    get_profile,
    parse_spice_value,
)
from spice_agent.simulation import DesignSpec, check_filter_spec, simulate_get_full_sweep

WIDE = {"type": "ac", "sweep": "dec", "points": 100, "fstart": "1",
        "fstop": "100meg", "output_node": "out"}
NARROW = {"type": "ac", "sweep": "dec", "points": 100, "fstart": "1",
          "fstop": "1meg", "output_node": "out"}

# The design the agent produced for "10 kHz lowpass, 40 dB down at 100 kHz"
LADDER_3RD = {"title": "LC lowpass 3rd order", "components": [
    {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
    {"id": "L1", "type": "inductor", "nodes": ["in", "n1"], "value": "23.87m"},
    {"id": "C1", "type": "capacitor", "nodes": ["n1", "0"], "value": "21.22n"},
    {"id": "L2", "type": "inductor", "nodes": ["n1", "out"], "value": "7.958m"},
    {"id": "R1", "type": "resistor", "nodes": ["out", "0"], "value": "1k"}]}

RC = {"title": "RC lowpass", "components": [
    {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
    {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "1.59k"},
    {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "10n"}]}


# ---------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("23.87m", 0.02387),
    ("100n", 1e-7),
    ("318p", 318e-12),
    ("1k", 1e3),
    ("470", 470.0),
    ("1e-3", 1e-3),
    ("10uF", 10e-6),      # trailing unit letter tolerated
    ("1kOhm", 1e3),
    ("1Meg", 1e6),
    ("100meg", 1e8),
    ("1M", 1e-3),         # SPICE's classic trap: M is milli, not mega
    ("1MEG", 1e6),
    ("2.5G", 2.5e9),
    ("-5u", -5e-6),
    (0.001, 0.001),       # already numeric
])
def test_parse_spice_value(text, expected):
    assert parse_spice_value(text) == pytest.approx(expected)


def test_parse_rejects_nonsense():
    for bad in ("", "abc", "1..2", "k100"):
        with pytest.raises(ValueError):
            parse_spice_value(bad)


# ---------------------------------------------------------------------
# Profile arithmetic
# ---------------------------------------------------------------------

def test_profile_lookup():
    assert get_profile(None) is IDEAL
    assert get_profile("ideal") is IDEAL
    assert get_profile("breadboard") is BREADBOARD
    assert get_profile(SMD_PCB) is SMD_PCB
    with pytest.raises(ValueError, match="Unknown technology profile"):
        get_profile("stripboard")


def test_ideal_profile_has_no_parasitics():
    assert IDEAL.is_ideal
    assert IDEAL.inductor_srf_hz(1e-3) is None
    assert IDEAL.inductor_dcr(1e-3) == 0.0
    assert IDEAL.capacitor_srf_hz(1e-9) is None


def test_srf_matches_the_textbook_formula():
    """SRF is the number to validate against a real datasheet, so it must
    be exactly 1/(2*pi*sqrt(L*Cp)) with no fudge factors."""
    l_h = 10e-3
    expected = 1 / (2 * math.pi * math.sqrt(l_h * BREADBOARD.inductor_winding_c))
    assert BREADBOARD.inductor_srf_hz(l_h) == pytest.approx(expected)


def test_srf_falls_as_inductance_rises():
    """A bigger inductor self-resonates lower - which is why a design
    wanting stopband performance should prefer small inductors."""
    srfs = [SMD_PCB.inductor_srf_hz(l) for l in (100e-6, 1e-3, 10e-3, 100e-3)]
    assert srfs == sorted(srfs, reverse=True)
    # a decade more inductance costs sqrt(10) in SRF
    assert srfs[0] / srfs[1] == pytest.approx(math.sqrt(10), rel=1e-6)


def test_smd_resonates_higher_than_breadboard():
    for l_h in (1e-3, 10e-3):
        assert SMD_PCB.inductor_srf_hz(l_h) > BREADBOARD.inductor_srf_hz(l_h)


def test_rp_is_q_times_tank_impedance():
    """R_p = Q * sqrt(L/Cp). That product IS the impedance the tank presents
    at resonance, so R_p is directly the height of the self-resonant peak -
    measured as 258 kohm set / 258 kohm peak, 46 k / 46 k, 1155 k / 1140 k.
    """
    for profile in (SMD_PCB, BREADBOARD):
        for l_h in (318.3e-6, 10e-3):
            expected = profile.inductor_q_at_srf * math.sqrt(
                l_h / profile.inductor_winding_c)
            assert profile.inductor_rp(l_h) == pytest.approx(expected)


def test_rp_scales_with_sqrt_of_inductance():
    """sqrt(L/Cp) is 4.6 kohm at 318 uH but 25.8 kohm at 10 mH, which is why
    a dimensionless Q is stored rather than a fixed resistance."""
    assert BREADBOARD.inductor_rp(10e-3) / BREADBOARD.inductor_rp(100e-6) == \
        pytest.approx(10.0, rel=1e-6)


def test_rp_is_zero_without_a_tank_to_damp():
    from dataclasses import replace

    assert IDEAL.inductor_rp(10e-3) == 0.0
    # no winding capacitance means no resonance, so nothing for R_p to do
    assert replace(BREADBOARD, inductor_winding_c=0.0).inductor_rp(10e-3) == 0.0
    assert replace(BREADBOARD, inductor_q_at_srf=0.0).inductor_rp(10e-3) == 0.0


def test_breadboard_inductors_are_lossier_than_smd():
    """Cheap leaded chokes have worse cores: Q = 10 against 20."""
    assert BREADBOARD.inductor_q_at_srf < SMD_PCB.inductor_q_at_srf


def test_rp_does_not_move_the_resonant_frequency():
    """R_p changes only how sharp the resonance is. If it shifted the SRF the
    datasheet cross-check would stop being valid."""
    from dataclasses import replace

    for q in (0.0, 1.0, 10.0, 80.0):
        variant = replace(BREADBOARD, inductor_q_at_srf=q)
        assert variant.inductor_srf_hz(10e-3) == \
            pytest.approx(BREADBOARD.inductor_srf_hz(10e-3))


def test_dcr_scales_with_sqrt_of_inductance():
    assert SMD_PCB.inductor_dcr(10e-3) / SMD_PCB.inductor_dcr(100e-6) == \
        pytest.approx(10.0, rel=1e-6)


def test_only_breadboard_has_material_io_coupling():
    """The structural difference between the profiles, not a scaled value:
    with no ground plane the signal gets a path around the filter."""
    assert BREADBOARD.io_coupling_c >= 1e-12
    assert SMD_PCB.io_coupling_c < 0.1e-12
    assert "floor on achievable stopband" in BREADBOARD.describe()
    assert "floor on achievable stopband" not in SMD_PCB.describe()


def test_every_profile_describes_itself():
    for name, p in PROFILES.items():
        assert p.describe().endswith(".")
        assert p.name == name


# ---------------------------------------------------------------------
# Expansion is opt-in
# ---------------------------------------------------------------------

def test_ideal_netlist_is_unchanged():
    """The regression guard for the whole suite: with no technology given,
    the netlist must be exactly the ideal component lines."""
    c = Circuit.from_dict(dict(LADDER_3RD, analysis=NARROW))
    plain = c.to_netlist("o.txt")
    assert plain == c.to_netlist("o.txt", technology=None)
    assert plain == c.to_netlist("o.txt", technology="ideal")
    # skip line 0, the title -- which here happens to begin with "LC"
    body = [l for l in plain.splitlines()[1:] if l and l[0] in "RLCVI"]
    assert body == ["V1 in 0 AC 1", "L1 in n1 23.87m", "C1 n1 0 21.22n",
                    "L2 n1 out 7.958m", "R1 out 0 1k"]
    assert "parasitics" not in plain


def test_default_spec_technology_is_ideal():
    assert DesignSpec("lowpass", 1000).technology == "ideal"


def test_spec_rejects_unknown_technology():
    with pytest.raises(ValueError, match="Unknown technology profile"):
        DesignSpec("lowpass", 1000, technology="perfboard")


# ---------------------------------------------------------------------
# What expansion emits
# ---------------------------------------------------------------------

@pytest.fixture
def expanded():
    c = Circuit.from_dict(dict(LADDER_3RD, analysis=WIDE))
    return c.to_netlist("o.txt", technology="breadboard")


def test_expansion_keeps_the_source_ideal(expanded):
    """A source's own parasitics are not part of the circuit being designed."""
    assert "V1 in 0 AC 1" in expanded.splitlines()


def test_inductor_is_the_four_element_model(expanded):
    """
        a --[R_dcr]-- L1_i --+--( L1 )--+-- b
                             +-- C_wnd -+
                             +-- R_par -+

    R_dcr in series OUTSIDE the parallel block; L, C_wnd and R_par all span
    the same node pair. Putting R_dcr inside instead measures identically up
    to 10 MHz, but this is the conventional drawing and floors |Z| at the
    wire resistance rather than letting it fall to zero.
    """
    spans = {}
    for line in expanded.splitlines():
        if line.startswith(("L1 ", "R_L1_dcr ", "C_L1_wnd ", "R_L1_par ")):
            ident, n1, n2, *_ = line.split()
            spans[ident] = (n1, n2)

    assert set(spans) == {"L1", "R_L1_dcr", "C_L1_wnd", "R_L1_par"}
    tank_in, tank_out = spans["L1"]
    assert spans["C_L1_wnd"] == (tank_in, tank_out), "Cp parallels the inductor"
    assert spans["R_L1_par"] == (tank_in, tank_out), "Rp parallels the inductor"
    # DCR feeds the tank's input node and comes from somewhere else entirely
    assert spans["R_L1_dcr"][1] == tank_in
    assert spans["R_L1_dcr"][0] != tank_out


def test_expansion_uses_the_profiles_rp_value(expanded):
    rp = BREADBOARD.inductor_rp(23.87e-3)
    line = next(l for l in expanded.splitlines() if l.startswith("R_L1_par "))
    assert float(line.split()[-1]) == pytest.approx(rp, rel=1e-5)


def test_expansion_adds_the_expected_elements(expanded):
    assert "C_L1_wnd" in expanded, "inductor winding capacitance"
    assert "R_L1_dcr" in expanded, "inductor DCR"
    assert "R_L1_par" in expanded, "inductor parallel loss resistance"
    assert "L_C1_esl" in expanded, "capacitor ESL"
    assert "R_C1_esr" in expanded, "capacitor ESR"
    assert "L_R1_ser" in expanded, "resistor lead inductance"
    assert "C_R1_shn" in expanded, "resistor shunt capacitance"
    assert "C_io_coupling in out" in expanded, "input-to-output coupling"
    assert "C_stray" in expanded, "node capacitance to ground"


def test_expansion_preserves_the_designed_values(expanded):
    """Parasitics are added around the chosen components, never instead of
    them: every value the designer picked must still appear."""
    for ident, value in (("L1", "23.87m"), ("C1", "21.22n"),
                         ("L2", "7.958m"), ("R1", "1k")):
        assert any(l.split()[0] == ident and l.split()[-1] == value
                   for l in expanded.splitlines()), f"{ident} {value} missing"


def test_expansion_generates_unique_element_names(expanded):
    names = [l.split()[0] for l in expanded.splitlines()
             if l and l[0] in "RLCVI" and not l.startswith("*")]
    assert len(names) == len(set(names)), f"duplicate element name in {names}"


def test_expansion_never_floats_ground():
    """Ground must stay the reference - no series element between a
    component's ground terminal and node 0."""
    c = Circuit.from_dict(dict(RC, analysis=WIDE))
    for tech in ("smd_pcb", "breadboard"):
        text = c.to_netlist("o.txt", technology=tech)
        assert " 0 " in text or text.rstrip().endswith(" 0"), "ground still referenced"
        # C1's ground side connects straight to 0 via its ESR, no lead L
        assert not any(l.startswith("L_C1_b") for l in text.splitlines())


def test_unknown_profile_rejected_at_netlist_time():
    c = Circuit.from_dict(dict(RC, analysis=WIDE))
    with pytest.raises(ValueError, match="Unknown technology profile"):
        c.to_netlist("o.txt", technology="nope")


# ---------------------------------------------------------------------
# The behaviour that matters
# ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def sweeps():
    return {tech: simulate_get_full_sweep(dict(LADDER_3RD, analysis=WIDE),
                                          technology=tech)
            for tech in ("ideal", "smd_pcb", "breadboard")}


def test_all_profiles_simulate(sweeps):
    for tech, df in sweeps.items():
        assert len(df) > 500, tech
        assert df["gain_db"].notna().all(), tech


def test_parasitics_barely_affect_the_passband(sweeps):
    """DCR costs a fraction of a dB and shifts the cutoff slightly. If
    parasitics wrecked the passband too, the stopband result below would
    be uninteresting."""
    for tech in ("smd_pcb", "breadboard"):
        pb = sweeps[tech]["gain_db"].iloc[0]
        assert -1.5 < pb < 0.0, f"{tech} passband {pb}"
        fc = metrics.lowpass_cutoff(sweeps[tech])
        assert fc == pytest.approx(10_000, rel=0.1)


def test_ideal_stopband_improves_forever(sweeps):
    """3rd order, so -60 dB/decade with no end to it. This is the
    prediction the ideal model makes and reality does not honour."""
    df = sweeps["ideal"]
    pb = df["gain_db"].iloc[0]
    assert pb - metrics.gain_at(df, 1e6) == pytest.approx(120, abs=2)
    assert pb - metrics.gain_at(df, 1e8) == pytest.approx(240, abs=2)


def test_parasitics_destroy_the_far_stopband(sweeps):
    """The headline: the same design, built, is ~200 dB worse than the
    ideal simulation claims at the top of the sweep."""
    attens = {}
    for tech, df in sweeps.items():
        pb = df["gain_db"].iloc[0]
        attens[tech] = pb - metrics.gain_at(df, 1e8)

    assert attens["ideal"] > 200
    assert attens["smd_pcb"] < 100
    assert attens["breadboard"] < 40
    assert attens["smd_pcb"] > attens["breadboard"], "SMD must beat breadboard"
    assert attens["ideal"] - attens["breadboard"] > 150, "the error being modelled"


def test_worst_stopband_point_moves_off_the_band_edge(sweeps):
    """Why the band check had to come first.

    Ideally the response falls monotonically, so the worst attenuation in
    any stopband is at its edge and a single-frequency check is enough.
    With parasitics the worst point is decades higher, where only a band
    check can see it.
    """
    band = (1e5, 1e8)
    pb_ideal = sweeps["ideal"]["gain_db"].iloc[0]
    _, worst_f_ideal = metrics.worst_attenuation(sweeps["ideal"], pb_ideal, *band)
    assert worst_f_ideal == pytest.approx(1e5, rel=0.02), "ideal: worst at the edge"

    for tech in ("smd_pcb", "breadboard"):
        pb = sweeps[tech]["gain_db"].iloc[0]
        worst, worst_f = metrics.worst_attenuation(sweeps[tech], pb, *band)
        assert worst_f > 1e6, f"{tech}: worst point should be deep in the band"


def test_a_spec_that_passes_ideal_fails_when_built(sweeps):
    """End to end through the spec checker: ideal says yes, breadboard
    says no, and the message blames the resonance rather than the order."""
    spec_kwargs = dict(stopband_hz=100_000, stopband_atten_db=55)
    ideal = check_filter_spec(sweeps["ideal"],
                              DesignSpec("lowpass", 10_000, **spec_kwargs))
    built = check_filter_spec(sweeps["breadboard"],
                              DesignSpec("lowpass", 10_000, technology="breadboard",
                                         **spec_kwargs))
    assert ideal["passed"] is True
    assert built["passed"] is False
    assert "NOT at the band edge" in built["message"]
    assert "resonating" in built["message"]


# ---------------------------------------------------------------------
# Agent wiring
# ---------------------------------------------------------------------

def test_sweep_extends_only_for_non_ideal_technology():
    from spice_agent.agent import analysis_for

    assert analysis_for(DesignSpec("lowpass", 1000))["fstop"] == "1meg"
    for tech in ("smd_pcb", "breadboard"):
        a = analysis_for(DesignSpec("lowpass", 1000, technology=tech))
        assert a["fstop"] == "100meg", "parasitics are invisible below ~1 MHz"


def test_prompt_mentions_construction_only_when_it_matters():
    from spice_agent.agent import build_system_prompt

    ideal = build_system_prompt(DesignSpec("lowpass", 1000))
    assert "CONSTRUCTION" not in ideal
    assert "sweeps 1 Hz to 1meg" in ideal

    built = build_system_prompt(DesignSpec("lowpass", 1000, technology="breadboard"))
    assert "CONSTRUCTION: this will be built as breadboard" in built
    assert "self-resonant frequency" in built
    assert "sweeps 1 Hz to 100meg" in built


def test_cli_accepts_technology():
    from spice_agent.agent import parse_args

    a = parse_args(["--type", "lowpass", "--target", "1000",
                    "--technology", "breadboard"])
    assert a.technology == "breadboard"
    assert parse_args(["--type", "lowpass", "--target", "1000"]).technology == "ideal"
