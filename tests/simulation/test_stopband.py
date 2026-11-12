"""
Tests for the stopband requirement on DesignSpec.

The point of a stopband term is that it cannot be satisfied by choosing
component values -- it constrains the filter ORDER, since an n-th order
filter only rolls off ~20*n dB/decade. These tests pin that behaviour:
a 1st-order RC that hits the cutoff exactly must still fail a steep
stopband spec, and a 3rd-order ladder must pass it.

Measured reference values (ngspice, 100 pts/decade, 1 Hz - 1 MHz):
    1st-order RC,  fc 10 kHz  ->  20.0 dB down at 100 kHz
    2nd-order RLC, fc  4.8 kHz -> 39.1 dB down at 10x fc
    3rd-order LC ladder, fc 10 kHz -> 60.0 dB down at 100 kHz
"""

import pytest

from spice_agent.simulation import metrics
from spice_agent.simulation import (
    DesignSpec,
    check_filter_spec,
    simulate_and_verify,
    simulate_get_full_sweep,
)

AC = {"type": "ac", "sweep": "dec", "points": 100, "fstart": "1", "fstop": "1meg",
      "output_node": "out"}
V1 = {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"}

# 1st order, fc = 1/(2*pi*1.59k*10n) = 10.0 kHz
RC_1ST = {"title": "1st-order RC lowpass", "components": [
    V1,
    {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "1.59k"},
    {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "10n"}]}

# 3rd-order Butterworth C-L-C ladder, 50/50 terminated, fc = 10 kHz.
# Passband sits at -6 dB because of the source/load divider, which is why
# the stopband is specified relative to the passband and not absolutely.
LADDER_3RD = {"title": "3rd-order LC Butterworth lowpass", "components": [
    V1,
    {"id": "RS", "type": "resistor", "nodes": ["in", "n1"], "value": "50"},
    {"id": "C1", "type": "capacitor", "nodes": ["n1", "0"], "value": "318n"},
    {"id": "L1", "type": "inductor", "nodes": ["n1", "out"], "value": "1.59m"},
    {"id": "C2", "type": "capacitor", "nodes": ["out", "0"], "value": "318n"},
    {"id": "RL", "type": "resistor", "nodes": ["out", "0"], "value": "50"}]}

# 1st order highpass, fc = 10 kHz
RC_HP_1ST = {"title": "1st-order RC highpass", "components": [
    V1,
    {"id": "C1", "type": "capacitor", "nodes": ["in", "out"], "value": "10n"},
    {"id": "R1", "type": "resistor", "nodes": ["out", "0"], "value": "1.59k"}]}


@pytest.fixture(scope="module")
def sweeps():
    return {name: simulate_get_full_sweep(dict(cj, analysis=AC))
            for name, cj in [("rc1", RC_1ST), ("ladder3", LADDER_3RD),
                             ("hp1", RC_HP_1ST)]}


# ---------------------------------------------------------------------
# DesignSpec validation
# ---------------------------------------------------------------------

def test_both_fields_required():
    with pytest.raises(ValueError, match="together"):
        DesignSpec("lowpass", 1e4, stopband_hz=1e5)
    with pytest.raises(ValueError, match="together"):
        DesignSpec("lowpass", 1e4, stopband_atten_db=40)


def test_stopband_must_be_on_the_correct_side():
    with pytest.raises(ValueError, match="above the cutoff"):
        DesignSpec("lowpass", 1e4, stopband_hz=5e3, stopband_atten_db=40)
    with pytest.raises(ValueError, match="below the cutoff"):
        DesignSpec("highpass", 1e4, stopband_hz=5e4, stopband_atten_db=40)
    # the valid orientations must not raise
    DesignSpec("lowpass", 1e4, stopband_hz=1e5, stopband_atten_db=40)
    DesignSpec("highpass", 1e4, stopband_hz=1e3, stopband_atten_db=40)


def test_attenuation_must_exceed_3db():
    with pytest.raises(ValueError, match="exceed 3 dB"):
        DesignSpec("lowpass", 1e4, stopband_hz=1e5, stopband_atten_db=3.0)


@pytest.mark.parametrize("ftype", ["bandpass", "notch"])
def test_rejected_for_bandpass_and_notch(ftype):
    with pytest.raises(ValueError, match="only defined for lowpass and highpass"):
        DesignSpec(ftype, 1e4, stopband_hz=1e5, stopband_atten_db=40)


def test_describe_mentions_the_stopband():
    # "above", not "at": the requirement covers the whole band.
    d = DesignSpec("lowpass", 1e4, stopband_hz=1e5, stopband_atten_db=40).describe()
    assert "40 dB below passband above 100000 Hz" in d
    d = DesignSpec("highpass", 1e4, stopband_hz=1e3, stopband_atten_db=40).describe()
    assert "40 dB below passband below 1000 Hz" in d
    # Note %g switches to exponential past 6 significant digits, so 1e6
    # renders as "1e+06" rather than "1000000".
    d = DesignSpec("lowpass", 1e4, stopband_hz=1e5, stopband_atten_db=40,
                   stopband_end_hz=1e6).describe()
    assert "everywhere from 100000 Hz to 1e+06 Hz" in d


def test_stopband_end_validation():
    with pytest.raises(ValueError, match="must lie above stopband_hz"):
        DesignSpec("lowpass", 1e4, stopband_hz=1e5, stopband_atten_db=40,
                   stopband_end_hz=5e4)
    with pytest.raises(ValueError, match="must lie below stopband_hz"):
        DesignSpec("highpass", 1e4, stopband_hz=1e3, stopband_atten_db=40,
                   stopband_end_hz=5e3)
    with pytest.raises(ValueError, match="no meaning without"):
        DesignSpec("lowpass", 1e4, stopband_end_hz=1e6)


@pytest.mark.parametrize("atten, stop_hz, expected", [
    (40, 1e5, 2),     # 1 decade, 40 dB -> 2nd order minimum
    (20, 1e5, 1),     # 1 decade, 20 dB -> 1st order is enough
    (60, 1e5, 3),     # 1 decade, 60 dB -> 3rd order
    (40, 2e4, 7),     # only a third of a decade away -> needs a lot of order
])
def test_min_order_estimate(atten, stop_hz, expected):
    spec = DesignSpec("lowpass", 1e4, stopband_hz=stop_hz, stopband_atten_db=atten)
    assert spec.min_order == expected


def test_min_order_is_none_without_a_stopband():
    assert DesignSpec("lowpass", 1e4).min_order is None


# ---------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------

def test_first_order_passes_cutoff_but_fails_stopband(sweeps):
    """The whole point: component values are right, order is not."""
    spec = DesignSpec("lowpass", 10_000, stopband_hz=100_000, stopband_atten_db=40)
    r = check_filter_spec(sweeps["rc1"], spec)

    assert r["passed"] is False
    assert abs(r["error_pct"]) < 1.0, "cutoff itself is on target"
    assert r["stopband_atten_db"] == pytest.approx(20.0, abs=0.5)
    assert r["stopband_required_atten_db"] == 40.0
    # feedback must point at the topology, not the component values
    assert "ORDER must increase" in r["message"]
    assert "short by 20.0 dB" in r["message"]


def test_third_order_ladder_passes(sweeps):
    spec = DesignSpec("lowpass", 10_000, stopband_hz=100_000, stopband_atten_db=40)
    r = check_filter_spec(sweeps["ladder3"], spec)

    assert r["passed"] is True
    assert r["stopband_atten_db"] == pytest.approx(60.0, abs=1.0)
    # attenuation is relative to the passband, which is -6 dB here
    assert r["passband_gain_db"] == pytest.approx(-6.02, abs=0.1)
    assert r["stopband_gain_db"] == pytest.approx(-66.0, abs=1.0)


def test_stopband_is_relative_to_passband_not_absolute(sweeps):
    """A -6 dB passband must not be charged 6 dB of free attenuation."""
    r = check_filter_spec(sweeps["ladder3"],
                          DesignSpec("lowpass", 10_000, stopband_hz=100_000,
                                     stopband_atten_db=40))
    assert r["stopband_atten_db"] == pytest.approx(
        r["passband_gain_db"] - r["stopband_gain_db"], abs=1e-6)


def test_first_order_passes_a_shallow_stopband(sweeps):
    r = check_filter_spec(sweeps["rc1"],
                          DesignSpec("lowpass", 10_000, stopband_hz=100_000,
                                     stopband_atten_db=18))
    assert r["passed"] is True
    assert "ORDER must increase" not in r["message"]


def test_highpass_stopband(sweeps):
    """Below the corner a 1st-order highpass gives ~20 dB/decade too."""
    ok = check_filter_spec(sweeps["hp1"],
                           DesignSpec("highpass", 10_000, stopband_hz=1_000,
                                      stopband_atten_db=18))
    assert ok["passed"] is True
    bad = check_filter_spec(sweeps["hp1"],
                            DesignSpec("highpass", 10_000, stopband_hz=1_000,
                                       stopband_atten_db=40))
    assert bad["passed"] is False
    assert "ORDER must increase" in bad["message"]


def test_stopband_outside_sweep_is_reported(sweeps):
    r = check_filter_spec(sweeps["rc1"],
                          DesignSpec("lowpass", 10_000, stopband_hz=5e6,
                                     stopband_atten_db=40))
    assert r["passed"] is False
    assert "outside the swept range" in r["message"]


def test_no_stopband_keys_when_not_specified(sweeps):
    r = check_filter_spec(sweeps["rc1"], DesignSpec("lowpass", 10_000))
    assert r["passed"] is True
    assert "stopband_atten_db" not in r
    assert "far_end_gain_db" in r, "the sanity-check field is still reported"


def test_end_to_end_through_simulate_and_verify():
    spec = DesignSpec("lowpass", 10_000, stopband_hz=100_000, stopband_atten_db=40)
    bad = simulate_and_verify(dict(RC_1ST, analysis=AC), spec)
    good = simulate_and_verify(dict(LADDER_3RD, analysis=AC), spec)
    assert bad["passed"] is False and bad["simulation_failed"] is False
    assert good["passed"] is True


# ---------------------------------------------------------------------
# Prompt + CLI wiring
# ---------------------------------------------------------------------

def test_prompt_carries_the_stopband_and_order_hint():
    from spice_agent.agent import build_system_prompt

    p = build_system_prompt(DesignSpec("lowpass", 10_000, stopband_hz=100_000,
                                       stopband_atten_db=40))
    assert "40 dB below passband above 100000 Hz" in p
    assert "ORDER SETS THE ROLLOFF" in p
    assert "20*n dB per decade" in p


# ---------------------------------------------------------------------
# The band check: responses that recover inside the stopband
# ---------------------------------------------------------------------

# A 2nd-order LC lowpass whose inductor carries 250 pF of parallel
# capacitance -- a self-resonant inductor. Measured on this circuit:
#   fc            = 15 450 Hz
#   atten @100kHz = 77.6 dB   (the parallel resonance peaks here)
#   atten floor   = 40.1 dB   (above resonance, Cp shorts out L1 and the
#                              response flattens at the Cp:C1 divider)
# So its attenuation is NOT monotonic, which is precisely what a
# single-frequency stopband check cannot see.
SELF_RESONANT = {"title": "LC lowpass with a self-resonant inductor", "components": [
    V1,
    {"id": "L1", "type": "inductor", "nodes": ["in", "out"], "value": "10m"},
    {"id": "CP", "type": "capacitor", "nodes": ["in", "out"], "value": "250p"},
    {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "25n"}]}

# Same topology with a small Cp: resonance lands high enough that the
# response stays monotonic over the band. The control case.
WELL_BEHAVED = {"title": "LC lowpass, small inductor parasitic", "components": [
    V1,
    {"id": "L1", "type": "inductor", "nodes": ["in", "out"], "value": "10m"},
    {"id": "CP", "type": "capacitor", "nodes": ["in", "out"], "value": "25p"},
    {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "25n"}]}

WIDE = {"type": "ac", "sweep": "dec", "points": 100, "fstart": "1",
        "fstop": "100meg", "output_node": "out"}


@pytest.fixture(scope="module")
def wide_sweeps():
    return {name: simulate_get_full_sweep(dict(cj, analysis=WIDE))
            for name, cj in [("resonant", SELF_RESONANT), ("clean", WELL_BEHAVED)]}


def test_band_catches_what_a_point_check_would_miss(wide_sweeps):
    """The regression this change exists for.

    At the stopband edge this circuit gives 77.6 dB -- comfortably past a
    60 dB requirement -- but it recovers to a 40 dB floor further out. The
    old single-frequency check passed it; the band check must not.
    """
    df = wide_sweeps["resonant"]
    spec = DesignSpec("lowpass", 15_000, stopband_hz=100_000, stopband_atten_db=60)

    # what the old point check saw, reconstructed
    assert metrics.gain_at(df, 100_000) * -1 == pytest.approx(77.6, abs=1.0)

    r = check_filter_spec(df, spec)
    assert r["passed"] is False
    assert r["stopband_edge_atten_db"] == pytest.approx(77.6, abs=1.0)
    assert r["stopband_atten_db"] == pytest.approx(40.1, abs=1.0)
    assert r["stopband_worst_hz"] > 1e6, "worst point is deep in the band"


def test_non_monotonic_failure_blames_resonance_not_order(wide_sweeps):
    """Feedback has to distinguish the two causes: a shallow rolloff needs
    more order, a recovering stopband needs the resonance dealt with.
    Telling the model to raise the order here would be wrong advice."""
    r = check_filter_spec(wide_sweeps["resonant"],
                          DesignSpec("lowpass", 15_000, stopband_hz=100_000,
                                     stopband_atten_db=60))
    assert "NOT at the band edge" in r["message"]
    assert "resonating" in r["message"]
    assert "ORDER must increase" not in r["message"]


def test_edge_limited_failure_still_blames_order(wide_sweeps):
    """The control: when the worst point IS the band edge, the advice is
    still to increase the order."""
    r = check_filter_spec(wide_sweeps["clean"],
                          DesignSpec("lowpass", 15_000, stopband_hz=150_000,
                                     stopband_atten_db=60))
    assert r["passed"] is False
    assert r["stopband_worst_hz"] == pytest.approx(150_000, rel=0.02)
    assert "ORDER must increase" in r["message"]
    assert "resonating" not in r["message"]


def test_explicit_band_end_narrows_the_requirement(wide_sweeps):
    """Same circuit, same attenuation figure, two band widths.

    Measured: this filter gives 61.9 dB at its worst between 98-105 kHz
    (it is sitting on its resonance peak there) but only 40.1 dB once the
    band runs out to 100 MHz. So a 55 dB requirement is met over the
    narrow band and missed over the open-ended one -- which is the whole
    semantic difference this field introduces.
    """
    df = wide_sweeps["resonant"]

    narrow = check_filter_spec(df, DesignSpec(
        "lowpass", 15_000, stopband_hz=98_000, stopband_atten_db=55,
        stopband_end_hz=105_000))
    assert narrow["passed"] is True
    assert narrow["stopband_end_hz"] == 105_000
    assert narrow["stopband_atten_db"] == pytest.approx(61.9, abs=1.0)

    open_ended = check_filter_spec(df, DesignSpec(
        "lowpass", 15_000, stopband_hz=98_000, stopband_atten_db=55))
    assert open_ended["passed"] is False
    assert open_ended["stopband_atten_db"] == pytest.approx(40.1, abs=1.0)


def test_band_defaults_to_the_end_of_the_sweep(wide_sweeps):
    r = check_filter_spec(wide_sweeps["clean"],
                          DesignSpec("lowpass", 15_000, stopband_hz=150_000,
                                     stopband_atten_db=40))
    assert r["stopband_end_hz"] == pytest.approx(1e8, rel=1e-6)


def test_monotonic_filter_band_equals_point(sweeps):
    """For a monotonic response the band's worst point IS the edge, so the
    stricter check costs nothing on every circuit we had before."""
    for name, atten in (("rc1", 20.0), ("ladder3", 60.0)):
        r = check_filter_spec(sweeps[name],
                              DesignSpec("lowpass", 10_000, stopband_hz=100_000,
                                         stopband_atten_db=atten - 1))
        assert r["stopband_atten_db"] == pytest.approx(atten, abs=0.5)
        assert r["stopband_worst_hz"] == pytest.approx(100_000, rel=0.02)
        assert r["stopband_atten_db"] == pytest.approx(
            r["stopband_edge_atten_db"], abs=0.01)


def test_worst_attenuation_is_direction_agnostic(wide_sweeps):
    """A highpass passes its band descending; argument order must not matter."""
    df = wide_sweeps["clean"]
    pb = df["gain_db"].iloc[0]
    assert (metrics.worst_attenuation(df, pb, 1e5, 1e7)
            == metrics.worst_attenuation(df, pb, 1e7, 1e5))


def test_cli_parses_stopband_args():
    from spice_agent.agent import parse_args

    a = parse_args(["--type", "lowpass", "--target", "10000",
                    "--stopband-hz", "100000", "--stopband-atten", "40"])
    assert a.stopband_hz == 100_000 and a.stopband_atten == 40
    assert a.stopband_end is None


def test_cli_stopband_end_reaches_the_spec(monkeypatch):
    """--stopband-end must land in the DesignSpec the loop is run with."""
    import spice_agent.agent.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "run_agent_loop",
                        lambda spec, **kw: seen.setdefault("spec", spec) and
                        cli.AgentRunResult(spec=spec, passed=False, iterations=0))
    cli.main(["--no-log", "--type", "lowpass", "--target", "10000",
              "--stopband-hz", "100000", "--stopband-atten", "40",
              "--stopband-end", "1e6"])
    assert seen["spec"].stopband_end_hz == 1e6
    assert "everywhere from 100000 Hz to 1e+06 Hz" in seen["spec"].describe()


def test_plot_marks_the_stopband(tmp_path):
    from spice_agent.plotting import plot_bode

    out = tmp_path / "sb.png"
    plot_bode(dict(RC_1ST, analysis=AC), save_path=out,
              spec=DesignSpec("lowpass", 10_000, stopband_hz=100_000,
                              stopband_atten_db=40), quiet=True)
    assert out.exists() and out.read_bytes()[:4] == b"\x89PNG"


# ---------------------------------------------------------------------
# Feedback when both causes apply, and the actionable extras
# ---------------------------------------------------------------------

def _synthetic_lowpass(fc=1e4, leak_db_at_1e8=-25.0):
    """2nd-order Butterworth plus a leakage path rising 20 dB/decade - the
    shape a breadboard's input-output stray capacitance produces. At
    100 kHz it is 40.0 dB down (order-limited); at 100 MHz the leak has
    pulled it back up to ~25 dB (interior failure)."""
    import numpy as np
    import pandas as pd
    f = np.logspace(0, 8, 801)
    mag = 1 / np.sqrt(1 + (f / fc) ** 4) + 10 ** (leak_db_at_1e8 / 20) * (f / 1e8)
    return pd.DataFrame({"freq": f, "gain_db": 20 * np.log10(mag)})


def test_both_stopband_causes_are_reported():
    """The live breadboard run that motivated this: 38.9 dB at the edge AND
    25 dB at 100 MHz. Reporting only the resonance told the model 'more
    order will not help', and it never tried 3rd order."""
    r = check_filter_spec(_synthetic_lowpass(),
                          DesignSpec("lowpass", 10_000, stopband_hz=100_000,
                                     stopband_atten_db=45))
    assert r["passed"] is False
    assert r["stopband_causes"] == ["order", "resonance"]
    assert "TWO separate stopband problems" in r["message"]
    assert "ORDER must increase" in r["message"]
    assert "NOT at the band edge" in r["message"]
    assert "raising the order fixes (1) but not this" in r["message"]
    assert "increasing the order will not help on its own" not in r["message"]


def test_order_advice_names_an_order():
    """1 decade, 45 dB (+3 dB margin): Butterworth order 2 gives 40.0 dB,
    order 3 gives 60.0 dB, so 3 is the suggestion; the synthetic filter
    itself is 2nd order."""
    r = check_filter_spec(_synthetic_lowpass(),
                          DesignSpec("lowpass", 10_000, stopband_hz=100_000,
                                     stopband_atten_db=45))
    assert r["suggested_order"] == 3
    assert r["effective_order"] == pytest.approx(2.0, abs=0.1)
    assert "Butterworth of order 3 gives 60.0 dB" in r["message"]
    assert "order 2 gives only 40.0 dB" in r["message"]


def test_failed_stopband_carries_a_profile():
    r = check_filter_spec(_synthetic_lowpass(),
                          DesignSpec("lowpass", 10_000, stopband_hz=100_000,
                                     stopband_atten_db=45))
    hz = [p["hz"] for p in r["stopband_profile"]]
    assert hz == pytest.approx([1e5, 1e6, 1e7, 1e8], rel=1e-3)
    assert r["stopband_profile"][-1]["atten_db"] == pytest.approx(r["stopband_atten_db"], abs=0.1)
    assert "attenuation across the band: 100 kHz" in r["message"]


def test_highpass_profile_runs_downward(sweeps):
    r = check_filter_spec(sweeps["hp1"],
                          DesignSpec("highpass", 10_000, stopband_hz=1_000,
                                     stopband_atten_db=40))
    hz = [p["hz"] for p in r["stopband_profile"]]
    assert hz[0] == pytest.approx(1_000) and hz == sorted(hz, reverse=True)


def test_cutoff_miss_gives_the_exact_scale_factor(sweeps):
    """RC at 10 kHz judged against a 5 kHz target: k = 2. Applying the
    hint must actually land on target."""
    r = check_filter_spec(sweeps["rc1"], DesignSpec("lowpass", 5_000))
    assert r["passed"] is False
    k = r["frequency_scale_factor"]
    assert k == pytest.approx(2.0, rel=0.02)
    assert "multiply EVERY inductor and capacitor value by" in r["message"]

    scaled = dict(RC_1ST, components=[
        dict(c, value=f"{10e-9 * k:.6g}") if c["id"] == "C1" else c
        for c in RC_1ST["components"]], analysis=AC)
    assert simulate_and_verify(scaled, DesignSpec("lowpass", 5_000))["passed"] is True


def test_no_scale_hint_when_on_target(sweeps):
    r = check_filter_spec(sweeps["rc1"], DesignSpec("lowpass", 10_000))
    assert "frequency_scale_factor" not in r
    assert "multiply EVERY" not in r["message"]
