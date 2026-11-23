"""
Is every active part biased where it can work?

An AC sweep linearises the circuit around its DC operating point, so it
produces a believable curve even for an op-amp whose output is pinned at a
rail - a circuit that, built, would output nothing useful. This module
reads the operating point (`op`, see simulation/raw.py) and checks each
op-amp against its library limits:

  * supply    the rails lie within the part's rated total supply
  * output    the DC output is not at (or within SATURATION_MARGIN_V of)
              the part's swing limit - i.e. not saturated
  * inputs    both inputs are inside the input common-mode range
  * DC path   both inputs have a DC path to something that holds a voltage;
              otherwise the input bias current charges the node until the
              output rails - the classic capacitor-coupled-input mistake

A failed bias check fails the design: the AC result describes a circuit
that would not work.
"""

from spice_agent.circuit.model import GROUND, MULTI_PIN, VCC, VEE, Circuit
from spice_agent.hints import render
from spice_agent.models import IDEAL_OPAMP, OPAMP_PINS, get_opamp
from spice_agent.simulation.raw import node_voltage

# How close to its swing limit a DC output may sit before it counts as
# saturated. The clamp is soft (diodes), so a saturated output sits a few
# tens of mV inside the nominal limit.
SATURATION_MARGIN_V = 0.1


def _dc_connected(circuit: Circuit) -> set[str]:
    """Nodes with a DC path (through resistors, inductors or sources) to a
    node that holds its voltage: ground, the rails, or an op-amp output."""
    held = {GROUND, VCC, VEE}
    edges: dict[str, set[str]] = {}
    for c in circuit.components:
        if c.type in MULTI_PIN:
            held.add(c.nodes[OPAMP_PINS.index("out")])
            continue
        if c.type == "capacitor" or c.type == "current_source":
            continue                                   # no DC path through these
        a, b = c.nodes
        edges.setdefault(a, set()).add(b)
        edges.setdefault(b, set()).add(a)
    seen, todo = set(held), list(held)
    while todo:
        n = todo.pop()
        for m in edges.get(n, ()):
            if m not in seen:
                seen.add(m)
                todo.append(m)
    return seen


def check_bias(circuit: Circuit, op: dict[str, float] | None,
               supply_pos: float, supply_neg: float, ideal: bool = False) -> dict:
    """
    Check every op-amp in `circuit` (the circuit as simulated, terminations
    and supplies included) against operating point `op`.

    Two kinds of limit, kept apart on purpose:
      * catalogue properties of the chosen grade - its rated supply range -
        apply on every technology: an ideal simulation of a part that the
        rails would destroy is still not a design;
      * electrical behaviour follows the technology: with `ideal` every
        op-amp was simulated as the ideal one, so swing and input range are
        the ideal part's (rail-to-rail), as an ideal inductor has no
        self-resonance.
    A missing DC path is a fault on every technology: the node's DC level is
    then undefined, whatever the part. Returns
    {"ok", "parts": [...], "supply_current_a", "message"}.
    """
    if op is None:
        return {"ok": False, "parts": [], "supply_current_a": None,
                "message": render("bias.no_operating_point")}
    dc = _dc_connected(circuit)
    total = supply_pos - supply_neg
    parts, texts = [], []
    for c in circuit.components:
        if c.type not in MULTI_PIN:
            continue
        grade = get_opamp(c.part)                        # catalogue: rating
        m = IDEAL_OPAMP if ideal else grade              # electrical behaviour
        pins = dict(zip(OPAMP_PINS, c.nodes))
        v = {p: node_voltage(op, pins[p]) for p in ("in+", "in-", "out")}
        hi_out, lo_out = supply_pos - m.output_headroom_v, supply_neg + m.output_headroom_v
        hi_in, lo_in = supply_pos - m.input_headroom_v, supply_neg + m.input_headroom_v
        issues = []
        if not (grade.supply_min_v <= total <= grade.supply_max_v):
            issues.append(render("bias.supply_rating", part=grade.name,
                                 lo=f"{grade.supply_min_v:g}", hi=f"{grade.supply_max_v:g}",
                                 total=f"{total:g}"))
        if pins["vcc"] != VCC or pins["vee"] != VEE:
            issues.append(render("bias.pins_not_on_rails", vcc=pins["vcc"],
                                 vee=pins["vee"], rail_p=VCC, rail_n=VEE))
        saturated = (v["out"] >= hi_out - SATURATION_MARGIN_V
                     or v["out"] <= lo_out + SATURATION_MARGIN_V)
        if saturated:
            issues.append(
                render("bias.saturated", v_out=f"{v['out']:+.2f}", lo=f"{lo_out:+.2f}",
                       hi=f"{hi_out:+.2f}")
                + ". " + render("bias.saturated_causes", capital=True))
        for pin in ("in+", "in-"):
            if not (lo_in - 1e-3 <= v[pin] <= hi_in + 1e-3):
                issues.append(render("bias.input_range", pin=pin, v=f"{v[pin]:+.2f}",
                                     lo=f"{lo_in:+.2f}", hi=f"{hi_in:+.2f}"))
            if pins[pin] not in dc:
                issues.append(render(
                    "bias.no_dc_path", pin=pin, node=pins[pin],
                    bias=f" ({m.input_bias_a:g} A)" if m.input_bias_a else ""))
        entry = {"id": c.id, "part": c.part, "v_out": v["out"], "v_in+": v["in+"],
                 "v_in-": v["in-"], "output_range": [lo_out, hi_out],
                 "saturated": saturated, "issues": issues}
        parts.append(entry)
        if issues:
            texts.append(f"{c.id} ({c.part}): " + "; ".join(issues))
    supply_i = op.get("i(v_supply_pos)")
    result = {"ok": not texts, "parts": parts,
              "supply_current_a": None if supply_i is None else -supply_i}
    result["message"] = "; ".join(texts)
    return result


def check_supply_budget(circuit: Circuit, op: dict[str, float] | None, spec,
                        ideal: bool = False) -> dict:
    """
    The current drawn from each supply rail against spec.max_supply_current_a.

    Measured from the operating point: everything the circuit draws from
    V_SUPPLY_POS / V_SUPPLY_NEG, the op-amps' own consumption included. On
    ideal technology the op-amps were simulated as ideal and draw nothing,
    so each grade's catalogue quiescent current is added - the budget is a
    property of the parts chosen and binds on every technology. Returns
    {"ok", "pos_a", "neg_a", "limit_a", "per_part", "message"}.
    """
    limit = spec.max_supply_current_a
    actives = [c for c in circuit.components if c.type in MULTI_PIN]
    per_part = [{"id": c.id, "part": c.part, "quiescent_a": get_opamp(c.part).quiescent_a}
                for c in actives]
    if op is None:
        return {"ok": limit is None, "pos_a": None, "neg_a": None, "limit_a": limit,
                "per_part": per_part, "message": "" if limit is None else
                "the operating point could not be solved, so the supply current is unknown"}
    pos = abs(op.get("i(v_supply_pos)", 0.0))
    neg = abs(op.get("i(v_supply_neg)", 0.0))
    if ideal:
        extra = sum(p["quiescent_a"] for p in per_part)
        pos, neg = pos + extra, neg + extra
    result = {"ok": True, "pos_a": pos, "neg_a": neg, "limit_a": limit,
              "per_part": per_part, "message": ""}
    if limit is None:
        return result
    worst = max(pos, neg)
    result["margin_a"] = limit - worst
    if worst > limit:
        rail = (f"+{spec.supply_pos_v:g} V" if pos >= neg else f"{spec.supply_neg_v:g} V")
        parts = ", ".join(f"{p['id']} ({p['part']}) {p['quiescent_a'] * 1e3:g} mA"
                          for p in per_part)
        result["ok"] = False
        result["message"] = (
            render("budget.over", ma=f"{worst * 1e3:.2f}", rail=rail,
                   limit=f"{limit * 1e3:g}",
                   parts=f"; op-amps alone: {parts}" if parts else "")
            + " - " + render("budget.levers"))
    return result
