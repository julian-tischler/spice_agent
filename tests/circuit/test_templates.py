"""
Verify every NETLIST_TEMPLATE_* in constants.py against the behaviour
its comment promises (cutoff / centre frequency, passband gain, rolloff
slope, notch depth, ...).

Run with pytest:
    python -m pytest tests/test_netlist_templates.py -v

Or as a plain script to get a human-readable summary table of measured
vs expected values for all templates (no pytest needed):
    python tests/test_netlist_templates.py
"""

import math
import sys

import pytest

from spice_agent.templates import NETLIST_TEMPLATES
from _support.ngspice_raw import (
    run_template,
    gain_at,
    lowpass_cutoff,
    highpass_cutoff,
    peak,
    notch,
    bandpass_bandwidth,
    rolloff_db_per_decade,
)


def _within_pct(measured: float, expected: float, pct: float) -> bool:
    return abs(measured - expected) <= abs(expected) * pct / 100


# ---------------------------------------------------------------------
# Analytical expectations. Every entry here corresponds to a formula in
# the template's comment block in constants.py.
# ---------------------------------------------------------------------
RC_FC = 1 / (2 * math.pi * 1e3 * 100e-9)                # 1k / 100n  -> 1591.5 Hz
RLC_F0 = 1 / (2 * math.pi * math.sqrt(10e-3 * 100e-9))  # 10m / 100n -> 5032.9 Hz
RLC_BW = 100 / (2 * math.pi * 10e-3)                    # R/(2*pi*L) -> 1591.5 Hz
LC_FC = 10e3                                            # designed for 10 kHz
DIVIDER_DB = 20 * math.log10(0.5)                       # -6.02 dB


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def sweeps():
    """Simulate every template once and cache the results."""
    return {name: run_template(tpl) for name, tpl in NETLIST_TEMPLATES.items()}


# ---------------------------------------------------------------------
# Generic checks that apply to every template
# ---------------------------------------------------------------------

@pytest.mark.parametrize("name", list(NETLIST_TEMPLATES))
def test_template_simulates(sweeps, name):
    df = sweeps[name]
    assert len(df) > 100, "AC sweep returned too few points"
    assert df["gain_db"].notna().all()
    assert df["phase_deg"].notna().all()
    assert df["freq"].iloc[0] == pytest.approx(1.0)
    assert df["freq"].iloc[-1] == pytest.approx(1e6)


# ---------------------------------------------------------------------
# Low-pass filters (templates 1, 2, 3, 9)
# ---------------------------------------------------------------------

def test_rc_lowpass_1st(sweeps):
    df = sweeps["rc_lowpass_1st"]
    assert gain_at(df, 1.0) == pytest.approx(0.0, abs=0.05)
    assert _within_pct(lowpass_cutoff(df), RC_FC, 5)
    assert rolloff_db_per_decade(df, 100e3, 1e6) == pytest.approx(-20, abs=1)


def test_rlc_lowpass_2nd(sweeps):
    df = sweeps["rlc_lowpass_2nd"]
    assert gain_at(df, 1.0) == pytest.approx(0.0, abs=0.05)
    # Q ~ 0.67 (slightly under Butterworth) pulls the -3dB point a little
    # below the natural frequency, hence the looser tolerance here.
    assert _within_pct(lowpass_cutoff(df), RLC_F0, 10)
    # No resonant peaking above the passband
    _, peak_db = peak(df)
    assert peak_db <= 0.5
    assert rolloff_db_per_decade(df, 100e3, 1e6) == pytest.approx(-40, abs=1)


def test_lc_butterworth_lowpass_3rd(sweeps):
    df = sweeps["lc_butterworth_lowpass_3rd"]
    # Doubly-terminated 50/50 ladder: passband is -6.02 dB, not 0 dB
    assert gain_at(df, 10.0) == pytest.approx(DIVIDER_DB, abs=0.1)
    assert _within_pct(lowpass_cutoff(df), LC_FC, 5)
    # Butterworth: maximally flat, no peaking above the passband level
    _, peak_db = peak(df)
    assert peak_db <= DIVIDER_DB + 0.1
    assert rolloff_db_per_decade(df, 100e3, 1e6) == pytest.approx(-60, abs=1)


def test_cascaded_rc_lowpass_3rd(sweeps):
    df = sweeps["cascaded_rc_lowpass_3rd"]
    assert gain_at(df, 1.0) == pytest.approx(0.0, abs=0.05)
    # Inter-stage loading pushes the -3dB point well below the
    # single-stage 1.59 kHz; the exact value has no clean formula.
    fc = lowpass_cutoff(df)
    assert 250 <= fc <= 450
    assert rolloff_db_per_decade(df, 100e3, 1e6) == pytest.approx(-60, abs=1)


# ---------------------------------------------------------------------
# High-pass (template 4)
# ---------------------------------------------------------------------

def test_rc_highpass_1st(sweeps):
    df = sweeps["rc_highpass_1st"]
    assert gain_at(df, 1e6) == pytest.approx(0.0, abs=0.05)
    assert _within_pct(highpass_cutoff(df), RC_FC, 5)
    # Rising +20 dB/decade well below the corner
    assert rolloff_db_per_decade(df, 1.0, 10.0) == pytest.approx(+20, abs=1)
    # Low-pass detection must NOT find a crossing on a high-pass response
    assert lowpass_cutoff(df) is None


# ---------------------------------------------------------------------
# Band-pass / band-stop (templates 5, 6, 7)
# ---------------------------------------------------------------------

def test_rlc_bandpass(sweeps):
    df = sweeps["rlc_bandpass"]
    f_peak, g_peak = peak(df)
    assert _within_pct(f_peak, RLC_F0, 5)
    assert g_peak == pytest.approx(0.0, abs=0.1)
    bw = bandpass_bandwidth(df)
    assert bw is not None
    assert _within_pct(bw, RLC_BW, 15)
    # Strong attenuation at both extremes of the sweep
    assert gain_at(df, 1.0) < -40
    assert gain_at(df, 1e6) < -40


def test_rlc_notch(sweeps):
    df = sweeps["rlc_notch"]
    f_notch, g_notch = notch(df)
    assert _within_pct(f_notch, RLC_F0, 5)
    assert g_notch < -25
    # Passband on both sides of the notch
    assert gain_at(df, 1.0) == pytest.approx(0.0, abs=0.05)
    assert gain_at(df, 1e6) == pytest.approx(0.0, abs=0.05)


def test_twin_t_notch(sweeps):
    df = sweeps["twin_t_notch"]
    f_notch, g_notch = notch(df)
    assert _within_pct(f_notch, RC_FC, 5)
    assert g_notch < -40
    assert gain_at(df, 1.0) == pytest.approx(0.0, abs=0.05)
    assert gain_at(df, 1e6) == pytest.approx(0.0, abs=0.05)


# ---------------------------------------------------------------------
# Frequency-independent sanity check (template 8)
# ---------------------------------------------------------------------

def test_resistive_divider(sweeps):
    df = sweeps["resistive_divider"]
    assert (df["gain_db"] - DIVIDER_DB).abs().max() < 0.01
    assert df["phase_deg"].abs().max() < 0.01
    assert lowpass_cutoff(df) is None
    assert highpass_cutoff(df) is None


# ---------------------------------------------------------------------
# Script mode: print a summary table instead of running assertions
# ---------------------------------------------------------------------

EXPECTED_FREQ = {
    "rc_lowpass_1st": RC_FC,
    "rlc_lowpass_2nd": RLC_F0,
    "lc_butterworth_lowpass_3rd": LC_FC,
    "rc_highpass_1st": RC_FC,
    "rlc_bandpass": RLC_F0,
    "rlc_notch": RLC_F0,
    "twin_t_notch": RC_FC,
    "resistive_divider": None,
    "cascaded_rc_lowpass_3rd": None,
}


def _fmt(x, unit=""):
    return "-" if x is None else f"{x:,.1f}{unit}"


def main():
    print(f"Simulating {len(NETLIST_TEMPLATES)} templates via ngspice...\n")
    header = (
        f"{'template':28s} {'passband':>11s} {'fc/f0 (Hz)':>12s} "
        f"{'expected':>10s} {'rolloff':>14s} {'extra':>14s}"
    )
    print(header)
    print("-" * len(header))

    failures = 0
    for name, tpl in NETLIST_TEMPLATES.items():
        try:
            df = run_template(tpl)
        except Exception as e:
            failures += 1
            print(f"{name:28s} FAILED: {e}")
            continue

        if "highpass" in name:
            fc = highpass_cutoff(df)
            passband = gain_at(df, 1e6)
            slope = rolloff_db_per_decade(df, 1.0, 10.0)
            extra = ""
        elif "bandpass" in name:
            fc, g_peak = peak(df)
            passband = g_peak
            slope = None
            extra = f"BW {_fmt(bandpass_bandwidth(df), ' Hz')}"
        elif "notch" in name:
            fc, g_notch = notch(df)
            passband = gain_at(df, 1.0)
            slope = None
            extra = f"depth {g_notch:.1f} dB"
        else:
            fc = lowpass_cutoff(df)
            passband = gain_at(df, 1.0)
            slope = rolloff_db_per_decade(df, 100e3, 1e6)
            extra = ""

        slope_s = "-" if slope is None else f"{slope:+.1f} dB/dec"
        print(
            f"{name:28s} {passband:>8.2f} dB {_fmt(fc):>12s} "
            f"{_fmt(EXPECTED_FREQ[name]):>10s} {slope_s:>14s} {extra:>14s}"
        )

    print("\nRun `python -m pytest tests/test_netlist_templates.py -v` for pass/fail assertions.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
