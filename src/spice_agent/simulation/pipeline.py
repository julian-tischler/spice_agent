"""
The end-to-end call the agent makes: circuit JSON in, verdict out.

compile -> simulate -> measure -> judge, with the netlist and its output
living in a temporary directory that is cleaned up afterwards. These two
functions are the only ones a caller normally needs.

For a failed design on a non-ideal technology, simulate_and_verify also
attaches a parasitics diagnosis (see diagnosis.py): a handful of extra
local simulations that say whether ideal parts would have passed and which
parasitic is costing the attenuation. It never changes the verdict.

Some verdicts rest on a single extremum, and on the log grid an extremum
falls between two samples. An ideal notch zero then reads as whatever those
samples show (31 dB for one that is infinitely deep), and a band-pass centre
as the nearest grid point (+/-1.2 % at 100 points per decade, against
tolerances of a few percent). For a notch or band-pass spec every sweep -
the verdict's, each diagnosis variant's, the plot's - is therefore refined
around its minimum or peak (refine_extremum) before it is judged; a
band-pass across both -3 dB edges as well (refine_band), which are
otherwise interpolated linearly across a 2.3 % grid step.
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


# Refinement: a pass re-sweeps linearly between the two samples either side
# of the current extremum. One pass of 5001 points shrinks the spacing
# ~2500x: an ideal series-RLC zero reads ~100 dB deep (the coarse grid
# showed 31 dB), and a band-pass centre is located to ~0.001 % (the grid
# alone quantised it to +/-1.2 %). An ngspice call costs its startup, not
# its points, so one dense pass is cheaper than several sparse ones.
# Further passes (if raised) stop once one moves the extremum's gain by
# less than REFINE_STOP_DB.
REFINE_PASSES = 1
REFINE_POINTS = 5001
REFINE_STOP_DB = 0.1


def refine_extremum(df: pd.DataFrame, run, lo: float, hi: float,
                    find: str = "min") -> pd.DataFrame:
    """
    `df` with extra points around its minimum (find="min", a notch) or
    maximum ("max", a band-pass peak) inside [lo, hi]. `run(f_a, f_b,
    points)` simulates a linear sweep and returns its metrics frame. The
    result is the coarse sweep and every fine pass merged, sorted by
    frequency, so everything downstream reads it like any other sweep.
    """
    pick = np.argmin if find == "min" else np.argmax
    for _ in range(REFINE_PASSES):
        freq = df["freq"].to_numpy()
        inside = np.where((freq >= lo) & (freq <= hi))[0]
        if len(inside) < 2:
            break
        i = int(inside[pick(df["gain_db"].to_numpy()[inside])])
        f_a, f_b = freq[max(i - 1, 0)], freq[min(i + 1, len(freq) - 1)]
        if not f_b > f_a:
            break
        before = float(df["gain_db"].iloc[i])
        fine = run(f_a, f_b, REFINE_POINTS)
        df = _merge(df, fine)
        after = float(fine["gain_db"].min() if find == "min" else fine["gain_db"].max())
        if abs(after - before) < REFINE_STOP_DB:
            break
    return df


def _merge(df: pd.DataFrame, fine: pd.DataFrame) -> pd.DataFrame:
    return (pd.concat([df, fine], ignore_index=True)
            .drop_duplicates(subset="freq").sort_values("freq", ignore_index=True))


# A band-pass is refined in ONE linear sweep from just below its lower -3 dB
# edge to just above its upper one: that span holds the peak and both edges,
# and one long sweep costs less than three short ones (0.2 s for 20001
# points vs 0.12 s each for 5001). 20001 points across a Q = 10 band is
# ~0.3 Hz spacing.
BAND_REFINE_POINTS = 20001


def _band_edge_brackets(df: pd.DataFrame, lo: float, hi: float, drop_db: float):
    """((f_a, f_b) around the lower edge or None, the same for the upper)."""
    freq = df["freq"].to_numpy()
    inside = np.where((freq >= lo) & (freq <= hi))[0]
    if len(inside) < 3:
        return None, None
    gain = df["gain_db"].to_numpy()
    i_peak = int(inside[np.argmax(gain[inside])])
    target = gain[i_peak] - drop_db
    below_lo = [i for i in inside if i < i_peak and gain[i] <= target]
    below_hi = [i for i in inside if i > i_peak and gain[i] <= target]
    lower = (freq[below_lo[-1]], freq[below_lo[-1] + 1]) if below_lo else None
    upper = (freq[below_hi[0] - 1], freq[below_hi[0]]) if below_hi else None
    return lower, upper


def refine_band(df: pd.DataFrame, run, lo: float, hi: float,
                drop_db: float = 3.0) -> pd.DataFrame:
    """
    `df` refined around a band-pass peak AND both edges where the gain falls
    `drop_db` below it - the points metrics.bandpass_bandwidth would
    otherwise interpolate across a 2.3 % grid step. With both edges inside
    [lo, hi], one sweep spans them (it holds the peak); otherwise the peak
    is refined on its own (refine_extremum) and whichever edge exists gets
    a short sweep of its own.
    """
    lower, upper = _band_edge_brackets(df, lo, hi, drop_db)
    if lower and upper:
        return _merge(df, run(lower[0], upper[1], BAND_REFINE_POINTS))
    df = refine_extremum(df, run, lo, hi, "max")
    lower, upper = _band_edge_brackets(df, lo, hi, drop_db)
    for bracket in (lower, upper):
        if bracket and bracket[1] > bracket[0]:
            df = _merge(df, run(bracket[0], bracket[1], REFINE_POINTS))
    return df


def refine_sweep(df: pd.DataFrame, run, refine: tuple[float, float, str]) -> pd.DataFrame:
    """Everything refinement_for() asked for: a notch's minimum, or a
    band-pass's peak and -3 dB edges."""
    lo, hi, find = refine
    if find == "max":
        return refine_band(df, run, lo, hi)
    return refine_extremum(df, run, lo, hi, find)


def _lin_sweep(circuit: Circuit, f_a: float, f_b: float, points: int) -> Circuit:
    return dataclasses.replace(circuit, analysis=dataclasses.replace(
        circuit.analysis, sweep="lin", points=points,
        fstart=f"{f_a:.12g}", fstop=f"{f_b:.12g}"))


def refinement_for(spec: DesignSpec | None) -> tuple[float, float, str] | None:
    """(lo, hi, find) for the specs whose verdict rests on one extremum - a
    notch's minimum, a band-pass's peak - else None."""
    if spec is None or spec.filter_type not in ("notch", "bandpass"):
        return None
    lo, hi = spec.judge_window()
    return lo, hi, ("min" if spec.filter_type == "notch" else "max")


def simulate_get_full_sweep(circuit_json: dict, technology: str = "ideal", *,
                            source_ohm: float = 0.0,
                            load_ohm: float | None = None,
                            supply_pos: float | None = None,
                            supply_neg: float | None = None,
                            refine: tuple[float, float, str] | None = None) -> pd.DataFrame:
    """
    Runs the simulation and returns the full AC sweep with computed
    metrics (freq, gain_db, gain_linear, phase_deg) - useful for
    plotting a Bode plot, unlike simulate_and_verify() which only
    returns the pass/fail summary. `source_ohm` / `load_ohm` are the
    spec's terminations (none by default). `refine` (lo, hi, find) refines
    the sweep around that extremum, as the verdict does (refinement_for).
    """
    return _sweep(Circuit.from_dict(circuit_json), technology,
                  source_ohm=source_ohm, load_ohm=load_ohm,
                  supply_pos=supply_pos, supply_neg=supply_neg, refine=refine)


def _sweep(circuit: Circuit, technology, *, source_ohm: float = 0.0,
           load_ohm: float | None = None, supply_pos: float | None = None,
           supply_neg: float | None = None, ideal_ids=(),
           refine: tuple[float, float, str] | None = None) -> pd.DataFrame:
    """Simulate an already-built Circuit; `technology` is a name or profile,
    `ideal_ids` keeps those components free of parasitics, `refine` (lo,
    hi, find) refines the sweep around that extremum (refine_extremum)."""
    terms = {"source_ohm": source_ohm, "load_ohm": load_ohm,
             "supply_pos": supply_pos, "supply_neg": supply_neg}
    df = _run_sweep(circuit, technology, ideal_ids=ideal_ids, **terms)
    if refine is not None:
        df = refine_sweep(df, lambda a, b, n: _run_sweep(
            _lin_sweep(circuit, a, b, n), technology, ideal_ids=ideal_ids, **terms),
            refine)
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
    spec: DesignSpec,
    *,
    capture: dict | None = None,
    diagnose: bool | str = "full",
) -> dict:
    """
    The full pipeline in one call: this is the function the agent loop
    will invoke on each iteration.

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
    refine = refinement_for(spec)
    if refine is not None:
        try:
            metrics_df = refine_sweep(metrics_df, lambda a, b, n: _run_sweep(
                _lin_sweep(circuit, a, b, n), spec.technology, **terms), refine)
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
                                                  refine=refine, **terms),
                level=level)
        except SimulationError:
            report = None      # an extra run failing must not hide the verdict
        if report is not None:
            result["parasitics"] = report
            result["message"] = result["message"] + " Diagnosis: " + report["summary"]
    return result
