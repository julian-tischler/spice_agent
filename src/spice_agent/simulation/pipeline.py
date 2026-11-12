"""
The end-to-end call the agent makes: circuit JSON in, verdict out.

compile -> simulate -> measure -> judge, with the netlist and its output
living in a temporary directory that is cleaned up afterwards. These two
functions are the only ones a caller normally needs.
"""

import os
import tempfile

import pandas as pd

from spice_agent.circuit.model import Circuit
from spice_agent.simulation.ngspice import (
    SimulationError,
    compute_ac_metrics,
    run_ngspice_ac,
)
from spice_agent.simulation.spec import DesignSpec
from spice_agent.simulation.verify import check_filter_spec


def simulate_get_full_sweep(circuit_json: dict, technology: str = "ideal") -> pd.DataFrame:
    """
    Runs the simulation and returns the full AC sweep with computed
    metrics (freq, gain_db, gain_linear, phase_deg) - useful for
    plotting a Bode plot, unlike simulate_and_verify() which only
    returns the pass/fail summary.
    """
    circuit = Circuit.from_dict(circuit_json)

    with tempfile.TemporaryDirectory() as tmpdir:
        netlist_path = os.path.join(tmpdir, "circuit.cir")
        output_path = os.path.join(tmpdir, "output.txt")
        netlist_str = circuit.to_netlist(output_path=output_path, technology=technology)
        raw_df = run_ngspice_ac(netlist_str, netlist_path, output_path)

    return compute_ac_metrics(raw_df)


def simulate_and_verify(
    circuit_json: dict,
    spec: DesignSpec | None = None,
    *,
    target_cutoff_hz: float | None = None,
    tolerance_pct: float = 10.0,
    capture: dict | None = None,
) -> dict:
    """
    The full pipeline in one call: this is the function the agent loop
    will invoke on each iteration.

    Pass a DesignSpec for any filter type. The keyword form
    simulate_and_verify(circuit, target_cutoff_hz=..., tolerance_pct=...)
    is kept for existing low-pass callers.

    `capture`, if given, is filled with the artifacts of this simulation
    (netlist, ngspice output, measured sweep) for run logging.
    """
    if spec is None:
        if target_cutoff_hz is None:
            raise TypeError("simulate_and_verify() needs a DesignSpec or target_cutoff_hz=")
        spec = DesignSpec("lowpass", target_cutoff_hz, tolerance_pct)

    circuit = Circuit.from_dict(circuit_json)

    with tempfile.TemporaryDirectory() as tmpdir:
        netlist_path = os.path.join(tmpdir, "circuit.cir")
        output_path = os.path.join(tmpdir, "output.txt")
        netlist_str = circuit.to_netlist(output_path=output_path,
                                         technology=spec.technology)
        if capture is not None:
            capture["netlist"] = netlist_str
            if spec.technology != "ideal":
                capture["netlist_ideal"] = circuit.to_netlist(output_path=output_path,
                                                              technology="ideal")

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
    return result
