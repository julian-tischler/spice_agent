"""
Op-amp macromodels, generated from a handful of documented parameters.

Each part is a small behavioural model in the spirit of the classic Boyle
macromodel, built only from elements ngspice and LTspice both understand:

    inp, inn  -- Rin between them, input bias current Ib into each pin
    gain      -- a transconductance into R1 || C1: open-loop gain
                 A0 = gm*R1 and a single pole at GBW / A0, so the
                 gain-bandwidth product is exactly GBW
    slew      -- the transconductance saturates (tanh) at Imax, so C1
                 cannot charge faster than Imax/C1 = the slew rate
    swing     -- diodes clamp the gain node to [vee + headroom,
                 vcc - headroom]: a saturated output really saturates
    output    -- a unity buffer behind Rout
    supply    -- a constant quiescent current from vcc to vee

What it does NOT model: offset voltage (0 V, so results are deterministic),
noise, distortion beyond slew limiting, output current limiting, phase
reversal, supply rejection, or a second pole. Those are stage-2 concerns.

GRADES, NOT PHYSICS. The model chooses an op-amp GRADE (SELECTABLE_OPAMPS) -
the role a value plays for a passive part: a design trade-off in bandwidth,
slew rate, supply range and bias current. How that grade is SIMULATED is
decided by the technology, exactly as for passives: technology "ideal"
simulates every op-amp as IDEAL_OPAMP (as every inductor is then perfect);
a real technology uses the grade's macromodel plus board parasitics.
IDEAL_OPAMP is therefore not on the menu: offered next to real grades, a
model simply picks perfection (seen live, 2026-11-23). It stays in the
library as the ideal-technology expansion and for the diagnosis's "would
an ideal op-amp pass?" check.

The parameter sets are GENERIC classes of part described by typical
datasheet figures, not copies of any vendor model. The names say what class
they stand for, not which product: a design built on OPAMP_GP1 tells you
what a 1 MHz general-purpose bipolar op-amp would do, not what one
particular part would.
"""

import math
from dataclasses import dataclass

# Pin names as the model sees them (in the JSON) and their order in the
# .subckt line. Kept here so the circuit model, schema and exporter agree.
OPAMP_PINS = ("in+", "in-", "vcc", "vee", "out")

_TRANSCONDUCTANCE = 1e-3          # gm of the gain stage, siemens


@dataclass(frozen=True)
class OpampModel:
    name: str
    description: str
    open_loop_gain_db: float
    gbw_hz: float
    slew_v_per_us: float
    rout_ohm: float
    # output swing: how close the output gets to each rail, volts
    output_headroom_v: float
    # input common-mode range: how close the inputs may get to each rail
    input_headroom_v: float
    input_bias_a: float
    rin_ohm: float
    quiescent_a: float
    # total supply (vcc - vee) the part is rated for
    supply_min_v: float
    supply_max_v: float
    source: str

    @property
    def a0(self) -> float:
        return 10 ** (self.open_loop_gain_db / 20)

    @property
    def subckt_name(self) -> str:
        return self.name

    def subckt(self) -> str:
        """The .subckt text, pins in OPAMP_PINS order."""
        r1 = self.a0 / _TRANSCONDUCTANCE
        pole = self.gbw_hz / self.a0
        c1 = 1 / (2 * math.pi * pole * r1)
        imax = self.slew_v_per_us * 1e6 * c1
        lines = [
            f".subckt {self.subckt_name} inp inn vcc vee out",
            f"* {self.description}",
            f"* A0 {self.open_loop_gain_db:g} dB, GBW {self.gbw_hz:g} Hz, slew "
            f"{self.slew_v_per_us:g} V/us, swing to {self.output_headroom_v:g} V "
            f"from each rail",
            f"Rin inp inn {self.rin_ohm:.6g}",
        ]
        if self.input_bias_a:
            lines += [f"Ibp inp 0 DC {self.input_bias_a:.6g}",
                      f"Ibn inn 0 DC {self.input_bias_a:.6g}"]
        lines += [
            f"B1 0 n1 I = {imax:.6g}*tanh({_TRANSCONDUCTANCE:g}*(v(inp)-v(inn))/{imax:.6g})",
            f"R1 n1 0 {r1:.6g}",
            f"C1 n1 0 {c1:.6g}",
            f"Vhp vcc hp DC {self.output_headroom_v:.6g}",
            "D1 n1 hp DCLAMP",
            f"Vhn ln vee DC {self.output_headroom_v:.6g}",
            "D2 ln n1 DCLAMP",
            "E1 o1 0 n1 0 1",
            f"Rout o1 out {self.rout_ohm:.6g}",
        ]
        if self.quiescent_a:
            lines.append(f"Iq vcc vee DC {self.quiescent_a:.6g}")
        lines += [".model DCLAMP D(IS=1e-15 N=0.05)", ".ends"]
        return "\n".join(lines)

    def summary(self) -> str:
        """One line for the design prompt."""
        swing = ("rail-to-rail output" if self.output_headroom_v < 0.1 else
                 f"output swings to within {self.output_headroom_v:g} V of each rail")
        inputs = ("inputs may reach both rails" if self.input_headroom_v < 0.1 else
                  f"inputs must stay {self.input_headroom_v:g} V inside the rails")
        bias = ("" if not self.input_bias_a else
                f", input bias current {self.input_bias_a:g} A per input (needs a DC path)")
        return (f"{self.name}: {self.description}; GBW {self.gbw_hz / 1e6:g} MHz, slew "
                f"{self.slew_v_per_us:g} V/us, {swing}, {inputs}{bias}; supply "
                f"{self.supply_min_v:g}-{self.supply_max_v:g} V total")


IDEAL_OPAMP = OpampModel(
    name="OPAMP_IDEAL",
    description="near-ideal op-amp: 120 dB, 1 GHz, rail-to-rail, no bias current",
    open_loop_gain_db=120, gbw_hz=1e9, slew_v_per_us=1e4, rout_ohm=0.01,
    output_headroom_v=0.0, input_headroom_v=0.0, input_bias_a=0.0, rin_ohm=1e12,
    quiescent_a=0.0, supply_min_v=0.0, supply_max_v=1e3,
    source="textbook ideal op-amp; the output still clamps at the rails",
)

OPAMPS = {m.name: m for m in (
    IDEAL_OPAMP,
    OpampModel(
        name="OPAMP_GP1",
        description="general-purpose bipolar op-amp, 1 MHz (LM358/LM741 class)",
        open_loop_gain_db=100, gbw_hz=1e6, slew_v_per_us=0.5, rout_ohm=75,
        output_headroom_v=1.5, input_headroom_v=1.5, input_bias_a=50e-9, rin_ohm=2e6,
        quiescent_a=0.5e-3, supply_min_v=3, supply_max_v=32,
        source="typical datasheet figures for the class"),
    OpampModel(
        name="OPAMP_FET3",
        description="JFET-input op-amp, 3 MHz (TL07x class)",
        open_loop_gain_db=106, gbw_hz=3e6, slew_v_per_us=13, rout_ohm=50,
        output_headroom_v=1.5, input_headroom_v=3.0, input_bias_a=30e-12, rin_ohm=1e12,
        quiescent_a=1.4e-3, supply_min_v=10, supply_max_v=36,
        source="typical datasheet figures for the class"),
    OpampModel(
        name="OPAMP_RR10",
        description="CMOS rail-to-rail op-amp, 10 MHz, low voltage",
        open_loop_gain_db=110, gbw_hz=10e6, slew_v_per_us=6, rout_ohm=50,
        output_headroom_v=0.05, input_headroom_v=0.0, input_bias_a=1e-12, rin_ohm=1e13,
        quiescent_a=1.0e-3, supply_min_v=2.7, supply_max_v=5.5,
        source="typical datasheet figures for the class"),
)}


# What the model may choose: every grade except the ideal expansion.
SELECTABLE_OPAMPS = tuple(name for name in OPAMPS if name != IDEAL_OPAMP.name)


def get_opamp(name: str) -> OpampModel:
    try:
        return OPAMPS[name]
    except KeyError:
        raise ValueError(f"Unknown op-amp part {name!r}; available: {sorted(OPAMPS)}") from None
