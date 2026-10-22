"""Smoke test: `python -m spice_agent.circuit` prints a compiled netlist.

Shows the same circuit as designed and as built, which is the quickest way
to see what the parasitic expansion actually does.
"""

from spice_agent.circuit import Circuit

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

circuit = Circuit.from_dict(EXAMPLE)
for technology in ("ideal", "breadboard"):
    print(f"===== {technology} =====")
    print(circuit.to_netlist(output_path="output.txt", technology=technology))
