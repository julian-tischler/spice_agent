"""
Building the design brief the model receives.

Pure string construction from a DesignSpec - no API calls, no state - so
the exact text sent can be asserted in a test.
"""

import math

from spice_agent.agent.analysis import analysis_for
from spice_agent.circuit import LOAD_ID, SOURCE_ID, SOURCE_NODE, VCC, VEE, get_profile
from spice_agent.models import OPAMPS, SELECTABLE_OPAMPS
from spice_agent.models.series import get_series
from spice_agent.hints import render
from spice_agent.simulation import DesignSpec
from spice_agent.units import format_value


# Topology reminders per filter type, assembled from the hint catalogue
# (spice_agent/hints.py), where each one is documented: when it applies,
# where it stops being valid, why it holds and its source.
TOPOLOGY_HINTS = {
    "lowpass": " ".join([render("prompt.passive.rc_lowpass"),
                         render("prompt.order_rolloff_lowpass", capital=True),
                         render("prompt.passive.lc_ladder_lowpass")]),
    "highpass": " ".join([render("prompt.passive.rc_highpass"),
                          render("prompt.order_rolloff_highpass", capital=True),
                          render("prompt.passive.ladder_highpass")]),
    "bandpass": render("prompt.passive.series_rlc_bandpass"),
    "notch": render("prompt.passive.series_rlc_notch"),
}


def build_termination_note(spec: DesignSpec) -> str:
    """
    What surrounds the filter. The harness, not the model, owns the source
    and load: with none set the source is ideal and 'out' is unloaded, and
    the ladder tables in the hint would give an undamped, peaking filter -
    so the model is told so rather than left to find out from the verdict.
    """
    if not spec.terminated:
        return render("prompt.termination.unterminated") + "\n"
    src = f"{spec.source_ohm:g} ohm" if spec.source_ohm > 0 else "an ideal (0 ohm)"
    load = (f"a {spec.load_ohm:g} ohm resistor from 'out' to ground"
            if spec.load_ohm is not None else "nothing (unloaded)")
    return render("prompt.termination.fixed", src=src, load=load, source_id=SOURCE_ID,
                  load_id=LOAD_ID, source_node=SOURCE_NODE) + "\n"


ACTIVE_HINTS = {
    "lowpass": render("prompt.active.sallen_key_lowpass"),
    "highpass": render("prompt.active.sallen_key_highpass"),
    "bandpass": render("prompt.active.mfb_bandpass"),
    "notch": render("prompt.active.notch_buffer"),
}


def _fmt_h(h: float) -> str:
    return format_value(h, "H")


def _fmt_range(r: tuple[float, float], unit: str) -> str:
    return f"{format_value(r[0], unit)} to {format_value(r[1], unit)}"


def build_parts_note(spec: DesignSpec) -> str:
    """
    Which parts may be used - stated neutrally. Rails mean op-amps are
    ALLOWED, never that they are preferred: with a one-sided prompt the
    model used an op-amp first in all nine powered live runs (2026-11-23),
    whatever the requirements said.
    """
    if not spec.active_allowed:
        why = ("the user asked for passive parts only" if spec.powered
               else "no supply rails are provided")
        return render("prompt.parts.passive_only", why=why) + "\n"
    grades = "\n".join(f"    * {OPAMPS[n].summary()}, supply current "
                       f"{OPAMPS[n].quiescent_a * 1e3:g} mA" for n in SELECTABLE_OPAMPS)
    physics = ("with technology 'ideal' every op-amp is simulated as an ideal op-amp, "
               "whatever grade you choose" if get_profile(spec.technology).is_ideal
               else f"each op-amp is simulated as the grade you choose, built as "
                    f"{spec.technology}")
    return (render("prompt.parts.active", vcc=VCC, vee=VEE, pos=f"{spec.supply_pos_v:g}",
                   neg=f"{spec.supply_neg_v:g}", physics=physics, grades=grades)
            + "\n" + build_tradeoff_note(spec))


def build_tradeoff_note(spec: DesignSpec) -> str:
    """The requirements that decide passive vs active, with this spec's numbers."""
    bits = []
    if spec.passband_gain_db is not None and spec.passband_gain_db > 0:
        bits.append(render("prompt.tradeoff.gain_needs_active",
                           gain_db=f"{spec.passband_gain_db:g}"))
    else:
        bits.append(render("prompt.tradeoff.gain_either"))
    if spec.load_ohm is not None:
        bits.append(render("prompt.tradeoff.load_isolation", load=f"{spec.load_ohm:g}"))
    l_1k = 1e3 / (2 * math.pi * spec.target_hz)
    q_part = ""
    if spec.filter_type == "bandpass" and spec.bandwidth_hz:
        q = spec.target_hz / spec.bandwidth_hz
        q_part = render("prompt.tradeoff.q_scaling", q=f"{q:g}", l_q=_fmt_h(l_1k * q))
    limit = get_profile(spec.technology).inductor_range[1]
    q_part += (render("prompt.tradeoff.inductor_any") if limit == float("inf") else
               render("prompt.tradeoff.inductor_limit", limit=_fmt_h(limit),
                      technology=spec.technology))
    bits.append(render("prompt.tradeoff.inductor_size", f=f"{spec.target_hz:g}",
                       l_1k=_fmt_h(l_1k), q_part=q_part))
    if spec.max_supply_current_a is not None:
        bits.append(render("prompt.tradeoff.budget",
                           ma=f"{spec.max_supply_current_a * 1e3:g}"))
    bits.append(render("prompt.tradeoff.opamp_swing") + "; "
                + render("prompt.tradeoff.opamp_gain"))
    bits.append(render("prompt.tradeoff.passive_no_power"))
    return "  TRADE-OFFS: " + "; ".join(bits) + ".\n"


def build_series_note(spec: DesignSpec) -> str:
    """The value series the spec requires, with every value of each - the
    model cannot be expected to know E96 by heart. Types sharing a series
    are listed together, so the table appears once."""
    if not spec.value_series:
        return ""
    by_series: dict[str, list[str]] = {}
    for kind, series in spec.value_series.items():
        by_series.setdefault(series, []).append(kind + "s")
    lines = [render("prompt.series.line", types=" and ".join(kinds), series=series,
                    values=", ".join(f"{m:g}" for m in get_series(series)))
             for series, kinds in by_series.items()]
    return "- " + render("prompt.series.required", lines="; ".join(lines)) + "\n"


def build_hints(spec: DesignSpec) -> str:
    """Passive and active options with equal weight."""
    text = f"Passive option: {TOPOLOGY_HINTS[spec.filter_type]}"
    if spec.active_allowed:
        text += f"\nActive option: {ACTIVE_HINTS[spec.filter_type]}"
    return text


def build_technology_note(spec: DesignSpec) -> str:
    """
    Tell the model how the circuit will be built, when that matters.

    This mirrors real practice: a designer does not pick L and C from a
    formula alone, they pick parts whose parasitics do not ruin the band
    of interest. The usual rule of thumb is to keep an inductor's
    self-resonant frequency well clear of the highest frequency that
    matters - so the model needs to know what it is building on.
    """
    profile = get_profile(spec.technology)
    if profile.is_ideal:
        return ""
    return (
        f"- CONSTRUCTION: this will be built as {profile.name}. "
        f"{profile.describe()}\n"
        f"  Components are therefore NOT ideal, and the simulation includes "
        f"these parasitics. " + render("prompt.technology.srf") + " "
        + render("prompt.technology.smaller_inductors", capital=True)
        + ("" if not profile.has_value_limits else
           render("prompt.technology.value_ranges", technology=profile.name,
                  r=_fmt_range(profile.resistor_range, "ohm"),
                  c=_fmt_range(profile.capacitor_range, "F"),
                  dielectric=profile.capacitor_dielectric,
                  l=_fmt_range(profile.inductor_range, "H")))
    )


def build_system_prompt(spec: DesignSpec) -> str:
    analysis = analysis_for(spec)
    if spec.passband_gain_db is not None:
        gain_line = (f"Passband gain {spec.passband_gain_db:g} dB "
                     f"(+/-{spec.gain_tolerance_db:g} dB) - this is checked")
    elif spec.terminated:
        gain_line = "Do not add loss beyond the given source/load divider"
    else:
        gain_line = "Unity passband gain (0 dB) — do not add a resistive divider in the passband"
    return f"""You are an analog circuit design agent. Design a filter \
meeting this spec:

- {spec.describe()}
- {gain_line}
- Topology: single voltage source 'V1' (nodes ['in','0'], value 'AC 1'), \
then the parts forming the filter from node 'in' to node 'out' (node '0' is ground)
- The simulation sweeps {analysis['fstart']} Hz to {analysis['fstop']}; \
the target frequency must land well inside that range.
- Judged from {spec.judge_window()[0]:g} Hz to {spec.judge_window()[1]:g} Hz: \
every requirement is checked inside that range; the response outside it is \
reported to you but cannot fail the design.
{build_termination_note(spec)}{build_parts_note(spec)}{build_series_note(spec)}\
{build_technology_note(spec)}
{build_hints(spec)}

Call the simulate_circuit tool with your proposed component values. You'll \
get back the ACTUAL simulated response from ngspice — not a guess. If it \
doesn't pass, revise your design based on the real result and try again. \
The result's message says what failed and how to fix it; act on its concrete \
numbers (value scale factor, suggested order, limiting parasitic) rather than \
guessing. {render("prompt.realistic_values", capital=True)}"""
