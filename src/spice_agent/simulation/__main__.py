"""Smoke test: `python -m spice_agent.simulation` runs one verification.

1k + 100n gives a theoretical cutoff of ~1.59 kHz, so this exercises the
whole path - compile, simulate, measure, judge - against a known answer.
"""

from spice_agent.simulation import DesignSpec, simulate_and_verify

EXAMPLE = {
    "title": "RC Low-Pass Filter",
    "components": [
        {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
        {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "1k"},
        {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "100n"},
    ],
    "analysis": {"type": "ac", "sweep": "dec", "points": 100,
                 "fstart": "1", "fstop": "1meg", "output_node": "out"},
}

print(simulate_and_verify(EXAMPLE, DesignSpec("lowpass", 1590.0))["message"])
