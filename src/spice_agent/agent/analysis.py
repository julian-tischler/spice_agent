"""
Which AC sweep to simulate a spec with.

Small on purpose: prompts, the loop and the CLI all need this, and a
shared leaf module is what stops them importing each other.
"""

from spice_agent.simulation import DesignSpec


AC_ANALYSIS = {
    "type": "ac",
    "sweep": "dec",
    "points": 100,
    "fstart": "1",
    "fstop": "1meg",
    "output_node": "out",
}

# Parasitic effects live from a few hundred kHz upward - an inductor's
# self-resonance is the main one - so a 1 MHz sweep cannot see them at all.
# Non-ideal technologies therefore sweep two decades further. Ideal mode
# keeps the original range so nothing already measured shifts.
PARASITIC_FSTOP = "100meg"


def analysis_for(spec: DesignSpec) -> dict:
    """
    The AC analysis block to simulate this spec with: the fixed default
    range (wider for a non-ideal technology), extended so it always covers
    the judged window - whether that is the default two decades around the
    target or an edge the user set explicitly.
    """
    from spice_agent.units import parse_spice_value

    analysis = dict(AC_ANALYSIS)
    if spec.technology != "ideal":
        analysis["fstop"] = PARASITIC_FSTOP
    lo, hi = spec.judge_window()
    if lo < parse_spice_value(analysis["fstart"]):
        analysis["fstart"] = f"{lo:g}"
    if hi > parse_spice_value(analysis["fstop"]):
        analysis["fstop"] = f"{hi:g}"
    return analysis
