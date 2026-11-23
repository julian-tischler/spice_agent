"""
Can the design be built from parts that exist?

A design whose response is perfect but needs a 22 mH SMD inductor is not a
design: the largest SMD power inductors are around 10 mH. Two things are
enforced here:

- the RANGE each passive part type is available in on the spec's
  technology (TechnologyProfile.resistor_range / capacitor_range /
  inductor_range) - always on, a fact about the technology;
- the standard value SERIES the spec requires (DesignSpec.value_series,
  models/series.py) - opt-in, a catalogue property, so it binds on every
  technology, ideal included.

Series feedback names the two nearest standard values with their distance
from the model's value. That is a table lookup: the harness never chooses
between them, never simulates them, and never searches for a design.

An unbuildable design FAILS, but is still simulated - the model sees its
response as usual, plus why it cannot be built. That follows the rule
that no check hides the others. Availability is a property of the chosen
parts, so the diagnosis classes it as a design problem, never a parasitic
one.
"""

from spice_agent.circuit import Circuit, get_profile
from spice_agent.hints import render
from spice_agent.simulation.spec import DesignSpec
from spice_agent.models.series import deviation_pct, neighbours
from spice_agent.units import format_value, parse_spice_value

UNITS = {"resistor": "ohm", "capacitor": "F", "inductor": "H"}

# What moves a part of each type back into range ("above" = too large).
LEVERS = {
    ("inductor", "above"): "availability.levers.inductor_smaller",
    ("inductor", "below"): "availability.levers.inductor_larger",
    ("capacitor", "above"): "availability.levers.capacitor_smaller",
    ("capacitor", "below"): "availability.levers.capacitor_larger",
    ("resistor", "above"): "availability.levers.resistor_smaller",
    ("resistor", "below"): "availability.levers.resistor_larger",
}

_EPS = 1e-9


def _range_violation(c, value: float, profile) -> dict | None:
    lo, hi = profile.value_range(c.type)
    if value > hi * (1 + _EPS):
        side, limit, hint = "above", hi, "availability.too_large"
    elif value < lo * (1 - _EPS):
        side, limit, hint = "below", lo, "availability.too_small"
    else:
        return None
    unit = UNITS[c.type]
    return {"kind": "range", "id": c.id, "type": c.type, "value": c.value,
            "value_si": value, "limit_si": limit, "side": side,
            "text": render(hint, id=c.id, value=format_value(value, unit),
                           limit=format_value(limit, unit), technology=profile.name)}


def _series_violation(c, value: float, series: str) -> dict | None:
    below, above = neighbours(value, series)
    if below == above:
        return None
    unit = UNITS[c.type]
    return {"kind": "series", "id": c.id, "type": c.type, "value": c.value,
            "value_si": value, "series": series, "below_si": below, "above_si": above,
            "text": render("availability.not_in_series", id=c.id, value=c.value,
                           series=series, below=format_value(below, unit),
                           below_pct=f"{deviation_pct(value, below):+.1f}%",
                           above=format_value(above, unit),
                           above_pct=f"{deviation_pct(value, above):+.1f}%")}


def check_availability(circuit: Circuit, spec: DesignSpec) -> dict:
    """{"ok", "violations": [...], "limits", "series", "message"}: the
    technology's value ranges and the spec's value series."""
    profile = get_profile(spec.technology)
    violations = []
    for c in circuit.components:
        if c.type not in UNITS:
            continue
        try:
            value = parse_spice_value(c.value)
        except (ValueError, TypeError):
            continue
        if value <= 0:
            continue                    # the validator reports non-positive values
        v = _range_violation(c, value, profile)
        if v is not None:
            violations.append(v)
        series = spec.series_for(c.type)
        if series is not None:
            v = _series_violation(c, value, series)
            if v is not None:
                violations.append(v)
    parts = []
    ranged = [v for v in violations if v["kind"] == "range"]
    if ranged:
        levers = list(dict.fromkeys(render(LEVERS[(v["type"], v["side"])]) for v in ranged))
        parts.append("; ".join(v["text"] for v in ranged) + " - " + "; ".join(levers)
                     + ". " + render("availability.impedance_scaling", capital=True))
    off_series = [v for v in violations if v["kind"] == "series"]
    if off_series:
        parts.append("; ".join(v["text"] for v in off_series))
    limits = {t: list(profile.value_range(t)) for t in UNITS} if profile.has_value_limits else None
    return {"ok": not violations, "violations": violations, "limits": limits,
            "series": spec.value_series, "message": ". ".join(parts)}
