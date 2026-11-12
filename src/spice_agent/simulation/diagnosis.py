"""
Why a design failed when built: attribution of a failure to parasitics.

check_filter_spec() judges one sweep and can only say *that* the stopband
recovers. When the circuit is simulated with a non-ideal technology, the
harness can do better, because it can re-simulate the same design with
parts of the parasitic model switched off:

  * with ideal parts      - would the design itself have passed?
  * one parasitic at a time - which parasitic is actually costing the dB?
  * per component         - which inductor or capacitor self-resonates
                            inside the band that has to stay down?

Each of those is a few extra local ngspice runs and no model tokens, and
it turns "something is resonating" into "L1 self-resonates at 310 kHz;
input-output stray coupling on its own takes the stopband from 67 to 2 dB",
which the model can act on.

Each parasitic group is measured TWO ways, because parasitics interact and
either measure alone gets real circuits wrong:

  isolation  the group on its own, every other group off: how much does it
             cost against the parasitic-free circuit? (damage_db)
  removal    every other group on, this one off: how much does that win
             back? (removal_gain_db)

Isolation misses damage that only exists in combination. On a C-L-C ladder
(live run, 2026-11-12) every group alone cost ~0 dB, yet removing the
inductor winding capacitance won back 21 dB and removing the lead
inductance 16 dB: the leads spoil the shunt capacitors and the winding
capacitance bypasses the inductor, and only together do they open a path.
Removal misses damage that another parasitic masks. On the textbook L-C-L
ladder, removing the input-output coupling made the stopband slightly WORSE
(19.5 -> 17.5 dB), yet on its own the coupling takes it from 67.5 to 1.5 dB,
because that ladder leaves 'out' with no shunt path at all.

A group is ranked by the larger of the two (impact_db), and the summary
says which measure it was, since "costs 66 dB on its own" and "removing it
wins back 21 dB" call for different readings.

Scores come straight from the sweep (worst attenuation over the result's
stopband), not from check_filter_spec, so a variant that no longer even
looks like a filter still gets a number instead of dropping out.

diagnose() only runs for a non-ideal technology and a failed verdict.
"""

import dataclasses
import math

from spice_agent.circuit import Circuit, get_profile
from spice_agent.simulation import metrics
from spice_agent.simulation.spec import DesignSpec
from spice_agent.simulation.verify import _fmt_hz, check_filter_spec
from spice_agent.units import parse_spice_value


# Parasitic groups that are simulated one at a time, with the advice that
# applies when that group turns out to be the limiting one. Losses
# (DCR, ESR, contact resistance) are left out: they damp, they do not
# create the high-frequency paths that break a stopband.
ABLATIONS = {
    "input-output stray coupling": (
        ("io_coupling_c",),
        "signal bypasses the whole filter through stray capacitance from 'in' "
        "to 'out', so more poles cannot help; only a lower high-frequency "
        "impedance at 'out' does: end the filter in a shunt capacitor at 'out' "
        "(a ladder ending in a series inductor leaves 'out' with nothing but "
        "the load to ground), ideally several in parallel, since N in parallel "
        "divide their lead inductance by N"),
    "inductor self-resonance (winding capacitance)": (
        ("inductor_winding_c",),
        "an inductor becomes a capacitor above its self-resonance, and smaller "
        "inductors resonate higher; lower the impedance level (smaller L with "
        "proportionally larger C and smaller R keeps the same cutoff), or split "
        "one inductor into several smaller ones in series"),
    "capacitor ESL": (
        ("capacitor_esl",),
        "a capacitor becomes an inductor above its self-resonance; use several "
        "smaller capacitors in parallel instead of one large one"),
    "lead / interconnect inductance": (
        ("interconnect_l_per_terminal", "resistor_series_l"),
        "every connection adds series inductance, which spoils shunt "
        "capacitors at high frequency; parallel shunt paths divide it"),
    "stray node capacitance": (
        ("interconnect_node_c", "resistor_shunt_c"),
        "stray capacitance across parts gives the signal a high-frequency "
        "path around them"),
}

# Below this cost a parasitic is not worth naming.
MIN_RELEVANT_DAMAGE_DB = 1.0


def _scorer(spec: DesignSpec, result: dict):
    """Worst attenuation over the result's stopband, measured directly on
    a sweep. None when the result has no stopband to judge."""
    if result.get("stopband_end_hz") is None:
        return None
    lo, hi = result["stopband_hz"], result["stopband_end_hz"]

    def score(df) -> float:
        passband = float(df["gain_db"].iloc[0 if spec.filter_type == "lowpass" else -1])
        return metrics.worst_attenuation(df, passband, lo, hi)[0]
    return score


def _components_report(circuit: Circuit, profile, spec: DesignSpec,
                       result: dict) -> list[dict]:
    band = None
    if result.get("stopband_end_hz") is not None:
        band = sorted((result["stopband_hz"], result["stopband_end_hz"]))
    lead = 2 * profile.interconnect_l_per_terminal
    out = []
    for c in circuit.components:
        if c.type not in ("inductor", "capacitor"):
            continue
        try:
            value = parse_spice_value(c.value)
        except (ValueError, TypeError):
            continue
        if c.type == "inductor":
            srf = profile.inductor_srf_hz(value)
            above = "acts as a capacitor"
        else:
            esl = profile.capacitor_esl + lead
            srf = (1 / (2 * math.pi * math.sqrt(value * esl))
                   if esl > 0 and value > 0 else None)
            above = "acts as an inductor (ESL + leads)"
        if srf is None:
            continue
        entry = {"id": c.id, "value": c.value, "self_resonance_hz": float(srf)}
        if band is not None:
            if srf < band[0] and spec.filter_type == "lowpass":
                entry["note"] = f"below the stopband: {above} across the whole stopband"
            elif band[0] <= srf <= band[1]:
                entry["note"] = f"inside the stopband: {above} above it"
        out.append(entry)
    return out


def diagnose(circuit: Circuit, spec: DesignSpec, result: dict, sweep) -> dict | None:
    """
    Attribute a failed non-ideal result to its causes. `sweep(technology)`
    simulates `circuit` and returns its metrics DataFrame; it is passed in
    so this module needs no knowledge of temp files or ngspice.

    Returns a JSON-able dict, or None when there is nothing to attribute.
    """
    profile = get_profile(spec.technology)
    if profile.is_ideal or result.get("passed") or result.get("simulation_failed"):
        return None

    ideal = check_filter_spec(sweep("ideal"), spec)
    report: dict = {
        "technology": profile.name,
        "ideal_parts_passed": bool(ideal.get("passed")),
    }
    for key in ("cutoff_hz", "stopband_atten_db", "center_hz", "notch_hz"):
        if ideal.get(key) is not None:
            report[f"ideal_{key}"] = ideal[key]

    report["components"] = _components_report(circuit, profile, spec, result)

    score = _scorer(spec, result)
    base = None
    if score is not None:
        groups = {name: (fields, advice) for name, (fields, advice) in ABLATIONS.items()
                  if any(getattr(profile, f) for f in fields)}
        all_fields = [f for fields, _ in groups.values() for f in fields]

        def only(keep):
            return dataclasses.replace(profile, **{f: 0.0 for f in all_fields
                                                   if f not in keep})

        base = result["stopband_atten_db"]
        clean = score(sweep(only(())))           # every group off, losses kept
        report["parasitic_free_atten_db"] = float(clean)
        limiting = []
        for name, (fields, advice) in groups.items():
            alone = score(sweep(only(fields)))
            without = score(sweep(dataclasses.replace(profile, **{f: 0.0 for f in fields})))
            damage, gain = clean - alone, without - base
            limiting.append({"parasitic": name,
                             "atten_db_with_only_it": float(alone),
                             "damage_db": float(damage),
                             "atten_db_without_it": float(without),
                             "removal_gain_db": float(gain),
                             "impact_db": float(max(damage, gain)),
                             "advice": advice})
        limiting.sort(key=lambda d: d["impact_db"], reverse=True)
        report["limiting_parasitics"] = [d for d in limiting
                                         if d["impact_db"] >= MIN_RELEVANT_DAMAGE_DB]

    report["summary"] = _summary(report)
    return report


def _impact_text(d: dict, report: dict) -> str:
    if d["damage_db"] >= d["removal_gain_db"]:
        return (f"on its own it takes the worst stopband attenuation from "
                f"{report['parasitic_free_atten_db']:.1f} dB (no parasitics) down to "
                f"{d['atten_db_with_only_it']:.1f} dB")
    return (f"removing it would raise the worst stopband attenuation to "
            f"{d['atten_db_without_it']:.1f} dB (+{d['removal_gain_db']:.1f} dB); it does "
            f"its damage in combination with the other parasitics")


def _impact_short(d: dict) -> str:
    if d["damage_db"] >= d["removal_gain_db"]:
        return f"-{d['damage_db']:.1f} dB on its own"
    return f"+{d['removal_gain_db']:.1f} dB if removed"


def _summary(report: dict) -> str:
    bits = []
    if report["ideal_parts_passed"]:
        bits.append("With ideal parts this exact design would PASS, so the failure "
                    "is caused by the construction parasitics, not the topology")
    else:
        bits.append("It would FAIL even with ideal parts, so fix the design itself "
                    "first (see above)")
    top = report.get("limiting_parasitics") or []
    if top:
        t = top[0]
        bits.append(f"Biggest single cause: {t['parasitic']} - "
                    f"{_impact_text(t, report)}; {t['advice']}")
        if len(top) > 1:
            bits.append("Also costly: " + ", ".join(
                f"{d['parasitic']} ({_impact_short(d)})" for d in top[1:3]))
    flagged = [c for c in report["components"] if "note" in c]
    if flagged:
        bits.append("; ".join(
            f"{c['id']} ({c['value']}) self-resonates at "
            f"{_fmt_hz(c['self_resonance_hz'])}, {c['note']}" for c in flagged))
    return ". ".join(bits) + "."
