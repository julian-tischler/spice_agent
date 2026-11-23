"""
Can the design be built from parts that exist?

A design whose response is perfect but needs a 22 mH SMD inductor is not a
design: the largest SMD power inductors are around 10 mH. The technology
profile records what is practical (TechnologyProfile.max_practical_
inductance); this module enforces it.

An unbuildable design FAILS, but is still simulated - the model sees its
response as usual, plus why it cannot be built. That follows the rule
that no check hides the others. Availability is a property of the chosen
parts, so the diagnosis classes it as a design problem, never a parasitic
one.
"""

from spice_agent.circuit import Circuit, get_profile
from spice_agent.hints import render
from spice_agent.simulation.spec import DesignSpec
from spice_agent.units import parse_spice_value


def _fmt_h(h: float) -> str:
    for scale, unit in ((1.0, "H"), (1e-3, "mH"), (1e-6, "uH")):
        if h >= scale:
            return f"{h / scale:.3g} {unit}"
    return f"{h / 1e-9:.3g} nH"


def check_availability(circuit: Circuit, spec: DesignSpec) -> dict:
    """{"ok", "violations": [...], "message"} for the spec's technology."""
    profile = get_profile(spec.technology)
    limit = profile.max_practical_inductance
    violations = []
    for c in circuit.components:
        if c.type != "inductor":
            continue
        try:
            value = parse_spice_value(c.value)
        except (ValueError, TypeError):
            continue
        if value > limit * (1 + 1e-9):
            violations.append({"id": c.id, "value": c.value, "value_h": value,
                               "limit_h": limit,
                               "text": render("availability.too_large", id=c.id,
                                              value=_fmt_h(value), limit=_fmt_h(limit),
                                              technology=profile.name)})
    message = ""
    if violations:
        message = ("; ".join(v["text"] for v in violations)
                   + " - " + render("availability.levers"))
    return {"ok": not violations, "violations": violations,
            "max_inductance_h": None if limit == float("inf") else limit,
            "message": message}
