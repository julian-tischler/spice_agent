"""
The JSON schema the model fills in to propose a circuit.

Deliberately spec-agnostic: it describes the SHAPE of a circuit, never
the requirements. Those are in the prompt. The model never writes SPICE -
it fills this in, and the harness compiles it.
"""

from google.genai import types

from spice_agent.models import OPAMP_PINS, SELECTABLE_OPAMPS


CIRCUIT_JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "components": {
                "type": "array",
                "description": (
                    "List of circuit components. Must include exactly one "
                    "voltage_source with value 'AC 1' (unity magnitude) "
                    "between nodes 'in' and '0', and the parts forming the "
                    "filter between node 'in' and node 'out', with node '0' "
                    "as ground. Two-terminal parts (resistor, capacitor, "
                    "inductor) use 'nodes' and 'value'. An opamp uses 'part' "
                    "and 'pins' instead, and only when supply rails are "
                    "provided (nodes 'vcc' and 'vee')."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "e.g. 'R1', 'C1', 'L1', 'V1', 'U1' — must start with the correct prefix letter for its type (opamp: 'U' or 'X').",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["resistor", "capacitor", "inductor", "voltage_source",
                                     "current_source", "opamp"],
                        },
                        "nodes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Exactly 2 node names, e.g. ['in', 'out']. '0' is ground.",
                        },
                        "value": {
                            "type": "string",
                            "description": "e.g. '1k', '100n', '10m', '10u', or 'AC 1' for the source. Not used for an opamp.",
                        },
                        "part": {
                            "type": "string",
                            "enum": sorted(SELECTABLE_OPAMPS),
                            "description": "opamp only: the op-amp grade.",
                        },
                        "pins": {
                            "type": "object",
                            "description": "opamp only: the node each pin connects to. "
                                           "'vcc'/'vee' normally go to the rail nodes 'vcc'/'vee'.",
                            "properties": {pin: {"type": "string"} for pin in OPAMP_PINS},
                            "required": list(OPAMP_PINS),
                        },
                    },
                    "required": ["id", "type"],
                },
            },
        },
        "required": ["title", "components"],
}

SIMULATE_CIRCUIT_TOOL = types.FunctionDeclaration(
    name="simulate_circuit",
    description=(
        "Simulates a circuit via ngspice (AC sweep, 1 Hz to 1 MHz) and "
        "checks it against the target filter spec given in the prompt. "
        "Returns the actual simulated cutoff / centre frequency, other "
        "measured metrics, and whether the spec passed."
    ),
    parameters=CIRCUIT_JSON_SCHEMA,
)
