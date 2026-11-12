"""
The JSON schema the model fills in to propose a circuit.

Deliberately spec-agnostic: it describes the SHAPE of a circuit, never
the requirements. Those are in the prompt. The model never writes SPICE -
it fills this in, and the harness compiles it.
"""

from google.genai import types


CIRCUIT_JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "components": {
                "type": "array",
                "description": (
                    "List of circuit components. Must include exactly one "
                    "voltage_source with value 'AC 1' (unity magnitude) "
                    "between nodes 'in' and '0', and passives "
                    "(resistor/capacitor/inductor) forming the filter "
                    "between node 'in' and node 'out', with node '0' as ground."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "e.g. 'R1', 'C1', 'L1', 'V1' — must start with the correct prefix letter for its type.",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["resistor", "capacitor", "inductor", "voltage_source", "current_source"],
                        },
                        "nodes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Exactly 2 node names, e.g. ['in', 'out']. '0' is ground.",
                        },
                        "value": {
                            "type": "string",
                            "description": "e.g. '1k', '100n', '10m', '10u', or 'AC 1' for the source.",
                        },
                    },
                    "required": ["id", "type", "nodes", "value"],
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
