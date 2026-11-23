"""
The component model library: SPICE models for parts that need one.

    opamps.py   op-amp macromodels, generated from documented parameters

Every model here is written for this project from published, generic
figures (gain, gain-bandwidth, slew rate, output swing, ...), so it can be
committed and redistributed. Vendor models are not bundled: their licences
generally forbid redistribution, and some are encrypted and will not run in
ngspice at all.

The LLM never writes a model. It picks a part by name from this library,
and the harness inlines that part's .subckt into the netlist.
"""

from spice_agent.models.opamps import (
    IDEAL_OPAMP,
    OPAMP_PINS,
    OPAMPS,
    SELECTABLE_OPAMPS,
    OpampModel,
    get_opamp,
)

__all__ = ["IDEAL_OPAMP", "OPAMP_PINS", "OPAMPS", "SELECTABLE_OPAMPS", "OpampModel",
           "get_opamp"]
