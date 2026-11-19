"""
Turning a simulated sweep into a pass/fail verdict.

check_filter_spec() is the single place that decides whether a design
meets its spec. Its return value is also the feedback the model sees, so
the messages are written to say what to change, not merely what failed:
a shortfall at a stopband edge means the ORDER is too low, while a
shortfall deeper in the band means something is resonating or leaking
around the filter. Those call for different fixes, and a design can have
both at once, so each is diagnosed on its own and both are reported.

Everything is judged inside the spec's judged window (default: two decades
either side of the target); what the response does outside it is reported,
never failed. The passband may not peak more than spec.max_peaking_db above
its own level. No failed check ends the verdict early, so every measurement
reaches the model whichever check failed.

A frequency miss comes with the exact correction: scaling every L and C
by the same factor k moves the whole response by 1/k without changing
its shape, so k = measured / target puts the feature on target (exactly
for ideal parts, closely with parasitics).

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


# Margin asked for on top of the requirement when recommending an order.
# A design sitting exactly on the asymptote fails on the first parasitic.
ORDER_MARGIN_DB = 3.0
MAX_SUGGESTED_ORDER = 12


def _scale_hint(measured: float, target: float, what: str) -> str:
    """The exact value correction for a frequency miss (see module doc)."""
    k = measured / target
    return (f"to move the {what} onto target, multiply EVERY inductor and capacitor "
            f"value by {k:.3f} and keep the resistors - that shifts the whole "
            f"response by 1/{k:.3f} without changing its shape")


def _butterworth_atten_db(order: int, ratio: float) -> float:
    """Attenuation of an n-th order Butterworth at `ratio` x the cutoff."""
    return 10 * math.log10(1 + ratio ** (2 * order))


def _effective_order(atten_db: float, ratio: float) -> float | None:
    """The Butterworth order that would give `atten_db` at `ratio`."""
    if ratio <= 1 or atten_db <= 0:
        return None
    return math.log10(10 ** (atten_db / 10) - 1) / (2 * math.log10(ratio))


def _order_advice(spec: DesignSpec, atten_at_edge: float) -> tuple[str, dict]:
    """How steep the design is now, and which order would meet the edge."""
    ratio = (spec.stopband_hz / spec.target_hz if spec.filter_type == "lowpass"
             else spec.target_hz / spec.stopband_hz)
    info: dict = {}
    bits = []
    eff = _effective_order(atten_at_edge, ratio)
    if eff is not None:
        info["effective_order"] = round(eff, 2)
        bits.append(f"at the edge it rolls off like a ~{eff:.1f}-order filter")
    need = next((n for n in range(1, MAX_SUGGESTED_ORDER + 1)
                 if _butterworth_atten_db(n, ratio)
                 >= spec.stopband_atten_db + ORDER_MARGIN_DB), None)
    if need is not None:
        info["suggested_order"] = need
        lower = (f" (order {need - 1} gives only "
                 f"{_butterworth_atten_db(need - 1, ratio):.1f} dB)") if need > 1 else ""
        bits.append(f"a Butterworth of order {need} gives "
                    f"{_butterworth_atten_db(need, ratio):.1f} dB there{lower}")
    return "; ".join(bits), info


def _stopband_profile(df: pd.DataFrame, passband_db: float, edge: float,
                      band_end: float, worst_hz: float) -> list[dict]:
    """Attenuation at the edge, at each decade into the band, at the far
    end and at the worst point - enough to see the shape of the band."""
    rising = band_end >= edge
    step = 10.0 if rising else 0.1
    points, f = [edge], edge * step
    while (f < band_end * 0.999) if rising else (f > band_end * 1.001):
        points.append(f)
        f *= step
    points += [band_end, worst_hz]
    seen, out = set(), []
    for f in sorted(points, reverse=not rising):
        key = round(math.log10(f), 3)
        if key in seen:
            continue
        seen.add(key)
        out.append({"hz": float(f),
                    "atten_db": float(passband_db - metrics.gain_at(df, f))})
    return out


def _fmt_db(x: float) -> str:
    """One decimal, but never round a real shortfall down to '0.0'."""
    return f"{x:.1f}" if x >= 0.05 else f"{x:.2f}"


def _fmt_hz(f: float) -> str:
    for scale, unit in ((1e6, "MHz"), (1e3, "kHz")):
        if abs(f) >= scale:
            return f"{f / scale:.3g} {unit}"
    return f"{f:.3g} Hz"


def _window(df: pd.DataFrame, lo: float, hi: float) -> pd.DataFrame:
    """Rows of the sweep inside [lo, hi] (tolerant of float edges)."""
    f = df["freq"]
    return df[(f >= lo * (1 - 1e-9)) & (f <= hi * (1 + 1e-9))].reset_index(drop=True)


def _peaking(df: pd.DataFrame, reference_db: float, f_from: float, f_to: float):
    """Largest rise above `reference_db` between f_from and f_to, and where."""
    lo, hi = sorted((f_from, f_to))
    part = _window(df, lo, hi)
    if part.empty:
        return 0.0, None
    i = int(part["gain_db"].idxmax())
    return float(part["gain_db"].iloc[i] - reference_db), float(part["freq"].iloc[i])


def _peaking_tip(spec: DesignSpec) -> str:
    if spec.terminated:
        return ("check the element values against the fixed source/load resistances - "
                "ladder tables assume the terminations they were computed for")
    return ("nothing damps it: the source is ideal (0 ohm) and 'out' is unloaded, so an "
            "L-C section needs its own damping resistance (series R before it, or "
            "a resistor across the output) or a lower Q")


def _check_peaking(spec: DesignSpec, df: pd.DataFrame, reference_db: float,
                   f_from: float, f_to: float, result: dict, msgs: list) -> bool:
    """Passband peaking check. Records the measurement always; returns ok."""
    peak, peak_hz = _peaking(df, reference_db, f_from, f_to)
    result.update(passband_peaking_db=float(peak),
                  passband_peaking_hz=None if peak_hz is None else float(peak_hz))
    if spec.max_peaking_db is None or peak <= spec.max_peaking_db:
        return True
    result["peaking_excess_db"] = float(peak - spec.max_peaking_db)
    msgs.append(f"passband peaks {peak:+.1f} dB above its level at {_fmt_hz(peak_hz)} "
                f"(limit {spec.max_peaking_db:g} dB) - an undamped resonance; "
                + _peaking_tip(spec))
    return False


def _outside_info(full: pd.DataFrame, spec: DesignSpec, lo: float, hi: float,
                  reference_db: float) -> list[dict]:
    """
    What the response does OUTSIDE the judged window - reported, never
    judged. Only notable behaviour is returned: a region that should pass
    sagging by more than 3 dB, or a region that should block coming back
    up by more than 3 dB above its level at the window edge.
    """
    passes = {"lowpass": ("pass", "block"), "highpass": ("block", "pass"),
              "bandpass": ("block", "block"), "notch": ("pass", "pass")}[spec.filter_type]
    notes = []
    for side, (edge, region) in zip(("below", "above"),
                                    ((lo, full[full["freq"] < lo * (1 - 1e-9)]),
                                     (hi, full[full["freq"] > hi * (1 + 1e-9)]))):
        if region.empty:
            continue
        kind = passes[0] if side == "below" else passes[1]
        edge_db = metrics.gain_at(full, edge)
        if kind == "pass":
            i = region["gain_db"].idxmin()
            g, f = float(region["gain_db"][i]), float(region["freq"][i])
            if g < reference_db - 3.0:
                notes.append({"side": side, "edge_hz": float(edge), "kind": "sags",
                              "gain_db": g, "at_hz": f,
                              "text": f"{side} {_fmt_hz(edge)} the response sags to "
                                      f"{g - reference_db:.1f} dB at {_fmt_hz(f)}"})
        else:
            i = region["gain_db"].idxmax()
            g, f = float(region["gain_db"][i]), float(region["freq"][i])
            if g > edge_db + 3.0:
                notes.append({"side": side, "edge_hz": float(edge), "kind": "rises",
                              "gain_db": g, "at_hz": f,
                              "text": f"{side} {_fmt_hz(edge)} the response comes back up "
                                      f"to {reference_db - g:.1f} dB below passband at "
                                      f"{_fmt_hz(f)}"})
    return notes


def _finish(result: dict, spec: DesignSpec, head: str, msgs: list, ok: bool,
            full: pd.DataFrame, lo: float, hi: float, reference_db: float | None) -> dict:
    result["passed"] = bool(ok)
    text = f"{head}: " + "; ".join(msgs) + ". " + ("PASS" if ok else "FAIL.")
    if reference_db is not None:
        notes = _outside_info(full, spec, lo, hi, reference_db)
        if notes:
            result["outside_window"] = notes
            text += ("." if ok else "") + (f" Not judged (outside the judged {_fmt_hz(lo)} - {_fmt_hz(hi)}, so it "
                     f"cannot fail the design): " + "; ".join(n["text"] for n in notes) + ".")
    result["message"] = text
    return result


def check_filter_spec(df: pd.DataFrame, spec: DesignSpec) -> dict:
    """
    Check simulated results against a DesignSpec. Returns a structured,
    JSON-serializable result dict (plain Python floats/bools, no numpy
    scalars) - this is what gets turned into feedback text for the LLM.

    Everything is judged inside spec.judge_window() - by default two
    decades either side of the target. The response outside it is reported
    (`outside_window`) but cannot fail a design: the user asked for a
    filter at the target, and what it does three decades away is only a
    requirement when they say so.

    Failed checks do not end the check: every measurement (cutoff, centre,
    bandwidth, depth, peaking, stopband) is reported whichever check
    failed, so the model always sees how close each requirement was.

    Always present: passed, filter_type, target_hz, tolerance_pct,
    judge_lo_hz, judge_hi_hz, message.
    """
    full = df
    sweep_lo, sweep_hi = float(full["freq"].iloc[0]), float(full["freq"].iloc[-1])
    lo, hi = spec.judge_window(sweep_lo, sweep_hi)
    df = _window(full, lo, hi)
    result = {
        "passed": False,
        "filter_type": spec.filter_type,
        "target_hz": float(spec.target_hz),
        "tolerance_pct": float(spec.tolerance_pct),
        "judge_lo_hz": float(lo),
        "judge_hi_hz": float(hi),
    }
    tol = spec.tolerance_pct
    g_lo, g_hi = metrics.gain_at(full, lo), metrics.gain_at(full, hi)
    window_txt = f"{_fmt_hz(lo)}-{_fmt_hz(hi)}"

    # ---- lowpass / highpass: one -3dB crossing ----------------------
    if spec.filter_type in ("lowpass", "highpass"):
        if spec.filter_type == "lowpass":
            passband_db, far_end_db = g_lo, g_hi
            cutoff = metrics.lowpass_cutoff(df, reference_db=passband_db)
            wrong_way = g_hi > g_lo + 3.0
        else:
            passband_db, far_end_db = g_hi, g_lo
            cutoff = metrics.highpass_cutoff(df, reference_db=passband_db)
            wrong_way = g_lo > g_hi + 3.0

        result.update(
            cutoff_hz=None if cutoff is None else float(cutoff),
            passband_gain_db=float(passband_db),
            far_end_gain_db=float(far_end_db),
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
                f"No usable -3dB crossing in the judged range ({window_txt}): gain "
                f"is {passband_db:.1f} dB in the passband and {far_end_db:.1f} dB at "
                f"the far edge of that range. The cutoff may be outside it, or the "
                f"circuit isn't attenuating enough to be a {spec.filter_type}."
            )
            return result

        err = _pct_error(cutoff, spec.target_hz)
        result["error_pct"] = float(err)
        ok = abs(err) <= tol
        msgs = [
            f"cutoff {cutoff:.1f} Hz vs target {spec.target_hz:.1f} Hz ({err:+.1f}%"
            + ("" if ok else f", outside +/-{tol:g}%") + ")"
        ]
        if not ok:
            result["frequency_scale_factor"] = float(cutoff / spec.target_hz)
            msgs.append(_scale_hint(cutoff, spec.target_hz, "cutoff"))

        # Peaking lives in the passband: below the cutoff for a lowpass,
        # above it for a highpass.
        pass_edge = lo if spec.filter_type == "lowpass" else hi
        ok = _check_peaking(spec, df, passband_db, pass_edge, cutoff, result, msgs) and ok

        if spec.stopband_hz is not None:
            band_end = spec.stopband_end(sweep_lo, sweep_hi)
            outside = [e for e in (spec.stopband_hz, band_end)
                       if not (sweep_lo * (1 - 1e-9) <= e <= sweep_hi * (1 + 1e-9))]
            if outside:
                result["message"] = (
                    f"Stopband edge {outside[0]:g} Hz lies outside the swept range "
                    f"({sweep_lo:g}-{sweep_hi:g} Hz), so the attenuation cannot be verified."
                )
                return result
            ok = _check_stopband(full, spec, passband_db, band_end, result, msgs) and ok

        return _finish(result, spec, f"Simulated {spec.filter_type}", msgs, ok,
                       full, lo, hi, passband_db)

    # ---- bandpass: peak location (+ optional bandwidth) --------------
    if spec.filter_type == "bandpass":
        f_peak, g_peak = metrics.peak(df)
        bw = metrics.bandpass_bandwidth(df)
        result.update(
            center_hz=f_peak,
            peak_gain_db=g_peak,
            bandwidth_hz=None if bw is None else float(bw),
            gain_at_judge_lo_db=float(g_lo),
            gain_at_judge_hi_db=float(g_hi),
        )
        err = _pct_error(f_peak, spec.target_hz)
        result["error_pct"] = float(err)
        msgs = [f"centre {f_peak:.1f} Hz vs target {spec.target_hz:.1f} Hz ({err:+.1f}%"
                + ("" if abs(err) <= tol else f", outside +/-{tol:g}%") + ")"]
        ok = abs(err) <= tol
        if abs(err) > tol:
            result["frequency_scale_factor"] = float(f_peak / spec.target_hz)
            msgs.append(_scale_hint(f_peak, spec.target_hz, "centre frequency"))
        if spec.bandwidth_hz is not None:
            if bw is None:
                ok = False
                msgs.append("-3dB bandwidth could not be measured (an edge lies outside "
                            f"the judged range {window_txt})")
            else:
                bw_err = _pct_error(bw, spec.bandwidth_hz)
                result["bandwidth_error_pct"] = float(bw_err)
                ok = ok and abs(bw_err) <= tol
                msgs.append(f"bandwidth {bw:.1f} Hz vs target {spec.bandwidth_hz:.1f} Hz "
                            f"({bw_err:+.1f}%" + ("" if abs(bw_err) <= tol
                                                  else f", outside +/-{tol:g}%") + ")")
        elif bw is not None:
            msgs.append(f"bandwidth {bw:.1f} Hz")
        rej_lo, rej_hi = g_peak - g_lo, g_peak - g_hi
        result.update(rejection_at_judge_lo_db=float(rej_lo),
                      rejection_at_judge_hi_db=float(rej_hi))
        if min(rej_lo, rej_hi) < MIN_STOPBAND_ATTENUATION_DB:
            ok = False
            msgs.append(
                f"Response is not band-pass shaped within the judged range: the edges "
                f"are only {rej_lo:.1f} dB ({_fmt_hz(lo)}) and {rej_hi:.1f} dB "
                f"({_fmt_hz(hi)}) below the peak; both sides need to roll off by at "
                f"least {MIN_STOPBAND_ATTENUATION_DB:g} dB")
        return _finish(result, spec, "Simulated bandpass", msgs, ok, full, lo, hi, g_peak)

    # ---- notch: minimum location + depth -----------------------------
    f_notch, g_notch = metrics.notch(df)
    passband_db = max(g_lo, g_hi)
    depth = passband_db - g_notch
    result.update(
        notch_hz=f_notch,
        notch_gain_db=g_notch,
        notch_depth_db=float(depth),
        passband_gain_db=float(passband_db),
        gain_at_judge_lo_db=float(g_lo),
        gain_at_judge_hi_db=float(g_hi),
    )
    err = _pct_error(f_notch, spec.target_hz)
    result["error_pct"] = float(err)
    ok = abs(err) <= tol
    msgs = [f"notch at {f_notch:.1f} Hz vs target {spec.target_hz:.1f} Hz ({err:+.1f}%"
            + ("" if ok else f", outside +/-{tol:g}%") + f"), depth {depth:.1f} dB"]
    if not ok:
        result["frequency_scale_factor"] = float(f_notch / spec.target_hz)
        msgs.append(_scale_hint(f_notch, spec.target_hz, "notch"))
    if depth < spec.min_notch_depth_db:
        ok = False
        msgs.append(f"deepest point is only {depth:.1f} dB below passband; spec needs at "
                    f"least {spec.min_notch_depth_db:g} dB")
    if abs(g_lo - g_hi) > 3.0:
        ok = False
        msgs.append(f"gain differs between the low edge ({g_lo:.1f} dB at {_fmt_hz(lo)}) "
                    f"and the high edge ({g_hi:.1f} dB at {_fmt_hz(hi)}) of the judged "
                    f"range - a notch should pass both sides equally; this looks more "
                    f"like a lowpass/highpass")
    ok = _check_peaking(spec, df, passband_db, lo, hi, result, msgs) and ok
    return _finish(result, spec, "Simulated notch", msgs, ok, full, lo, hi, passband_db)


def _check_stopband(df: pd.DataFrame, spec: DesignSpec, passband_db: float,
                    band_end: float, result: dict, msgs: list) -> bool:
    """The stopband requirement across the whole band; returns ok."""
    worst_atten, worst_hz = metrics.worst_attenuation(
        df, passband_db, spec.stopband_hz, band_end)
    atten_at_edge = passband_db - metrics.gain_at(df, spec.stopband_hz)
    ok_stop = worst_atten >= spec.stopband_atten_db

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
    if ok_stop:
        return True
    req = spec.stopband_atten_db
    result["stopband_shortfall_db"] = float(req - worst_atten)
    profile = _stopband_profile(df, passband_db, spec.stopband_hz, band_end, worst_hz)
    result["stopband_profile"] = profile
    # Two independent causes with different fixes; either, or both, can
    # apply, and both must be fixed to pass.
    #   edge:     too little attenuation right at the edge, so the rolloff
    #             is too shallow - order too low.
    #   interior: the response comes back up inside the band - a resonance
    #             or a path around the filter.
    edge_short = atten_at_edge < req
    interior_short = (abs(worst_hz - spec.stopband_hz) > 0.02 * spec.stopband_hz
                      and worst_atten < atten_at_edge - 1.0)
    if not (edge_short or interior_short):
        edge_short = True      # worst point hugs the edge
    causes = [c for c, on in (("order", edge_short), ("resonance", interior_short)) if on]
    result["stopband_causes"] = causes
    if len(causes) == 2:
        msgs.append("TWO separate stopband problems - both must be "
                    "fixed, fixing one alone will not pass")
    if edge_short:
        advice, info = _order_advice(spec, atten_at_edge)
        result.update(info)
        decades = abs(math.log10(spec.stopband_hz / spec.target_hz))
        msgs.append(
            f"(1) short by {_fmt_db(req - atten_at_edge)} dB at the stopband edge "
            f"({atten_at_edge:.1f} dB at {spec.stopband_hz:g} Hz) - no choice "
            f"of component values fixes this, the filter ORDER must "
            f"increase (each order adds ~20 dB/decade, and there "
            f"{'is' if abs(decades - 1) < 0.05 else 'are'} "
            f"{decades:.2f} decade(s) between cutoff and stopband"
            + (f"; {advice}" if advice else "") + ")"
        )
    if interior_short:
        tail = ("raising the order fixes (1) but not this" if edge_short
                else "increasing the order will not help on its own")
        msgs.append(
            f"({2 if edge_short else 1}) short by {req - worst_atten:.1f} dB, "
            f"and NOT at the band edge: attenuation is {atten_at_edge:.1f} dB "
            f"at {spec.stopband_hz:g} Hz but degrades to {worst_atten:.1f} dB "
            f"at {worst_hz:.0f} Hz, so the response recovers inside the "
            f"stopband instead of staying down - something is resonating "
            f"or signal is leaking around the filter; {tail}"
        )
    msgs.append("attenuation across the band: " + ", ".join(
        f"{_fmt_hz(q['hz'])} {q['atten_db']:.1f} dB" for q in profile))
    return False


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
