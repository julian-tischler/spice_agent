"""
The run-history plot (spice_agent.run_plot): built purely from a run
folder's saved sweeps, circuits and verdicts - nothing is re-simulated.
"""

import json

import pytest

from spice_agent.agent import claude_loop, cli
from spice_agent.run_plot import component_table, load_run, plot_run_history

from _support.circuits import RC_LOWPASS

BAD = {"title": "too slow", "components": [
    {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
    {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "10k"},
    {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "100n"},
]}


def envelope(circuit):
    return {"is_error": False, "structured_output": circuit,
            "usage": {"input_tokens": 1, "output_tokens": 1}, "total_cost_usd": 0.0}


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    """A real two-iteration run folder: BAD (fails), then RC_LOWPASS (passes)."""
    replies = [envelope(BAD), envelope(RC_LOWPASS)]
    monkeypatch.setenv("CLAUDE_BIN", "claude")
    monkeypatch.setattr(claude_loop, "_run_cli", lambda cmd, prompt: replies.pop(0))
    monkeypatch.setenv("SPICE_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    assert cli.main(["--backend", "claude", "--type", "lowpass", "--target", "1590"]) == 0
    (d,) = [p for p in (tmp_path / "runs" / "live").iterdir() if p.is_dir()]
    return d


def test_cli_run_writes_history_png(run_dir):
    png = run_dir / "history.png"
    assert png.is_file() and png.read_bytes()[:4] == b"\x89PNG"
    manifest = json.loads((run_dir / "run.json").read_text())
    assert "history.png" in manifest["files"]


def test_load_run_reads_every_iteration(run_dir):
    run = load_run(run_dir)
    assert [it["n"] for it in run["iterations"]] == [1, 2]
    assert [it["result"]["passed"] for it in run["iterations"]] == [False, True]
    assert {"freq", "gain_db", "phase_deg"} <= set(run["iterations"][0]["sweep"].columns)


def test_explicit_output_path(run_dir, tmp_path):
    out = plot_run_history(run_dir, tmp_path / "elsewhere" / "h.png", quiet=True)
    assert out.is_file()


def test_run_without_iterations_is_an_error(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps(
        {"spec": {"filter_type": "lowpass", "target_hz": 1000.0}}))
    with pytest.raises(ValueError):
        plot_run_history(tmp_path, quiet=True)


def test_component_table_collapses_parallel_parts():
    """The live run's last design: one inductor, twenty identical caps."""
    caps = [{"id": f"C{i}", "type": "capacitor", "nodes": ["out", "0"], "value": "30n"}
            for i in range(20)]
    first = {"components": [
        {"id": "L1", "type": "inductor", "nodes": ["in", "out"], "value": "1m"},
        {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "150n"},
        {"id": "C2", "type": "capacitor", "nodes": ["out", "0"], "value": "47n"}]}
    second = {"components": [
        {"id": "L1", "type": "inductor", "nodes": ["in", "out"], "value": "1m"}, *caps]}
    labels, cells = component_table([{"circuit": first}, {"circuit": second}])
    assert labels == ["L  in - out", "C  out - 0"]
    assert cells == [["1m", "1m"], ["150n || 47n", "20x 30n"]]


def test_component_absent_in_an_iteration_shows_a_dash():
    a = {"components": [{"id": "R1", "type": "resistor", "nodes": ["in", "n1"], "value": "50"}]}
    b = {"components": [{"id": "L1", "type": "inductor", "nodes": ["in", "n1"], "value": "1m"}]}
    labels, cells = component_table([{"circuit": a}, {"circuit": b}])
    assert cells == [["50", "-"], ["-", "1m"]]


# ---------------------------------------------------------------------
# Why an iteration failed: the "failed on" row
# ---------------------------------------------------------------------

def _verdict(circuit, spec):
    from spice_agent.agent.analysis import analysis_for
    from spice_agent.simulation import simulate_and_verify

    return simulate_and_verify(dict(circuit, analysis=analysis_for(spec)), spec)


def test_fail_reason_states_the_value_and_its_limit():
    """The live E12 run of 2026-11-23: its two Bode curves are a pixel apart,
    the reason (+2.6 % against +/-2 %) is only in the numbers."""
    from spice_agent.run_plot import fail_reasons
    from spice_agent.simulation import DesignSpec

    spec = DesignSpec("lowpass", 1000, tolerance_pct=2)
    r = _verdict(RC_LOWPASS, spec)                     # 1.59 kHz: +59 %
    assert fail_reasons(r, spec) == [f"cutoff {r['error_pct']:+.1f}% (limit ±2%)"]
    assert fail_reasons(_verdict(RC_LOWPASS, DesignSpec("lowpass", 1590)), spec) == []


def test_every_failed_requirement_is_listed_in_priority_order():
    from spice_agent.run_plot import fail_reasons
    from spice_agent.simulation import DesignSpec

    spec = DesignSpec("lowpass", 1000, stopband_hz=1e4, stopband_atten_db=40,
                      technology="smd_pcb", value_series={"R": "E12"})
    r = _verdict(BAD, spec)                            # 10k / 100n: 159 Hz, 1st order
    reasons = fail_reasons(r, spec)
    assert reasons[0] == "stopband " + f"{r['stopband_atten_db']:.1f} dB (need 40 dB)"
    assert reasons[1].startswith("cutoff -84.1% (limit")


def test_off_range_and_off_series_parts_are_named():
    from spice_agent.run_plot import fail_reasons
    from spice_agent.simulation import DesignSpec

    spec = DesignSpec("lowpass", 159, technology="smd_pcb", value_series={"R": "E6"})
    r = _verdict(BAD, spec)                            # R1 = 10k is E6; C1 100n is in range
    assert fail_reasons(r, spec) == []
    spec = DesignSpec("lowpass", 133, technology="smd_pcb", value_series={"C": "E6"})
    big = {"title": "t", "components": BAD["components"][:2] + [
        {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "120n"}]}  # 133 Hz
    assert fail_reasons(_verdict(big, spec), spec) == ["not buildable: C1 too large, C1 not E6"]


def test_failed_on_row_appears_only_when_something_failed(run_dir):
    from spice_agent.run_plot import metric_rows

    run = load_run(run_dir)
    labels, cells = metric_rows(run["spec"], run["iterations"])
    assert labels[-1] == "failed on"
    assert cells[-1][0].startswith("cutoff ") and cells[-1][1] == "-"
    labels, _ = metric_rows(run["spec"], run["iterations"][1:])
    assert "failed on" not in labels
