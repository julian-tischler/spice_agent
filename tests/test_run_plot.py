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
