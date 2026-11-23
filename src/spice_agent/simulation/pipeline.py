"""
The end-to-end call the agent makes: circuit JSON in, verdict out.

compile -> simulate -> measure -> judge, with the netlist and its output
living in a temporary directory that is cleaned up afterwards. These two
functions are the only ones a caller normally needs.

For a failed design on a non-ideal technology, simulate_and_verify also
attaches a parasitics diagnosis (see diagnosis.py): a handful of extra
local simulations that say whether ideal parts would have passed and which
parasitic is costing the attenuation. It never changes the verdict.

A notch is a singular metric: on the log grid an ideal zero falls between
two samples and its depth reads as whatever those samples happen to show
(31 dB for a notch that is infinitely deep). For a notch spec every sweep -
the verdict's, each diagnosis variant's, the plot's - is therefore refined
around its minimum (refine_notch) before it is judged.
"""

import dataclasses
import os
import tempfile

import numpy as np
import pandas as pd

from spice_agent.circuit import get_profile
from spice_agent.circuit.model import Circuit, op_path_for
from spice_agent.simulation.availability import check_availability
from spice_agent.simulation.bias import check_bias, check_supply_budget
from spice_agent.simulation.raw import read_operating_point
from spice_agent.simulation.diagnosis import diagnose as diagnose_parasitics
from spice_agent.simulation.ngspice import (
    SimulationError,
    compute_ac_metrics,
    run_ngspice_ac,
)
from spice_agent.simulation.spec import DesignSpec
from spice_agent.simulation.verify import check_filter_spec


# Notch refinement: a pass re-sweeps linearly between the two samples
# either side of the current minimum. One pass of 5001 points shrinks the
# spacing ~2500x, which reads an ideal series-RLC zero as ~100 dB (the
# coarse grid showed 31 dB); an ngspice call costs its startup, not its
# points, so one dense pass is cheaper than several sparse ones. Further
# passes (if raised) stop once one deepens the notch by < STOP_DB.
NOTCH_REFINE_PASSES = 1
NOTCH_REFINE_POINTS = 5001
NOTCH_REFINE_STOP_DB = 0.1


def refine_notch(df: pd.DataFrame, run, lo: float, hi: float) -> pd.DataFrame:
    """
    `df` with extra points around its minimum inside [lo, hi]. `run(f_a,
    f_b, points)` simulates a linear sweep and returns its metrics frame.
    The result is the coarse sweep and every fine pass merged, sorted by
    frequency, so everything downstream reads it like any other sweep.
    """
    for _ in range(NOTCH_REFINE_PASSES):
        freq = df["freq"].to_numpy()
        inside = np.where((freq >= lo) & (freq <= hi))[0]
        if len(inside) < 2:
            break
        i = int(inside[np.argmin(df["gain_db"].to_numpy()[inside])])
        f_a, f_b = freq[max(i - 1, 0)], freq[min(i + 1, len(freq) - 1)]
        if not f_b > f_a:
            break
        before = float(df["gain_db"].iloc[i])
        fine = run(f_a, f_b, NOTCH_REFINE_POINTS)
        df = (pd.concat([df, fine], ignore_index=True)
              .drop_duplicates(subset="freq").sort_values("freq", ignore_index=True))
        if before - float(fine["gain_db"].min()) < NOTCH_REFINE_STOP_DB:
            break
    return df


def _lin_sweep(circuit: Circuit, f_a: float, f_b: float, points: int) -> Circuit:
    return dataclasses.replace(circuit, analysis=dataclasses.replace(
        circuit.analysis, sweep="lin", points=points,
        fstart=f"{f_a:.12g}", fstop=f"{f_b:.12g}"))


def _notch_window(spec: DesignSpec | None):
    return spec.judge_window() if spec is not None and spec.filter_type == "notch" else None


def simulate_get_full_sweep(circuit_json: dict, technology: str = "ideal", *,
                            source_ohm: float = 0.0,
                            load_ohm: float | None = None,
                            supply_pos: float | None = None,
                            supply_neg: float | None = None,
                            notch_window: tuple[float, float] | None = None) -> pd.DataFrame:
    """
    Runs the simulation and returns the full AC sweep with computed
    metrics (freq, gain_db, gain_linear, phase_deg) - useful for
    plotting a Bode plot, unlike simulate_and_verify() which only
    returns the pass/fail summary. `source_ohm` / `load_ohm` are the
    spec's terminations (none by default). `notch_window` (lo, hi) refines
    the sweep around its minimum there, as the verdict does for a notch.
    """
    return _sweep(Circuit.from_dict(circuit_json), technology,
                  source_ohm=source_ohm, load_ohm=load_ohm,
                  supply_pos=supply_pos, supply_neg=supply_neg,
                  notch_window=notch_window)


def _sweep(circuit: Circuit, technology, *, source_ohm: float = 0.0,
           load_ohm: float | None = None, supply_pos: float | None = None,
           supply_neg: float | None = None, ideal_ids=(),
           notch_window: tuple[float, float] | None = None) -> pd.DataFrame:
    """Simulate an already-built Circuit; `technology` is a name or profile,
    `ideal_ids` keeps those components free of parasitics, `notch_window`
    refines the sweep around its minimum there (see refine_notch)."""
    terms = {"source_ohm": source_ohm, "load_ohm": load_ohm,
             "supply_pos": supply_pos, "supply_neg": supply_neg}
    df = _run_sweep(circuit, technology, ideal_ids=ideal_ids, **terms)
    if notch_window is not None:
        df = refine_notch(df, lambda a, b, n: _run_sweep(
            _lin_sweep(circuit, a, b, n), technology, ideal_ids=ideal_ids, **terms),
            *notch_window)
    return df


def _run_sweep(circuit: Circuit, technology, *, source_ohm: float = 0.0,
               load_ohm: float | None = None, supply_pos: float | None = None,
               supply_neg: float | None = None, ideal_ids=()) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as tmpdir:
        netlist_path = os.path.join(tmpdir, "circuit.cir")
        output_path = os.path.join(tmpdir, "output.txt")
        netlist_str = circuit.to_netlist(output_path=output_path, technology=technology,
                                         source_ohm=source_ohm, load_ohm=load_ohm,
                                         supply_pos=supply_pos, supply_neg=supply_neg,
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
    terms = {"source_ohm": spec.source_ohm, "load_ohm": spec.load_ohm,
             "supply_pos": spec.supply_pos_v, "supply_neg": spec.supply_neg_v}

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
        op = None
        if circuit.active_parts and os.path.exists(op_path_for(output_path)):
            op = read_operating_point(op_path_for(output_path))
            if capture is not None:
                capture["op"] = op

    metrics_df = compute_ac_metrics(raw_df)
    notch_window = _notch_window(spec)
    if notch_window is not None:
        try:
            metrics_df = refine_notch(metrics_df, lambda a, b, n: _run_sweep(
                _lin_sweep(circuit, a, b, n), spec.technology, **terms), *notch_window)
        except SimulationError:
            pass            # the coarse sweep stands; a refinement must not hide it
    if capture is not None:
        capture["sweep"] = metrics_df
    result = check_filter_spec(metrics_df, spec)
    result["simulation_failed"] = False
    if circuit.active_parts:
        bias = check_bias(circuit.terminated(spec.source_ohm, spec.load_ohm,
                                             spec.supply_pos_v, spec.supply_neg_v),
                          op, spec.supply_pos_v, spec.supply_neg_v,
                          ideal=get_profile(spec.technology).is_ideal)
        result["bias"] = bias
        if not bias["ok"]:
            result["passed"] = False
            result["message"] = ("BIAS PROBLEM - " + bias["message"] + ". "
                                 + result["message"])
    if spec.max_supply_current_a is not None:
        if circuit.active_parts:
            supply = check_supply_budget(
                circuit, op, spec, ideal=get_profile(spec.technology).is_ideal)
        else:                                   # passive: draws nothing
            supply = {"ok": True, "pos_a": 0.0, "neg_a": 0.0, "per_part": [],
                      "limit_a": spec.max_supply_current_a,
                      "margin_a": spec.max_supply_current_a, "message": ""}
        result["supply"] = supply
        if not supply["ok"]:
            result["passed"] = False
            result["message"] = "OVER BUDGET - " + supply["message"] + ". " + result["message"]
    availability = check_availability(circuit, spec)
    result["availability"] = availability
    if not availability["ok"]:
        result["passed"] = False
        result["message"] = ("NOT BUILDABLE - " + availability["message"] + ". "
                             + result["message"])
    if level != "off":
        try:
            report = diagnose_parasitics(
                circuit, spec, result,
                lambda tech, ideal_ids=(): _sweep(circuit, tech, ideal_ids=ideal_ids,
                                                  notch_window=notch_window, **terms),
                level=level)
        except SimulationError:
            report = None      # an extra run failing must not hide the verdict
        if report is not None:
            result["parasitics"] = report
            result["message"] = result["message"] + " Diagnosis: " + report["summary"]
    return result
