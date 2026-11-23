"""
Why a design failed when built: attribution of a failure to parasitics.

check_filter_spec() judges one sweep and can only say *that* a requirement
was missed. When the circuit is simulated with a non-ideal technology, the
harness can do better, because it can re-simulate the same design with
parts of the parasitic model switched off:

  * with ideal parts          - would the design itself have passed?
  * per parasitic group       - which KIND of parasitic costs the margin?
  * per component ("full")    - which of the model's PARTS is to blame?
  * self-resonances           - which parts resonate inside the judged band?

Each of those is a few extra local ngspice runs and no model tokens. The
point is to hand the model something it can act on: it cannot edit
"winding capacitance", but it can change L1.

What is diagnosed is the PRIMARY failed check - the first failing one in
CHECK_PRIORITY - scored as a margin: positive means the requirement is met
by that much, negative means it is missed by that much (dB, or % for
frequency checks). Every variant is re-judged with check_filter_spec, so
the diagnosis can never disagree with the verdict about what passing is.
The stopband is the one exception: it is scored directly on the sweep, so
a variant that no longer even looks like a filter (the -3 dB crossing
vanishes) still gets a number instead of dropping out.

Parasitic groups are measured TWO ways, because parasitics interact and
either measure alone gets real circuits wrong:

  isolation  the group on its own, every other group off: how much margin
             does it cost against the parasitic-free circuit? (damage)
  removal    every other group on, this one off: how much does that win
             back? (removal_gain)

Isolation misses damage that only exists in combination. On a C-L-C ladder
(live run, 2026-11-12) every group alone cost ~0 dB, yet removing the
inductor winding capacitance won back 21 dB and removing the lead
inductance 16 dB. Removal misses damage that another parasitic masks. On
the textbook L-C-L ladder, removing the input-output coupling made the
stopband slightly WORSE (19.5 -> 17.5 dB), yet on its own the coupling takes
it from 67.5 to 1.5 dB. A group is ranked by the larger of the two.

Per-component attribution re-simulates the as-built circuit with ONE
component (or one group of identical parts in parallel) made ideal, every
other part keeping its parasitics. It is the expensive part - one run per
component group - so it only runs at level "full" (the default); "basic"
skips it for quicker results and "off" skips the diagnosis altogether.

The diagnosis says what is wrong and where. It never tries new values:
choosing them is the model's job, and a harness that searched for them
would be measuring itself instead of the model.

diagnose() only runs for a non-ideal technology and a failed verdict.
"""

import dataclasses
import math
import time

from spice_agent.circuit import Circuit, get_profile
from spice_agent.hints import render
from spice_agent.simulation import metrics
from spice_agent.simulation.spec import DesignSpec
from spice_agent.simulation.verify import (
    MIN_STOPBAND_ATTENUATION_DB,
    _fmt_hz,
    check_filter_spec,
)
from spice_agent.units import parse_spice_value

LEVELS = ("full", "basic", "off")

# Parasitic groups that are simulated one at a time, with the advice that
# applies when that group turns out to be the limiting one.
#
# Losses are a group too. They were once left out on the grounds that they
# only damp, but in a low-impedance design they are the dominant error: in
# a live breadboard run a 210 uH inductor's 4.35 ohm winding resistance, in
# a filter built around 10 ohm, pulled a 10 kHz cutoff down to 6 kHz. The
# per-component check named L1; this group says WHY. The inductor's
# parallel loss resistance (Q at self-resonance) stays with the winding
# capacitance - it only shapes that resonance.


def _advice(key: str) -> str:
    """A group's explanation (a fact) followed by its remedy (a rule of thumb),
    both from the hint catalogue."""
    return (render(f"parasitic.{key}.why") + ". "
            + render(f"parasitic.{key}.remedy", capital=True))


ABLATIONS = {
    "input-output stray coupling": (("io_coupling_c",), _advice("coupling")),
    "inductor self-resonance (winding capacitance)": (("inductor_winding_c",),
                                                      _advice("winding_c")),
    "losses (inductor winding resistance, capacitor ESR, contact resistance)": (
        ("inductor_dcr_per_sqrt_h", "capacitor_esr", "contact_resistance"),
        _advice("losses")),
    "capacitor ESL": (("capacitor_esl",), _advice("esl")),
    "lead / interconnect inductance": (("interconnect_l_per_terminal", "resistor_series_l"),
                                       _advice("leads")),
    "stray node capacitance": (("interconnect_node_c", "resistor_shunt_c"),
                               _advice("node_c")),
}

# The check diagnosed when several fail: the first failing one in this order.
CHECK_PRIORITY = ("availability", "supply current", "stopband", "band shape", "notch symmetry", "peaking",
                  "notch depth", "passband gain", "bandwidth", "frequency")

LOSSES = "losses (inductor winding resistance, capacitor ESR, contact resistance)"

# Below this a parasitic group or a component is not worth naming.
MIN_RELEVANT_DB = 1.0
# Stands in for "the response broke" (no measurable check) in margins.
BROKEN = -999.0


# ---------------------------------------------------------------------
# Margins: one number per judged requirement
# ---------------------------------------------------------------------

def margins(result: dict, spec: DesignSpec) -> dict[str, tuple[float, str]]:
    """
    How far each judged requirement in `result` is from its limit, as
    {check: (margin, unit)}. Positive = met by that much, negative = missed
    by that much. Only checks the result actually measured are present.
    """
    m: dict[str, tuple[float, str]] = {}
    tol = spec.tolerance_pct
    if result.get("error_pct") is not None:
        m["frequency"] = (tol - abs(result["error_pct"]), "%")
    if result.get("bandwidth_error_pct") is not None:
        m["bandwidth"] = (tol - abs(result["bandwidth_error_pct"]), "%")
    if result.get("stopband_atten_db") is not None:
        m["stopband"] = (result["stopband_atten_db"] - spec.stopband_atten_db, "dB")
    if result.get("availability") is not None:
        m["availability"] = (1.0 if result["availability"]["ok"] else -1.0, "")
    if (result.get("supply") or {}).get("margin_a") is not None:
        m["supply current"] = (result["supply"]["margin_a"] * 1e3, "mA")
    if result.get("passband_gain_error_db") is not None:
        m["passband gain"] = (spec.gain_tolerance_db - abs(result["passband_gain_error_db"]), "dB")
    if result.get("passband_peaking_db") is not None and spec.max_peaking_db is not None:
        m["peaking"] = (spec.max_peaking_db - result["passband_peaking_db"], "dB")
    if result.get("rejection_at_judge_lo_db") is not None:
        m["band shape"] = (min(result["rejection_at_judge_lo_db"],
                               result["rejection_at_judge_hi_db"])
                           - MIN_STOPBAND_ATTENUATION_DB, "dB")
    if spec.filter_type == "notch" and result.get("notch_depth_db") is not None:
        m["notch depth"] = (result["notch_depth_db"] - spec.min_notch_depth_db, "dB")
        m["notch symmetry"] = (3.0 - abs(result["gain_at_judge_lo_db"]
                                         - result["gain_at_judge_hi_db"]), "dB")
    return m


def _describe(check: str, margin: float, spec: DesignSpec) -> str:
    """A margin in the requirement's own words."""
    if margin <= BROKEN:
        return "no measurable response (the filter shape breaks)"
    tol = spec.tolerance_pct
    return {
        "stopband": lambda: f"{margin + spec.stopband_atten_db:.1f} dB worst attenuation",
        "peaking": lambda: f"{spec.max_peaking_db - margin:+.1f} dB peaking",
        "band shape": lambda: f"{margin + MIN_STOPBAND_ATTENUATION_DB:.1f} dB edge rejection",
        "notch symmetry": lambda: f"{3.0 - margin:.1f} dB between the passband edges",
        "notch depth": lambda: f"{margin + spec.min_notch_depth_db:.1f} dB notch depth",
        "bandwidth": lambda: f"bandwidth {tol - margin:.1f}% off target",
        "frequency": lambda: f"{tol - margin:.1f}% off target",
        "passband gain": lambda: f"{spec.gain_tolerance_db - margin:.2f} dB from the gain target",
    }[check]()


def _scorer(check: str, spec: DesignSpec, result: dict):
    """score(df) -> (margin of `check`, passed overall)."""
    def judged(df):
        r = check_filter_spec(df, spec)
        return margins(r, spec).get(check, (BROKEN, ""))[0], bool(r.get("passed"))

    if check != "stopband":
        return judged
    lo, hi = result["stopband_hz"], result["stopband_end_hz"]
    w_lo, w_hi = result["judge_lo_hz"], result["judge_hi_hz"]

    def stopband(df):
        passband = metrics.gain_at(df, w_lo if spec.filter_type == "lowpass" else w_hi)
        atten = metrics.worst_attenuation(df, passband, lo, hi)[0]
        return atten - spec.stopband_atten_db, judged(df)[1]
    return stopband


# ---------------------------------------------------------------------
# Self-resonances
# ---------------------------------------------------------------------

def _components_report(circuit: Circuit, profile, spec: DesignSpec,
                       result: dict) -> list[dict]:
    if result.get("stopband_end_hz") is not None:
        band, where = sorted((result["stopband_hz"], result["stopband_end_hz"])), "stopband"
    else:
        band, where = [result["judge_lo_hz"], result["judge_hi_hz"]], "judged band"
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
        if c.type == "inductor" and profile.inductor_dcr(value) > 0:
            entry["series_resistance_ohm"] = float(profile.inductor_dcr(value))
        if where == "stopband" and srf < band[0] and spec.filter_type == "lowpass":
            entry["note"] = render("diag.srf_below_stopband", above=above)
        elif band[0] <= srf <= band[1]:
            entry["note"] = render("diag.srf_inside", where=where, above=above)
        out.append(entry)
    return out


def component_groups(circuit: Circuit) -> list[dict]:
    """
    The model's parts as the per-component check treats them: identical
    parts in parallel (same type, same two nodes, same value) form one
    group, so twenty paralleled capacitors cost one simulation, not twenty.
    """
    groups: dict[tuple, dict] = {}
    for c in circuit.components:
        if c.type not in ("resistor", "capacitor", "inductor", "opamp"):
            continue
        # an op-amp's pins are not interchangeable: keep their order
        key = (c.type, tuple(c.nodes) if c.type == "opamp" else frozenset(c.nodes),
               c.value)
        g = groups.setdefault(key, {"ids": [], "value": c.value, "nodes": list(c.nodes),
                                    "type": c.type})
        g["ids"].append(c.id)
    out = []
    for g in groups.values():
        n = len(g["ids"])
        g["label"] = (f"{g['ids'][0]} ({g['value']})" if n == 1 else
                      f"{g['ids'][0]}..{g['ids'][-1]} ({n}x {g['value']}, "
                      f"{'-'.join(g['nodes'])})")
        out.append(g)
    return out


# ---------------------------------------------------------------------
# The diagnosis
# ---------------------------------------------------------------------

def diagnose(circuit: Circuit, spec: DesignSpec, result: dict, sweep,
             level: str = "full") -> dict | None:
    """
    Attribute a failed non-ideal result to its causes.

    `sweep(technology, ideal_ids=())` simulates `circuit` (with the spec's
    terminations) and returns its metrics DataFrame; it is passed in so this
    module needs no knowledge of temp files or ngspice. `level` is "full",
    "basic" (no per-component check) or "off".

    Returns a JSON-able dict, or None when there is nothing to attribute.
    """
    if level not in LEVELS:
        raise ValueError(f"diagnosis level must be one of {LEVELS}, not {level!r}")
    profile = get_profile(spec.technology)
    if level == "off" or result.get("passed") or result.get("simulation_failed"):
        return None
    if profile.is_ideal:
        return None          # ideal parts throughout: nothing to attribute
    t0 = time.monotonic()

    ideal_df = sweep("ideal")
    ideal = check_filter_spec(ideal_df, spec)
    report: dict = {
        "technology": profile.name,
        "level": level,
        "ideal_parts_passed": bool(ideal.get("passed")),
        "has_active_parts": bool(circuit.active_parts),
    }
    for key in ("cutoff_hz", "stopband_atten_db", "center_hz", "notch_hz",
                "notch_depth_db", "passband_peaking_db"):
        if ideal.get(key) is not None:
            report[f"ideal_{key}"] = ideal[key]
    report["components"] = _components_report(circuit, profile, spec, result)

    # Split the failed checks: those ideal parts would ALSO fail are design
    # problems (the verdict already says how to fix them; blaming parasitics
    # for them would mislead), the rest are caused by construction. Only a
    # construction-caused check is attributed - the first in CHECK_PRIORITY.
    have = margins(result, spec)
    ideal_have = margins(ideal, spec)
    failing = [c for c in CHECK_PRIORITY if c in have and have[c][0] < 0]
    design = [c for c in failing if ideal_have.get(c, (BROKEN, ""))[0] < 0]
    built = [c for c in failing if c not in design]
    # Checks ideal parts would miss but this design passes as built: the
    # parasitics happen to help there. Worth knowing before changing values.
    ideal_only = [c for c in CHECK_PRIORITY
                  if c not in failing and ideal_have.get(c, (0.0, ""))[0] < 0]
    report.update(failed_checks=failing, design_failures=design,
                  parasitic_failures=built, ideal_only_failures=ideal_only)
    if built:
        check = built[0]
        unit = have[check][1]
        score = _scorer(check, spec, result)
        base = have[check][0]
        report.update(check=check, unit=unit, margin=float(base))
        report["ideal_margin"] = float(score(ideal_df)[0])

        groups = {name: (fields, advice) for name, (fields, advice) in ABLATIONS.items()
                  if any(getattr(profile, f) for f in fields)}
        # Isolation baseline: every parasitic PATH off, losses kept - real
        # parts are never lossless, and dropping losses from the baseline
        # makes other groups' damage vanish into interactions with them (on
        # the L-C-L ladder the winding capacitance's 6 dB did). Losses
        # themselves are charged against a separate, fully lossless run.
        paths = [f for name, (fields, _) in groups.items() if name != LOSSES
                 for f in fields]
        loss_fields = list(groups.get(LOSSES, ((), ""))[0])

        def only(keep):
            return dataclasses.replace(profile, **{f: 0.0 for f in paths
                                                   if f not in keep})

        clean = score(sweep(only(())))[0]          # parasitic paths off, losses kept
        report["parasitic_free_margin"] = float(clean)
        lossless = None
        if loss_fields:
            lossless = score(sweep(dataclasses.replace(
                profile, **{f: 0.0 for f in paths + loss_fields})))[0]
            report["lossless_margin"] = float(lossless)
        limiting = []
        for name, (fields, advice) in groups.items():
            if name == LOSSES:
                baseline, alone = lossless, clean      # losses on, paths off
            else:
                baseline, alone = clean, score(sweep(only(fields)))[0]
            without = score(sweep(dataclasses.replace(profile,
                                                      **{f: 0.0 for f in fields})))[0]
            damage, gain = baseline - alone, without - base
            limiting.append({"parasitic": name,
                             "baseline_margin": float(baseline),
                             "margin_with_only_it": float(alone),
                             "damage": float(damage),
                             "margin_without_it": float(without),
                             "removal_gain": float(gain),
                             "impact": float(max(damage, gain)),
                             "advice": advice})
        limiting.sort(key=lambda d: d["impact"], reverse=True)
        report["limiting_parasitics"] = [d for d in limiting
                                         if d["impact"] >= MIN_RELEVANT_DB]

        if level == "full":
            per_part = []
            for g in component_groups(circuit):
                m, passed = score(sweep(spec.technology, ideal_ids=g["ids"]))
                per_part.append({"ids": g["ids"], "label": g["label"],
                                 "part": g["value"] if g["type"] == "opamp" else None,
                                 "margin_if_ideal": float(m),
                                 "recovered": float(m - base),
                                 "passes_if_ideal": passed})
            per_part.sort(key=lambda d: (d["passes_if_ideal"], d["recovered"]),
                          reverse=True)
            report["per_component"] = per_part

    report["duration_s"] = round(time.monotonic() - t0, 3)
    report["summary"] = _summary(report, spec)
    return report


def grade_advice(part: str, spec: DesignSpec) -> str:
    """
    What to do when an op-amp grade is the limit - naming only grades that
    are faster, rated for THESE rails and within the supply-current budget,
    since the model can change neither the rails nor the budget. "Choose a
    faster grade" with none available sent a live run (2026-11-23) off to a
    passive design it could not finish.
    """
    from spice_agent.models import OPAMPS, SELECTABLE_OPAMPS

    current = OPAMPS[part]
    total = spec.supply_pos_v - spec.supply_neg_v
    budget = spec.max_supply_current_a
    options = [OPAMPS[n] for n in SELECTABLE_OPAMPS
               if OPAMPS[n].gbw_hz > current.gbw_hz
               and OPAMPS[n].supply_min_v <= total <= OPAMPS[n].supply_max_v
               and (budget is None or OPAMPS[n].quiescent_a <= budget)]
    rails = f"{spec.supply_pos_v:g} V / {spec.supply_neg_v:g} V"
    within = "" if budget is None else f" within {budget * 1e3:g} mA"
    if options:
        names = ", ".join(f"{m.name} ({m.gbw_hz / 1e6:g} MHz)" for m in options)
        return (render("diag.grade.faster", rails=rails, within=within, names=names)
                + "; " + render("diag.grade.less_q_otherwise"))
    return (render("diag.grade.none", part=part, gbw=f"{current.gbw_hz / 1e6:g}",
                   rails=rails, within=within)
            + "; " + render("diag.grade.less_q"))


def _impact_text(d: dict, report: dict, spec: DesignSpec) -> str:
    c = report["check"]
    if d["damage"] >= d["removal_gain"]:
        return (f"on its own it takes the {c} from "
                f"{_describe(c, d['baseline_margin'], spec)} (no parasitics) "
                f"to {_describe(c, d['margin_with_only_it'], spec)}")
    return (f"removing it would bring the {c} to "
            f"{_describe(c, d['margin_without_it'], spec)} (+{d['removal_gain']:.1f} "
            f"{report['unit']}); it does its damage in combination with the other "
            f"parasitics")


def _impact_short(d: dict, unit: str) -> str:
    if d["damage"] >= d["removal_gain"]:
        return f"-{d['damage']:.1f} {unit} on its own"
    return f"+{d['removal_gain']:.1f} {unit} if removed"


def _summary(report: dict, spec: DesignSpec) -> str:
    bits = []
    check = report.get("check")
    design, built = report.get("design_failures", []), report.get("parasitic_failures", [])
    if report["ideal_parts_passed"] and report.get("has_active_parts"):
        bits.append(render("diag.ideal_pass_active"))
    elif report["ideal_parts_passed"]:
        bits.append(render("diag.ideal_pass"))
    elif design and not built:
        bits.append(render("diag.design_only", checks=", ".join(design)))
    elif design:
        bits.append(render("diag.mixed", design=", ".join(design), built=", ".join(built)))
    elif built:
        only = report.get("ideal_only_failures")
        bits.append(render("diag.built_only", built=", ".join(built),
                           ideal_only=render("diag.ideal_only", checks=", ".join(only))
                           if only else ""))
    if check:
        bits.append(f"Diagnosed (parasitic) check: {check}, currently "
                    f"{_describe(check, report['margin'], spec)}")
    parts = report.get("per_component")
    if parts:
        fixers = [p for p in parts if p["passes_if_ideal"]]
        helpful = [p for p in parts if p["recovered"] >= MIN_RELEVANT_DB]
        if fixers:
            verb = (" alone would make the design PASS - that is the part to change"
                    if len(fixers) == 1 else " would each alone make it PASS")
            bits.append(render("diag.per_component.fixer",
                               labels=", ".join(p["label"] for p in fixers), verb=verb)
                        + "".join(f"; {grade_advice(p['part'], spec)}"
                                  for p in fixers if p.get("part")))
        elif helpful:
            bits.append(render("diag.per_component.partial", items=", ".join(
                f"{p['label']} +{p['recovered']:.1f} {report['unit']}"
                for p in helpful[:3])))
        else:
            bits.append(render("diag.per_component.none", limit=f"{MIN_RELEVANT_DB:g}",
                               unit=report["unit"]))
    top = report.get("limiting_parasitics") or []
    if top:
        t = top[0]
        bits.append(f"Biggest single cause: {t['parasitic']} - "
                    f"{_impact_text(t, report, spec)}; {t['advice']}")
        if t["parasitic"].startswith("losses"):
            dcr = [c for c in report["components"] if "series_resistance_ohm" in c]
            if dcr:
                bits.append("Winding resistance as built: " + ", ".join(
                    f"{c['id']} ({c['value']}) {c['series_resistance_ohm']:.2f} ohm"
                    for c in dcr))
        if len(top) > 1:
            bits.append("Also costly: " + ", ".join(
                f"{d['parasitic']} ({_impact_short(d, report['unit'])})" for d in top[1:3]))
    flagged = [c for c in report["components"] if "note" in c]
    if flagged:
        bits.append("; ".join(
            f"{c['id']} ({c['value']}) self-resonates at "
            f"{_fmt_hz(c['self_resonance_hz'])}, {c['note']}" for c in flagged))
    return ". ".join(bits) + "."
