"""
Tests for the Bode plot (spice_agent.plotting).

A plot is hard to assert on directly, so these check the two things that
actually go wrong: that a file is produced at all, and that the
annotations match the filter type — i.e. a bandpass is marked at its
peak and a notch at its null, rather than everything being treated as a
lowpass the way the original version did.
"""

from pathlib import Path

import pytest

from spice_agent.simulation import metrics
from spice_agent.plotting import plot_bode
from spice_agent.simulation import DesignSpec, simulate_get_full_sweep
from _support.circuits import RC_HIGHPASS, RC_LOWPASS, RLC_BANDPASS, RLC_NOTCH

AC = {"type": "ac", "sweep": "dec", "points": 100, "fstart": "1", "fstop": "1meg",
      "output_node": "out"}

CASES = {
    "lowpass": (RC_LOWPASS, DesignSpec("lowpass", 1590), 1591),
    "highpass": (RC_HIGHPASS, DesignSpec("highpass", 1590), 1591),
    "bandpass": (RLC_BANDPASS, DesignSpec("bandpass", 5030, bandwidth_hz=1590), 5033),
    "notch": (RLC_NOTCH, DesignSpec("notch", 5030), 5033),
}


@pytest.fixture(scope="module")
def sweeps():
    return {name: simulate_get_full_sweep(dict(cj, analysis=AC))
            for name, (cj, _, _) in CASES.items()}


@pytest.mark.parametrize("name", list(CASES))
def test_writes_a_png(name, sweeps, tmp_path):
    cj, spec, _ = CASES[name]
    out = tmp_path / f"{name}.png"
    df = plot_bode(dict(cj, analysis=AC), save_path=out, spec=spec,
                   df=sweeps[name], quiet=True)
    assert out.exists() and out.stat().st_size > 5000
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert {"freq", "gain_db", "phase_deg"} <= set(df.columns)


@pytest.mark.parametrize("name", list(CASES))
def test_annotation_marks_the_right_feature(name, sweeps, tmp_path, monkeypatch):
    """The measured vertical line must land on this filter type's own
    feature — the peak for a bandpass, the null for a notch, the corner
    for lowpass/highpass — not always on a lowpass -3dB crossing."""
    import matplotlib.axes

    cj, spec, expected_hz = CASES[name]
    vlines = []
    orig = matplotlib.axes.Axes.axvline

    def record(self, x=0, *a, **kw):
        vlines.append(x)
        return orig(self, x, *a, **kw)

    monkeypatch.setattr(matplotlib.axes.Axes, "axvline", record)
    plot_bode(dict(cj, analysis=AC), save_path=tmp_path / "p.png", spec=spec,
              df=sweeps[name], quiet=True)

    assert any(abs(x - expected_hz) / expected_hz < 0.03 for x in vlines), \
        f"no measured line near {expected_hz} Hz; got {vlines}"
    # the target line is drawn too, on both panels
    assert sum(abs(x - spec.target_hz) < 1e-6 for x in vlines) == 2


def test_notch_is_not_annotated_as_a_lowpass(sweeps, tmp_path, monkeypatch):
    """Regression: the RLC notch has a -3dB crossing around 4.3 kHz on the
    way into the null. A lowpass-style annotation would mark that instead
    of the 5.03 kHz notch itself."""
    import matplotlib.axes

    cj, spec, _ = CASES["notch"]
    df = sweeps["notch"]
    lowpass_artifact = metrics.lowpass_cutoff(df)
    f_notch, _ = metrics.notch(df)
    assert lowpass_artifact is not None and abs(lowpass_artifact - f_notch) > 300

    vlines = []
    orig = matplotlib.axes.Axes.axvline
    monkeypatch.setattr(matplotlib.axes.Axes, "axvline",
                        lambda self, x=0, *a, **kw: (vlines.append(x), orig(self, x, *a, **kw))[1])
    plot_bode(dict(cj, analysis=AC), save_path=tmp_path / "n.png", spec=spec, df=df, quiet=True)

    assert all(abs(x - lowpass_artifact) > 100 for x in vlines)


def test_legacy_target_cutoff_kwarg_still_works(sweeps, tmp_path):
    out = tmp_path / "legacy.png"
    plot_bode(dict(RC_LOWPASS, analysis=AC), save_path=out,
              target_cutoff_hz=1590.0, df=sweeps["lowpass"], quiet=True)
    assert out.exists()


def test_no_spec_defaults_to_lowpass(sweeps, tmp_path):
    out = tmp_path / "nospec.png"
    plot_bode(dict(RC_LOWPASS, analysis=AC), save_path=out, df=sweeps["lowpass"], quiet=True)
    assert out.exists()


def test_simulates_when_no_dataframe_given(tmp_path):
    out = tmp_path / "sim.png"
    df = plot_bode(dict(RC_LOWPASS, analysis=AC), save_path=out,
                   spec=DesignSpec("lowpass", 1590), quiet=True)
    assert out.exists() and len(df) > 100


def test_agent_cli_writes_plot(tmp_path, monkeypatch):
    import spice_agent.agent.cli as agent_cli
    from _support.fake_gemini import NO_WAIT, FakeGeminiClient

    fake = FakeGeminiClient([RC_LOWPASS])
    real_loop = agent_cli.run_agent_loop
    monkeypatch.setattr(
        agent_cli, "run_agent_loop",
        lambda spec, **kw: real_loop(spec, client=fake, retry_policy=NO_WAIT, verbose=False),
    )
    png = tmp_path / "design.png"
    rc = agent_cli.main(["--type", "lowpass", "--target", "1590", "--plot", str(png)])
    assert rc == 0
    assert png.exists() and png.read_bytes()[:4] == b"\x89PNG"
