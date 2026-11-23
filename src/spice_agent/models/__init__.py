"""
The component model library: SPICE models for parts that need one.

    opamps.py   op-amp macromodels, generated from documented parameters
    series.py   the standard E-series values passive parts are sold in

Every model here is written for this project from published, generic
figures (gain, gain-bandwidth, slew rate, output swing, ...), so it can be
committed and redistributed. Vendor models are not bundled: their licences
generally forbid redistribution, and some are encrypted and will not run in
ngspice at all.

series.py is the passive side of the catalogue: not a SPICE model, but the
values a resistor, capacitor or inductor can actually be bought in.

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
from spice_agent.models.series import SERIES, TOLERANCE_PCT, get_series, is_member, neighbours

__all__ = ["IDEAL_OPAMP", "OPAMP_PINS", "OPAMPS", "SELECTABLE_OPAMPS", "OpampModel",
           "SERIES", "TOLERANCE_PCT", "get_opamp", "get_series", "is_member", "neighbours"]
