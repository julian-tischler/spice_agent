"""
The end-to-end call the agent makes: circuit JSON in, verdict out.

compile -> simulate -> measure -> judge, with the netlist and its output
living in a temporary directory that is cleaned up afterwards. These two
functions are the only ones a caller normally needs.

For a failed design on a non-ideal technology, simulate_and_verify also
attaches a parasitics diagnosis (see diagnosis.py): a handful of extra
local simulations that say whether ideal parts would have passed and which
parasitic is costing the attenuation. It never changes the verdict.
"""

import os
import tempfile

import pandas as pd

from spice_agent.circuit.model import Circuit
from spice_agent.simulation.diagnosis import diagnose as diagnose_parasitics
from spice_agent.simulation.ngspice import (
    SimulationError,
    compute_ac_metrics,
    run_ngspice_ac,
)
from spice_agent.simulation.spec import DesignSpec
from spice_agent.simulation.verify import check_filter_spec


def simulate_get_full_sweep(circuit_json: dict, technology: str = "ideal", *,
                            source_ohm: float = 0.0,
                            load_ohm: float | None = None) -> pd.DataFrame:
    """
    Runs the simulation and returns the full AC sweep with computed
    metrics (freq, gain_db, gain_linear, phase_deg) - useful for
    plotting a Bode plot, unlike simulate_and_verify() which only
    returns the pass/fail summary. `source_ohm` / `load_ohm` are the
    spec's terminations (none by default).
    """
    return _sweep(Circuit.from_dict(circuit_json), technology,
                  source_ohm=source_ohm, load_ohm=load_ohm)


def _sweep(circuit: Circuit, technology, *, source_ohm: float = 0.0,
           load_ohm: float | None = None, ideal_ids=()) -> pd.DataFrame:
    """Simulate an already-built Circuit; `technology` is a name or profile,
    `ideal_ids` keeps those components free of parasitics."""
    with tempfile.TemporaryDirectory() as tmpdir:
        netlist_path = os.path.join(tmpdir, "circuit.cir")
        output_path = os.path.join(tmpdir, "output.txt")
        netlist_str = circuit.to_netlist(output_path=output_path, technology=technology,
                                         source_ohm=source_ohm, load_ohm=load_ohm,
                                         ideal_ids=ideal_ids)
        raw_df = run_ngspice_ac(netlist_str, netlist_path, output_path)
    return compute_ac_metrics(raw_df)


def _diagnosis_level(diagnose) -> str:
    if diagnose is True:
        return "full"
    if diagnose in (False, None):
        return "off"
    return diagnose


def simulate_and_verify(
    circuit_json: dict,
    spec: DesignSpec | None = None,
    *,
    target_cutoff_hz: float | None = None,
    tolerance_pct: float = 10.0,
    capture: dict | None = None,
    diagnose: bool | str = "full",
) -> dict:
    """
    The full pipeline in one call: this is the function the agent loop
    will invoke on each iteration.

    Pass a DesignSpec for any filter type. The keyword form
    simulate_and_verify(circuit, target_cutoff_hz=..., tolerance_pct=...)
    is kept for existing low-pass callers.

    The circuit is simulated between the spec's source and load resistances
    when it sets them (none by default, so nothing is ever added that the
    spec did not ask for).

    `capture`, if given, is filled with the artifacts of this simulation
    (netlist, ngspice output, measured sweep) for run logging.

    `diagnose` sets the parasitics diagnosis of a failed non-ideal design:
    "full" (default; includes the per-component check), "basic" (quicker,
    no per-component check) or "off" (True / False map to full / off). It
    costs extra local simulations, never model tokens, and never changes
    the verdict.
    """
    if spec is None:
        if target_cutoff_hz is None:
            raise TypeError("simulate_and_verify() needs a DesignSpec or target_cutoff_hz=")
        spec = DesignSpec("lowpass", target_cutoff_hz, tolerance_pct)
    level = _diagnosis_level(diagnose)
    terms = {"source_ohm": spec.source_ohm, "load_ohm": spec.load_ohm}

    circuit = Circuit.from_dict(circuit_json)

    with tempfile.TemporaryDirectory() as tmpdir:
        netlist_path = os.path.join(tmpdir, "circuit.cir")
        output_path = os.path.join(tmpdir, "output.txt")
        netlist_str = circuit.to_netlist(output_path=output_path,
                                         technology=spec.technology, **terms)
        if capture is not None:
            capture["netlist"] = netlist_str
            if spec.technology != "ideal":
                capture["netlist_ideal"] = circuit.to_netlist(output_path=output_path,
                                                              technology="ideal", **terms)

        try:
            raw_df = run_ngspice_ac(netlist_str, netlist_path, output_path, capture)
        except SimulationError as e:
            return {
                "passed": False,
                "simulation_failed": True,
                "message": str(e),
                "stderr": e.stderr,
            }

    metrics_df = compute_ac_metrics(raw_df)
    if capture is not None:
        capture["sweep"] = metrics_df
    result = check_filter_spec(metrics_df, spec)
    result["simulation_failed"] = False
    if level != "off":
        try:
            report = diagnose_parasitics(
                circuit, spec, result,
                lambda tech, ideal_ids=(): _sweep(circuit, tech, ideal_ids=ideal_ids, **terms),
                level=level)
        except SimulationError:
            report = None      # an extra run failing must not hide the verdict
        if report is not None:
            result["parasitics"] = report
            result["message"] = result["message"] + " Diagnosis: " + report["summary"]
    return result
