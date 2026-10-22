"""
Reference circuits shared across the test suite.

Each is the JSON form of one of the hand-written netlists in
spice_agent.templates, with known analytically checkable behaviour. They
stand in for what a correct model proposal looks like, so a test can
assert on a verdict without an LLM in the loop.
"""

# The standard AC sweeps. WIDE reaches far enough to see parasitic
# effects, which live from a few hundred kHz upward.
AC = {"type": "ac", "sweep": "dec", "points": 100, "fstart": "1",
      "fstop": "1meg", "output_node": "out"}
WIDE = {**AC, "fstop": "100meg"}


def _src():
    return {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"}


RC_LOWPASS = {           # fc ~ 1591 Hz
    "title": "RC low-pass",
    "components": [
        _src(),
        {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "1k"},
        {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "100n"},
    ],
}
RC_HIGHPASS = {          # fc ~ 1591 Hz
    "title": "RC high-pass",
    "components": [
        _src(),
        {"id": "C1", "type": "capacitor", "nodes": ["in", "out"], "value": "100n"},
        {"id": "R1", "type": "resistor", "nodes": ["out", "0"], "value": "1k"},
    ],
}
RLC_BANDPASS = {         # f0 ~ 5033 Hz, BW ~ 1591 Hz
    "title": "RLC band-pass",
    "components": [
        _src(),
        {"id": "L1", "type": "inductor", "nodes": ["in", "n1"], "value": "10m"},
        {"id": "C1", "type": "capacitor", "nodes": ["n1", "out"], "value": "100n"},
        {"id": "R1", "type": "resistor", "nodes": ["out", "0"], "value": "100"},
    ],
}
RLC_NOTCH = {            # f0 ~ 5033 Hz, depth ~ 31 dB
    "title": "RLC notch",
    "components": [
        _src(),
        {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "100"},
        {"id": "L1", "type": "inductor", "nodes": ["out", "n1"], "value": "10m"},
        {"id": "C1", "type": "capacitor", "nodes": ["n1", "0"], "value": "100n"},
    ],
}
TWIN_T_NOTCH = {         # f0 ~ 1591 Hz, depth ~ 53 dB
    "title": "Twin-T notch",
    "components": [
        _src(),
        {"id": "R1", "type": "resistor", "nodes": ["in", "n1"], "value": "1k"},
        {"id": "R2", "type": "resistor", "nodes": ["n1", "out"], "value": "1k"},
        {"id": "C3", "type": "capacitor", "nodes": ["n1", "0"], "value": "200n"},
        {"id": "C1", "type": "capacitor", "nodes": ["in", "n2"], "value": "100n"},
        {"id": "C2", "type": "capacitor", "nodes": ["n2", "out"], "value": "100n"},
        {"id": "R3", "type": "resistor", "nodes": ["n2", "0"], "value": "500"},
    ],
}
DIVIDER = {              # flat -6 dB, not a filter at all
    "title": "Resistive divider",
    "components": [
        _src(),
        {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "1k"},
        {"id": "R2", "type": "resistor", "nodes": ["out", "0"], "value": "1k"},
    ],
}


def _with_values(circuit: dict, **values: str) -> dict:
    """Copy of a reference circuit with some component values swapped."""
    out = {"title": circuit["title"], "components": []}
    for c in circuit["components"]:
        c = dict(c)
        if c["id"] in values:
            c["value"] = values[c["id"]]
        out["components"].append(c)
    return out
