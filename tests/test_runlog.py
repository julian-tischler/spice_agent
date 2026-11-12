"""
Per-run logging (spice_agent.runlog). Models are scripted fakes, so these
need ngspice but no network and no subscription.
"""

import json

import pytest

from spice_agent.agent import claude_loop, cli, run_agent_loop
from spice_agent.agent.loop import AgentRunResult
from spice_agent.runlog import RunRecorder
from spice_agent.simulation import DesignSpec

from _support.circuits import RC_LOWPASS
from _support.fake_gemini import NO_WAIT, FakeGeminiClient

BAD = {"title": "too slow", "components": [
    {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
    {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "10k"},
    {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "100n"},
]}


def envelope(circuit):
    return {"is_error": False, "structured_output": circuit, "session_id": "s",
            "usage": {"input_tokens": 100, "output_tokens": 20}, "total_cost_usd": 0.002}


@pytest.fixture
def fake_claude(monkeypatch):
    """Replace `claude -p` with a scripted runner (BAD first, then a good circuit)."""
    replies = [envelope(BAD), envelope(RC_LOWPASS)]
    monkeypatch.setenv("CLAUDE_BIN", "claude")
    monkeypatch.setattr(claude_loop, "_run_cli", lambda cmd, prompt: replies.pop(0))


def only_run(runs_dir):
    runs = [p for p in runs_dir.iterdir() if p.is_dir()]
    assert len(runs) == 1
    return runs[0]


def test_cli_run_writes_a_complete_folder(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("SPICE_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    argv = ["--backend", "claude", "--type", "lowpass", "--target", "1590",
            "--technology", "breadboard"]
    assert cli.main(argv) == 0

    d = only_run(tmp_path / "runs")
    assert "lowpass-1590Hz_claude-haiku" in d.name
    manifest = json.loads((d / "run.json").read_text())
    assert manifest["status"] == "passed" and manifest["iterations_used"] == 2
    assert manifest["backend"] == "claude" and manifest["model"] == "haiku"
    assert manifest["spec"]["technology"] == "breadboard"
    assert manifest["request"]["argv"] == argv
    assert manifest["environment"]["python"] and "packages" in manifest["environment"]
    assert isinstance(manifest["parasitics_profile"], dict) and manifest["parasitics_profile"]
    assert manifest["usage_total"]["output_tokens"] == 40
    assert len(manifest["iterations"]) == 2

    assert (d / "request.txt").read_text().strip().startswith("--backend claude")
    assert json.loads((d / "spec.json").read_text())["spec"]["target_hz"] == 1590
    assert "lowpass" in (d / "system_prompt.txt").read_text()

    for it in ("iter_01", "iter_02"):
        for name in ("prompt.txt", "command.json", "model_response.json", "circuit.json",
                     "circuit_full.json", "circuit.cir", "circuit_ideal.cir",
                     "ngspice.log", "sweep.csv", "result.json"):
            assert (d / it / name).is_file(), f"{it}/{name}"
    assert "R1" in (d / "iter_02" / "circuit.cir").read_text()
    assert json.loads((d / "iter_01" / "result.json").read_text())["passed"] is False
    assert json.loads((d / "iter_02" / "result.json").read_text())["passed"] is True
    assert "Simulation result" in (d / "iter_02" / "prompt.txt").read_text()

    for name in ("circuit.json", "circuit.cir", "circuit.asc", "bode.png"):
        assert (d / "final" / name).is_file(), name
    assert "final/bode.png" in manifest["files"]

    index = [json.loads(line) for line in (tmp_path / "runs" / "index.jsonl").read_text().splitlines()]
    assert index[0]["status"] == "passed" and index[0]["path"] == d.name


def test_no_log_writes_nothing(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("SPICE_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    assert cli.main(["--backend", "claude", "--no-log"]) == 0
    assert not (tmp_path / "runs").exists()


def test_explicit_outputs_still_written_and_noted(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("SPICE_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    png = tmp_path / "mine" / "plot.png"
    assert cli.main(["--backend", "claude", "--plot", str(png)]) == 0
    assert png.is_file()
    manifest = json.loads((only_run(tmp_path / "runs") / "run.json").read_text())
    assert str(png) in manifest["external_outputs"]


def test_gemini_loop_is_recorded(tmp_path):
    rec = RunRecorder.start(spec=DesignSpec(), backend="gemini", model="fake-model",
                            argv=["--x"], runs_dir=tmp_path)
    with rec:
        run = run_agent_loop(DesignSpec(), client=FakeGeminiClient([BAD, RC_LOWPASS]),
                             model_name="fake-model", retry_policy=NO_WAIT,
                             verbose=False, recorder=rec)
        rec.finish(run)
    d = only_run(tmp_path)
    assert json.loads((d / "run.json").read_text())["status"] == "passed"
    raw = json.loads((d / "iter_01" / "model_response.json").read_text())
    assert "simulate_circuit" in json.dumps(raw)
    assert (d / "iter_02" / "circuit.cir").is_file()
    assert not (d / "iter_02" / "circuit_ideal.cir").exists()   # ideal technology


def test_failed_run_status(tmp_path):
    rec = RunRecorder.start(spec=DesignSpec(), backend="gemini", model="m", argv=[], runs_dir=tmp_path)
    with rec:
        rec.finish(AgentRunResult(spec=DesignSpec(), passed=False, iterations=3))
    assert json.loads((only_run(tmp_path) / "run.json").read_text())["status"] == "failed"


def test_exception_leaves_a_readable_error_record(tmp_path):
    rec = RunRecorder.start(spec=DesignSpec(), backend="gemini", model="m", argv=[], runs_dir=tmp_path)
    with pytest.raises(RuntimeError):
        with rec:
            raise RuntimeError("boom")
    m = json.loads((only_run(tmp_path) / "run.json").read_text())
    assert m["status"] == "error" and "boom" in m["error"]


def test_keyboard_interrupt_is_aborted(tmp_path):
    rec = RunRecorder.start(spec=DesignSpec(), backend="gemini", model="m", argv=[], runs_dir=tmp_path)
    with pytest.raises(KeyboardInterrupt):
        with rec:
            raise KeyboardInterrupt
    assert json.loads((only_run(tmp_path) / "run.json").read_text())["status"] == "aborted"


def test_secrets_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-super-secret-value")
    rec = RunRecorder.start(spec=DesignSpec(), backend="gemini", model="m",
                            argv=["--note", "sk-super-secret-value"], runs_dir=tmp_path)
    with rec:
        rec.set_system_prompt("key is sk-super-secret-value")
    d = only_run(tmp_path)
    for f in d.rglob("*"):
        if f.is_file():
            assert "sk-super-secret-value" not in f.read_text(encoding="utf-8")
    assert "sk-super-secret-value" not in (tmp_path / "index.jsonl").read_text()


def test_same_second_runs_do_not_collide(tmp_path):
    spec = DesignSpec()
    roots = {RunRecorder.start(spec=spec, backend="gemini", model="m", argv=[],
                               runs_dir=tmp_path).root for _ in range(3)}
    assert len(roots) == 3


def test_disabled_recorder_accepts_every_call():
    rec = RunRecorder.disabled()
    with rec:
        rec.set_system_prompt("x")
        rec.record_model_call(1, raw={}, usage={"a": 1})
        rec.record_circuit(1, {})
        rec.record_simulation(1, {"netlist": "x"}, {"passed": True})
        rec.finish(AgentRunResult(spec=DesignSpec(), passed=True, iterations=1))
        assert rec.final_dir() is None
