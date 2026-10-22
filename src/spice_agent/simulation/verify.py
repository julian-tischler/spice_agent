"""
Turning a simulated sweep into a pass/fail verdict.

check_filter_spec() is the single place that decides whether a design
meets its spec. Its return value is also the feedback the model sees, so
the messages are written to say what to change, not merely what failed:
a shortfall at a stopband edge means the ORDER is too low, while the same
shortfall deeper in the band means something is resonating, and those
call for opposite fixes.

Every result value is a plain Python float or bool, never a numpy scalar,
because the dict is JSON-serialised into a tool response.

See spec.py for the requirement model this checks against.
"""

import math

import pandas as pd

from spice_agent.simulation import metrics
from spice_agent.simulation.spec import DesignSpec


# How far below the passband the response must be at the far end of the
# sweep to count as behaving like the requested filter type at all. Stops
# a flat divider or a mis-wired circuit passing on a lucky -3dB crossing.
MIN_STOPBAND_ATTENUATION_DB = 10.0


def _pct_error(measured: float, target: float) -> float:
    return 100 * (measured - target) / target


def check_filter_spec(df: pd.DataFrame, spec: DesignSpec) -> dict:
    """
    Check simulated results against a DesignSpec. Returns a structured,
    JSON-serializable result dict (plain Python floats/bools, no numpy
    scalars) - this is what gets turned into feedback text for the LLM.

    Always present: passed, filter_type, target_hz, tolerance_pct, message.
    Additional measured fields depend on filter_type.
    """
    result = {
        "passed": False,
        "filter_type": spec.filter_type,
        "target_hz": float(spec.target_hz),
        "tolerance_pct": float(spec.tolerance_pct),
    }
    tol = spec.tolerance_pct
    f_lo, f_hi = float(df["freq"].iloc[0]), float(df["freq"].iloc[-1])
    g_lo, g_hi = float(df["gain_db"].iloc[0]), float(df["gain_db"].iloc[-1])

    # ---- lowpass / highpass: one -3dB crossing ----------------------
    if spec.filter_type in ("lowpass", "highpass"):
        if spec.filter_type == "lowpass":
            cutoff = metrics.lowpass_cutoff(df)
            passband_db, far_end_db = g_lo, g_hi
            wrong_way = g_hi > g_lo + 3.0
        else:
            cutoff = metrics.highpass_cutoff(df)
            passband_db, far_end_db = g_hi, g_lo
            wrong_way = g_lo > g_hi + 3.0

        result.update(
            cutoff_hz=None if cutoff is None else float(cutoff),
            passband_gain_db=passband_db,
            far_end_gain_db=far_end_db,
        )
        if wrong_way:
            result["message"] = (
                f"Response rises with frequency - this looks like a "
                f"{'highpass' if spec.filter_type == 'lowpass' else 'lowpass'}, "
                f"not a {spec.filter_type}. Check the topology (which "
                f"element is in series vs shunt to ground)."
            )
            return result
        if cutoff is None or passband_db - far_end_db < MIN_STOPBAND_ATTENUATION_DB:
            result["message"] = (
                f"No usable -3dB crossing in the swept range "
                f"({f_lo:g}–{f_hi:g} Hz): gain is {passband_db:.1f} dB in the "
                f"passband and {far_end_db:.1f} dB at the far end of the sweep. "
                f"The cutoff may be outside the simulated band, or the circuit "
                f"isn't attenuating enough to be a {spec.filter_type}."
            )
            return result

        err = _pct_error(cutoff, spec.target_hz)
        result["error_pct"] = float(err)
        ok = abs(err) <= tol
        msgs = [
            f"cutoff {cutoff:.1f} Hz vs target {spec.target_hz:.1f} Hz ({err:+.1f}%"
            + ("" if ok else f", outside +/-{tol:g}%") + ")"
        ]

        if spec.stopband_hz is not None:
            # The band runs from the edge nearest the passband out to
            # stopband_end_hz, or to the end of the sweep if unspecified.
            band_end = spec.stopband_end_hz
            if band_end is None:
                band_end = f_hi if spec.filter_type == "lowpass" else f_lo
            outside = [e for e in (spec.stopband_hz, band_end)
                       if not (f_lo <= e <= f_hi)]
            if outside:
                result["message"] = (
                    f"Stopband edge {outside[0]:g} Hz lies outside the swept range "
                    f"({f_lo:g}-{f_hi:g} Hz), so the attenuation cannot be verified."
                )
                return result

            worst_atten, worst_hz = metrics.worst_attenuation(
                df, passband_db, spec.stopband_hz, band_end)
            atten_at_edge = passband_db - metrics.gain_at(df, spec.stopband_hz)
            ok_stop = worst_atten >= spec.stopband_atten_db
            ok = ok and ok_stop

            band_lo, band_hi = sorted((spec.stopband_hz, band_end))
            result.update(
                stopband_hz=float(spec.stopband_hz),
                stopband_end_hz=float(band_end),
                stopband_required_atten_db=float(spec.stopband_atten_db),
                stopband_atten_db=float(worst_atten),
                stopband_worst_hz=float(worst_hz),
                stopband_gain_db=float(passband_db - worst_atten),
                stopband_edge_atten_db=float(atten_at_edge),
            )
            msgs.append(
                f"worst attenuation across {band_lo:g}-{band_hi:g} Hz is "
                f"{worst_atten:.1f} dB at {worst_hz:.0f} Hz "
                f"(need >= {spec.stopband_atten_db:g} dB)"
            )
            if not ok_stop:
                shortfall = spec.stopband_atten_db - worst_atten
                # Two very different causes, two very different fixes.
                edge_limited = abs(worst_hz - spec.stopband_hz) <= 0.02 * spec.stopband_hz
                if edge_limited:
                    decades = abs(math.log10(spec.stopband_hz / spec.target_hz))
                    msgs.append(
                        f"short by {shortfall:.1f} dB at the stopband edge - no choice "
                        f"of component values fixes this, the filter ORDER must "
                        f"increase (each order adds ~20 dB/decade, and there "
                        f"{'is' if abs(decades - 1) < 0.05 else 'are'} "
                        f"{decades:.2f} decade(s) between cutoff and stopband)"
                    )
                else:
                    msgs.append(
                        f"short by {shortfall:.1f} dB, and NOT at the band edge: "
                        f"attenuation is {atten_at_edge:.1f} dB at {spec.stopband_hz:g} Hz "
                        f"but degrades to {worst_atten:.1f} dB at {worst_hz:.0f} Hz, so "
                        f"the response recovers inside the stopband instead of staying "
                        f"down - something is resonating; increasing the order will not "
                        f"help on its own"
                    )

        result["passed"] = bool(ok)
        result["message"] = (f"Simulated {spec.filter_type}: " + "; ".join(msgs) + ". "
                             + ("PASS" if ok else "FAIL."))
        return result

    # ---- bandpass: peak location (+ optional bandwidth) --------------
    if spec.filter_type == "bandpass":
        f_peak, g_peak = metrics.peak(df)
        bw = metrics.bandpass_bandwidth(df)
        result.update(
            center_hz=f_peak,
            peak_gain_db=g_peak,
            bandwidth_hz=None if bw is None else float(bw),
            gain_at_fstart_db=g_lo,
            gain_at_fstop_db=g_hi,
        )
        if (g_peak - g_lo < MIN_STOPBAND_ATTENUATION_DB
                or g_peak - g_hi < MIN_STOPBAND_ATTENUATION_DB):
            result["message"] = (
                f"Response is not band-pass shaped: peak {g_peak:.1f} dB at "
                f"{f_peak:.1f} Hz but the sweep edges are only at {g_lo:.1f} dB "
                f"({f_lo:g} Hz) and {g_hi:.1f} dB ({f_hi:g} Hz). Both sides of "
                f"the peak need to roll off."
            )
            return result

        err = _pct_error(f_peak, spec.target_hz)
        result["error_pct"] = float(err)
        msgs = [f"Simulated centre {f_peak:.1f} Hz vs target {spec.target_hz:.1f} Hz ({err:+.1f}%)"]
        ok = abs(err) <= tol
        if spec.bandwidth_hz is not None:
            if bw is None:
                ok = False
                msgs.append("-3dB bandwidth could not be measured (edges outside sweep)")
            else:
                bw_err = _pct_error(bw, spec.bandwidth_hz)
                result["bandwidth_error_pct"] = float(bw_err)
                ok = ok and abs(bw_err) <= tol
                msgs.append(
                    f"bandwidth {bw:.1f} Hz vs target {spec.bandwidth_hz:.1f} Hz ({bw_err:+.1f}%)"
                )
        elif bw is not None:
            msgs.append(f"bandwidth {bw:.1f} Hz")
        result["passed"] = bool(ok)
        result["message"] = "; ".join(msgs) + ". " + (
            "PASS" if ok else f"FAIL - outside +/-{tol:g}% tolerance."
        )
        return result

    # ---- notch: minimum location + depth -----------------------------
    f_notch, g_notch = metrics.notch(df)
    passband_db = max(g_lo, g_hi)
    depth = passband_db - g_notch
    result.update(
        notch_hz=f_notch,
        notch_gain_db=g_notch,
        notch_depth_db=float(depth),
        passband_gain_db=float(passband_db),
    )
    if abs(g_lo - g_hi) > 3.0:
        result["message"] = (
            f"Gain differs between the low end ({g_lo:.1f} dB) and high end "
            f"({g_hi:.1f} dB) of the sweep - a notch should pass both sides "
            f"equally. This looks more like a lowpass/highpass."
        )
        return result
    if depth < spec.min_notch_depth_db:
        result["message"] = (
            f"Deepest point is only {depth:.1f} dB below passband (at "
            f"{f_notch:.1f} Hz); spec needs at least {spec.min_notch_depth_db:g} dB."
        )
        return result

    err = _pct_error(f_notch, spec.target_hz)
    result["error_pct"] = float(err)
    result["passed"] = bool(abs(err) <= tol)
    result["message"] = (
        f"Simulated notch at {f_notch:.1f} Hz vs target {spec.target_hz:.1f} Hz "
        f"({err:+.1f}%), depth {depth:.1f} dB. "
        + ("PASS" if result["passed"] else f"FAIL - outside +/-{tol:g}% tolerance.")
    )
    return result


def check_lowpass_spec(
    df: pd.DataFrame,
    target_cutoff_hz: float,
    tolerance_pct: float = 10.0,
) -> dict:
    """Backward-compatible wrapper: low-pass only."""
    result = check_filter_spec(
        df, DesignSpec("lowpass", target_cutoff_hz, tolerance_pct)
    )
    result["target_cutoff_hz"] = result["target_hz"]
    return result
