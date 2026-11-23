"""
The circuit itself: what the design is, and how it becomes SPICE text.

    model.py       Component, AnalysisSpec, Circuit
    parasitics.py  TechnologyProfile and the built-in profiles - EDIT
                   THE PARASITIC NUMBERS THERE
    expansion.py   ideal component -> parasitic equivalent circuit

Import from the package; the split above is an implementation detail.
"""

from spice_agent.circuit.expansion import expand_with_parasitics
from spice_agent.circuit.model import (
    COMPONENT_PREFIXES,
    EXTERNAL_IDS,
    GROUND,
    LOAD_ID,
    MULTI_PIN,
    SOURCE_ID,
    SOURCE_NODE,
    SUPPLY_NEG_ID,
    SUPPLY_POS_ID,
    VCC,
    VEE,
    AnalysisSpec,
    Circuit,
    Component,
)
from spice_agent.circuit.parasitics import (
    BREADBOARD,
    IDEAL,
    PROFILES,
    SMD_PCB,
    TechnologyProfile,
    get_profile,
)

__all__ = [
    "BREADBOARD",
    "COMPONENT_PREFIXES",
    "EXTERNAL_IDS",
    "GROUND",
    "LOAD_ID",
    "MULTI_PIN",
    "SOURCE_ID",
    "SOURCE_NODE",
    "SUPPLY_NEG_ID",
    "SUPPLY_POS_ID",
    "VCC",
    "VEE",
    "IDEAL",
    "PROFILES",
    "SMD_PCB",
    "AnalysisSpec",
    "Circuit",
    "Component",
    "TechnologyProfile",
    "expand_with_parasitics",
    "get_profile",
]
