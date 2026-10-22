"""
Tests for the optional LTspice export (spice_agent.ltspice_export).

Three layers:

1. Offline (always run): the .cir text, and structural checks on the
   .asc — every component placed, values/flags present, 16-unit grid,
   and a pure-Python re-netlisting of the schematic from its wires, flags
   and pin positions that must reproduce the circuit exactly. This is
   the same job LTspice's netlister does, so it catches a bad rotation
   or a pin that missed a wire without needing LTspice at all.

2. LTspice-driven (skipped unless LTspice.exe is found): ask LTspice
   itself to netlist the .asc and compare; then batch-simulate it and
   compare the response against ngspice on the same circuit.

3. CLI: `--asc/--cir` on the agent write the files (with a fake model).

Run:
    python -m pytest tests/test_ltspice_export.py -v
    python -m pytest tests/test_ltspice_export.py -v -m "not ltspice"
"""

import re
from pathlib import Path

import pytest

from spice_agent.circuit import Circuit
from spice_agent.export import (
    SYMBOLS,
    export,
    find_ltspice,
    layout_circuit,
    ltspice_netlist,
    ltspice_run,
    parse_ascii_raw,
    pin_positions,
    to_asc,
    to_ltspice_netlist,
)
from _support.circuits import (
    DIVIDER,
    RC_HIGHPASS,
    RC_LOWPASS,
    RLC_BANDPASS,
    RLC_NOTCH,
    TWIN_T_NOTCH,
)
from _support.fake_gemini import NO_WAIT, FakeGeminiClient

AC = {"type": "ac", "sweep": "dec", "points": 100, "fstart": "1", "fstop": "1meg",
      "output_node": "out"}

# Skip marker for every check that shells out to LTspice.exe.
needs_ltspice = pytest.mark.skipif(find_ltspice() is None, reason="LTspice.exe not found")

# Three-stage cascaded RC: a longer ladder with a stub on every node
CASCADED_RC = {
    "title": "Cascaded RC",
    "components": [
        {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
        {"id": "R1", "type": "resistor", "nodes": ["in", "n1"], "value": "1k"},
        {"id": "C1", "type": "capacitor", "nodes": ["n1", "0"], "value": "100n"},
        {"id": "R2", "type": "resistor", "nodes": ["n1", "n2"], "value": "1k"},
        {"id": "C2", "type": "capacitor", "nodes": ["n2", "0"], "value": "100n"},
        {"id": "R3", "type": "resistor", "nodes": ["n2", "out"], "value": "1k"},
        {"id": "C3", "type": "capacitor", "nodes": ["out", "0"], "value": "100n"},
    ],
}
# Same RC low-pass but with node orders reversed on every component, so
# the layout has to use the "other" rotation for each symbol.
RC_LOWPASS_FLIPPED = {
    "title": "RC low-pass (flipped nodes)",
    "components": [
        {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
        {"id": "R1", "type": "resistor", "nodes": ["out", "in"], "value": "1k"},
        {"id": "C1", "type": "capacitor", "nodes": ["0", "out"], "value": "100n"},
    ],
}

CASES = {
    "rc_lowpass": (RC_LOWPASS, "ladder"),
    "rc_lowpass_flipped": (RC_LOWPASS_FLIPPED, "ladder"),
    "rc_highpass": (RC_HIGHPASS, "ladder"),
    "rlc_bandpass": (RLC_BANDPASS, "ladder"),
    "rlc_notch": (RLC_NOTCH, "ladder"),
    "cascaded_rc": (CASCADED_RC, "ladder"),
    "divider": (DIVIDER, "ladder"),
    "twin_t": (TWIN_T_NOTCH, "netlabel"),
}


def _circuit(cj: dict) -> Circuit:
    return Circuit.from_dict(dict(cj, analysis=AC))


def _expected_netlist(c: Circuit) -> set[tuple[str, frozenset, str]]:
    """{(id, {nodes}, value)} — node order ignored (passives are symmetric)."""
    return {(x.id, frozenset(x.nodes), x.value) for x in c.components}


def _parse_ltspice_net(text: str) -> set[tuple[str, frozenset, str]]:
    out = set()
    for line in text.splitlines():
        if line and line[0] in "RLCVI":
            parts = line.split()
            out.add((parts[0], frozenset(parts[1:3]), " ".join(parts[3:])))
    return out


# ---------------------------------------------------------------------
# 1. Offline
# ---------------------------------------------------------------------

def test_ltspice_netlist_text():
    text = to_ltspice_netlist(_circuit(RC_LOWPASS))
    lines = text.strip().splitlines()
    assert lines[0] == "RC low-pass"
    assert "V1 in 0 AC 1" in lines
    assert "R1 in out 1k" in lines
    assert "C1 out 0 100n" in lines
    assert ".ac dec 100 1 1meg" in lines
    assert ".print ac vdb(out) vp(out)" in lines
    assert lines[-1] == ".end"
    assert ".control" not in text and "wrdata" not in text


def test_netlist_print_line_is_optional():
    text = to_ltspice_netlist(_circuit(RC_LOWPASS), include_print=False)
    assert ".print" not in text
    assert ".ac dec 100 1 1meg" in text


def test_exported_netlist_runs_in_ngspice(tmp_path):
    """ngspice -b rejects a netlist with no .print/.plot/.fourier line, so
    the exported .cir must carry one to be runnable by BOTH simulators."""
    import subprocess

    from spice_agent.paths import require_ngspice

    cir = tmp_path / "x.cir"
    cir.write_text(to_ltspice_netlist(_circuit(RC_LOWPASS)), encoding="ascii")
    p = subprocess.run([require_ngspice(), "-b", str(cir)],
                       capture_output=True, text=True, timeout=300)
    combined = p.stdout + p.stderr
    assert "no simulations run" not in combined, combined[-500:]
    assert "vdb(out)" in p.stdout, "AC table should be printed to stdout"
    assert p.returncode == 0


@needs_ltspice
@pytest.mark.ltspice
def test_exported_netlist_runs_in_ltspice(tmp_path):
    """The .print line must not stop LTspice from running the same file."""
    import subprocess

    from spice_agent.export import require_ltspice

    cir = tmp_path / "y.cir"
    cir.write_text(to_ltspice_netlist(_circuit(RC_LOWPASS)), encoding="ascii")
    p = subprocess.run([require_ltspice(), "-Run", "-b", "-ascii", str(cir)],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0
    assert cir.with_suffix(".raw").exists(), "LTspice produced no .raw"


@pytest.mark.parametrize("name", list(CASES))
def test_layout_strategy(name):
    cj, strategy = CASES[name]
    assert layout_circuit(_circuit(cj)).strategy == strategy


@pytest.mark.parametrize("name", list(CASES))
def test_asc_structure(name):
    cj, _ = CASES[name]
    c = _circuit(cj)
    text, layout = to_asc(c)
    lines = text.splitlines()

    assert lines[0] == "Version 4"
    assert lines[1].startswith("SHEET 1 ")
    assert text.isascii()

    # every component placed exactly once, with its value
    for comp in c.components:
        assert lines.count(f"SYMATTR InstName {comp.id}") == 1
        if comp.type == "voltage_source":
            assert 'SYMATTR Value ""' in text and f"SYMATTR Value2 {comp.value}" in text
        else:
            assert f"SYMATTR Value {comp.value}" in text
        assert f"SYMBOL {SYMBOLS[comp.type].name} " in text

    # ground and output are labelled; analysis directive present
    flags = [l for l in lines if l.startswith("FLAG ")]
    assert any(l.endswith(" 0") for l in flags)
    assert any(l.endswith(" out") for l in flags)
    assert "TEXT" in text and "!.ac dec 100 1 1meg" in text

    # everything on the 16-unit grid
    for l in lines:
        if l.startswith(("WIRE", "FLAG", "SYMBOL")):
            nums = [int(t) for t in l.split()[1:] if re.fullmatch(r"-?\d+", t)]
            assert nums and all(n % 16 == 0 for n in nums), l


class _Nets:
    """Union-find over sheet points, built from wires. Used to re-netlist."""
    def __init__(self):
        self.parent = {}

    def find(self, p):
        self.parent.setdefault(p, p)
        while self.parent[p] != p:
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        return p

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _renetlist(layout) -> set[tuple[str, frozenset, str]]:
    """
    Reproduce what LTspice's netlister would do: points joined by wires
    are one net; a FLAG names the net; a pin lying on a net takes its
    name. Two pins on the same unnamed net share an auto name.
    """
    nets = _Nets()
    for x1, y1, x2, y2 in layout.wires:
        nets.union((x1, y1), (x2, y2))
    names = {}
    for x, y, net in layout.flags:
        root = nets.find((x, y))
        assert names.get(root, net) == net, f"two names on one net: {names[root]} / {net}"
        names[root] = net

    out = set()
    auto = {}
    for p in layout.placed:
        node_names = []
        for pin in p.pins:
            root = nets.find(pin)
            if root not in names:
                names[root] = auto.setdefault(root, f"N{len(auto) + 1:03d}")
            node_names.append(names[root])
        out.add((p.comp.id, frozenset(node_names), p.comp.value))
    return out


@pytest.mark.parametrize("name", list(CASES))
def test_asc_connectivity_reproduces_circuit(name):
    cj, _ = CASES[name]
    c = _circuit(cj)
    _, layout = to_asc(c)
    assert _renetlist(layout) == _expected_netlist(c)


def test_pins_never_overlap_between_components():
    for name, (cj, _) in CASES.items():
        _, layout = to_asc(_circuit(cj))
        seen = {}
        for p in layout.placed:
            for pin in p.pins:
                assert pin not in seen, f"{name}: {p.comp.id} and {seen[pin]} share pin point {pin}"
                seen[pin] = p.comp.id


def test_rotation_math_matches_known_ltspice_file():
    # From a real LTspice export: `SYMBOL res 224 80 R90` has its pins on
    # (208, 96) and (128, 96).
    a, b = pin_positions(SYMBOLS["resistor"], 224, 80, 90)
    assert {a, b} == {(208, 96), (128, 96)}


def test_export_writes_files(tmp_path):
    info = export(dict(RC_LOWPASS, analysis=AC),
                  asc_path=tmp_path / "x.asc", cir_path=tmp_path / "x.cir")
    assert info["layout"] == "ladder"
    assert (tmp_path / "x.asc").read_text().startswith("Version 4")
    assert ".ac dec 100 1 1meg" in (tmp_path / "x.cir").read_text()


def test_export_rejects_invalid_circuit(tmp_path):
    bad = {"title": "no ground", "components": [
        {"id": "V1", "type": "voltage_source", "nodes": ["in", "ref"], "value": "AC 1"},
        {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "1k"},
    ], "analysis": AC}
    with pytest.raises(ValueError):
        export(bad, asc_path=tmp_path / "bad.asc")
    assert not (tmp_path / "bad.asc").exists()


# ---------------------------------------------------------------------
# 2. Driven by LTspice.exe
# ---------------------------------------------------------------------


# ---------------------------------------------------------------------
# Exporting the AS-BUILT circuit (parasitics included)
# ---------------------------------------------------------------------

def test_as_built_is_a_no_op_for_ideal():
    from spice_agent.export import as_built_circuit

    c = _circuit(RC_LOWPASS)
    assert as_built_circuit(c, None) is c
    assert as_built_circuit(c, "ideal") is c


def test_as_built_expands_into_real_components():
    """The expansion's own output lines must parse back into valid
    Components, or the exporter cannot draw what was simulated."""
    from spice_agent.export import as_built_circuit

    built = as_built_circuit(_circuit(RC_LOWPASS), "breadboard")
    ids = [x.id for x in built.components]
    assert len(ids) > len(RC_LOWPASS["components"])
    assert len(ids) == len(set(ids)), "element ids must stay unique"
    assert "V1" in ids and "R1" in ids and "C1" in ids, "design survives"
    assert "C_io_coupling" in ids, "and the parasitics are present"
    assert "as built: breadboard" in built.title
    built.validate()   # must still be a legal circuit


def test_as_built_netlist_carries_the_parasitics():
    ideal = to_ltspice_netlist(_circuit(RC_LOWPASS))
    built = to_ltspice_netlist(_circuit(RC_LOWPASS), technology="breadboard")
    assert "C_io_coupling" not in ideal
    assert "C_io_coupling" in built
    # the designed values are still in there, not replaced
    assert "1k" in built and "100n" in built
    assert ".print ac vdb(out) vp(out)" in built


def test_export_writes_as_built_when_technology_given(tmp_path):
    ideal = export(dict(RC_LOWPASS, analysis=AC), cir_path=tmp_path / "i.cir")
    built = export(dict(RC_LOWPASS, analysis=AC), asc_path=tmp_path / "b.asc",
                   cir_path=tmp_path / "b.cir", technology="breadboard")
    assert "C_io_coupling" not in (tmp_path / "i.cir").read_text()
    assert "C_io_coupling" in (tmp_path / "b.cir").read_text()
    # an expanded circuit is no longer a ladder, so layout falls back
    assert built["layout"] == "netlabel"


def test_as_built_asc_reproduces_the_simulated_circuit(tmp_path):
    """Same union-find check as for ideal circuits, applied to the
    expanded one: the schematic must describe what ngspice ran."""
    from spice_agent.export import as_built_circuit

    built = as_built_circuit(_circuit(RC_LOWPASS), "breadboard")
    _, layout = to_asc(built)
    assert _renetlist(layout) == _expected_netlist(built)


@needs_ltspice
@pytest.mark.ltspice
def test_ltspice_matches_ngspice_on_the_as_built_circuit(tmp_path):
    """The cross-check that matters most for parasitics: two independent
    simulators on the expanded netlist must agree."""
    from spice_agent.simulation import simulate_get_full_sweep

    wide = dict(AC, fstop="100meg")
    design = dict(RC_LOWPASS, analysis=wide)
    cir = tmp_path / "ab.cir"
    export(design, cir_path=cir, technology="breadboard")

    lt = parse_ascii_raw(ltspice_run(cir))
    ng = simulate_get_full_sweep(design, technology="breadboard")
    assert len(lt) == len(ng)
    diff = (lt["gain_db"] - ng["gain_db"]).abs()
    mask = ng["gain_db"] > -60
    assert diff[mask].max() < 0.05, f"max |dB diff| = {diff[mask].max():.3f}"


@needs_ltspice
@pytest.mark.ltspice
@pytest.mark.parametrize("name", list(CASES))
def test_ltspice_netlister_agrees(name, tmp_path):
    cj, _ = CASES[name]
    c = _circuit(cj)
    asc = tmp_path / f"{name}.asc"
    export(dict(cj, analysis=AC), asc_path=asc)
    net = ltspice_netlist(asc)
    assert _parse_ltspice_net(net) == _expected_netlist(c)
    assert ".ac dec 100 1 1meg" in net


@needs_ltspice
@pytest.mark.ltspice
@pytest.mark.parametrize("name", ["rc_lowpass", "rlc_bandpass", "rlc_notch", "twin_t", "cascaded_rc"])
def test_ltspice_simulation_matches_ngspice(name, tmp_path):
    from spice_agent.simulation import simulate_get_full_sweep

    cj, _ = CASES[name]
    asc = tmp_path / f"{name}.asc"
    export(dict(cj, analysis=AC), asc_path=asc)
    lt = parse_ascii_raw(ltspice_run(asc))
    ng = simulate_get_full_sweep(dict(cj, analysis=AC))

    assert len(lt) == len(ng)
    assert lt["freq"].to_numpy() == pytest.approx(ng["freq"].to_numpy(), rel=1e-6)
    # Same circuit, two simulators: magnitudes must agree closely except
    # deep inside a notch, where tiny numeric differences blow up in dB.
    diff = (lt["gain_db"] - ng["gain_db"]).abs()
    mask = ng["gain_db"] > -60
    assert diff[mask].max() < 0.05, f"max |dB diff| = {diff[mask].max():.3f}"


# ---------------------------------------------------------------------
# 3. Agent CLI
# ---------------------------------------------------------------------

def test_agent_cli_exports_on_pass(tmp_path, monkeypatch):
    import spice_agent.agent.cli as agent_cli
    from spice_agent.simulation import DesignSpec

    fake = FakeGeminiClient([RC_LOWPASS])
    real_loop = agent_cli.run_agent_loop

    def fake_loop(spec, **kw):
        return real_loop(spec, client=fake, retry_policy=NO_WAIT, verbose=False)

    monkeypatch.setattr(agent_cli, "run_agent_loop", fake_loop)
    asc, cir = tmp_path / "d.asc", tmp_path / "d.cir"
    rc = agent_cli.main(["--type", "lowpass", "--target", "1590",
                         "--asc", str(asc), "--cir", str(cir)])
    assert rc == 0
    assert asc.exists() and cir.exists()
    assert "SYMATTR InstName R1" in asc.read_text()


def test_agent_cli_no_export_on_fail(tmp_path, monkeypatch):
    import spice_agent.agent.cli as agent_cli

    fake = FakeGeminiClient(["I give up."])
    real_loop = agent_cli.run_agent_loop
    monkeypatch.setattr(
        agent_cli, "run_agent_loop",
        lambda spec, **kw: real_loop(spec, client=fake, retry_policy=NO_WAIT, verbose=False),
    )
    asc = tmp_path / "d.asc"
    rc = agent_cli.main(["--type", "lowpass", "--target", "1590", "--asc", str(asc)])
    assert rc == 1
    assert not asc.exists()
