"""
Parasitic values per construction technology -- EDIT THE NUMBERS HERE.

This file holds nothing but data and the formulas that scale it. The code
that turns an ideal component into its parasitic equivalent circuit lives
in netlist_generator; this module only answers "how bad are the parasitics
if I build it this way".

------------------------------------------------------------------------
WHAT THESE NUMBERS ARE, AND WHAT THEY ARE NOT
------------------------------------------------------------------------
They are representative engineering figures for a *class* of construction,
taken from standard rules of thumb and typical datasheet values. They are
NOT measurements of your parts, and they are NOT extracted from a layout
-- we have no layout, so trace lengths genuinely do not exist here (see
TechnologyProfile.interconnect_* below).

Treat a prediction from this model as "a filter built roughly this way
behaves roughly like this", never as "your board will measure this". A
tight layout beats these figures; a sloppy one is worse.

The single most load-bearing number is the inductor's winding capacitance,
because it sets the self-resonant frequency, and SRF is what decides where
an LC filter's stopband stops working. Everything else is a correction by
comparison. If you get to measure one thing, measure that -- an impedance
sweep of a single inductor pins it directly.

------------------------------------------------------------------------
SOURCES AND CONFIDENCE
------------------------------------------------------------------------
  inductor winding capacitance  LOW confidence. Varies hugely with winding
                                style; 5-30 pF is the usual range quoted
                                for mH-class leaded chokes. ANCHOR THIS.
  inductor Q at self-resonance  LOW confidence. Filter inductors run
                                Q = 20-80 there; the value only changes how
                                sharp the resonant peak is, not where it is.
  inductor DCR                  MEDIUM. Scales with turns, so roughly with
                                sqrt(L) within one family.
  MLCC ESL                      HIGH. 0.5-0.9 nH for 0603 is a standard
                                figure in vendor application notes.
  lead / trace inductance       MEDIUM. ~1 nH per mm is the long-standing
                                rule of thumb for a narrow trace or a
                                component lead.
  breadboard strip coupling     LOW. A few pF between adjacent strips.
  breadboard contact resistance LOW. Tens of milliohms, and genuinely
                                variable between insertions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from spice_agent.units import parse_spice_value  # noqa: F401  (re-export)


# ---------------------------------------------------------------------
# Per-technology parasitic data
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class TechnologyProfile:
    """
    Parasitics for one way of physically building a circuit.

    All values are SI (farads, henries, ohms). Zero means "not modelled",
    which is how the IDEAL profile switches everything off.
    """

    name: str
    description: str

    # -- inductor -----------------------------------------------------
    # Winding (inter-turn) capacitance, taken as CONSTANT within a family.
    # This is the approximation that sets SRF; see module docstring.
    inductor_winding_c: float = 0.0
    # DCR is modelled as k * sqrt(L), since turns -- and so wire length --
    # grow roughly with sqrt(L) for a fixed core and gauge.
    inductor_dcr_per_sqrt_h: float = 0.0
    # Quality factor at self-resonance, which sets the parallel loss
    # resistance R_p = Q * sqrt(L/Cp). Stored as a dimensionless Q rather
    # than a resistance because the right R_p scales with the part:
    # sqrt(L/Cp) is 25.8 kohm at 10 mH but only 4.6 kohm at 318 uH.
    #
    # Without it the resonance is damped by DCR alone, which gives Q ~ 860
    # for a 10 mH part and so an unphysically sharp, deep notch at SRF.
    # Real filter inductors sit around Q = 20-80 there; a deliberately
    # lossy part such as a ferrite bead is nearer Q = 1.
    inductor_q_at_srf: float = 0.0

    # -- capacitor ----------------------------------------------------
    capacitor_esl: float = 0.0
    capacitor_esr: float = 0.0

    # -- resistor -----------------------------------------------------
    resistor_series_l: float = 0.0
    resistor_shunt_c: float = 0.0

    # -- mounting and interconnect ------------------------------------
    # Per component terminal: pad + lead + the trace or jumper reaching it.
    # This is the lumped stand-in for a layout we do not have.
    interconnect_l_per_terminal: float = 0.0
    # Stray capacitance from each internal node to ground. Meaningful when
    # there IS a ground plane; on a breadboard the dominant stray is
    # node-to-node instead (see io_coupling_c).
    interconnect_node_c: float = 0.0
    # Contact resistance per terminal. Soldered joints are negligible;
    # solderless contacts are not, and are variable between insertions.
    contact_resistance: float = 0.0
    # STRUCTURAL, not a scaled version of anything above: capacitance
    # directly between the input and output nodes. With no ground plane,
    # adjacent breadboard strips couple, which gives the signal a path
    # AROUND the filter. That sets a hard floor on stopband attenuation
    # which no amount of extra filter order can lower.
    io_coupling_c: float = 0.0

    # -- availability -------------------------------------------------
    # Technology constrains which parts exist: a 24 mH inductor is a
    # leaded choke, not something you find in an 0603 package. Advisory
    # only -- nothing refuses to simulate an unavailable value.
    max_practical_inductance: float = float("inf")

    # ---- derived quantities, the useful public API ------------------

    def inductor_dcr(self, inductance_h: float) -> float:
        """Series resistance of an inductor of this value."""
        return self.inductor_dcr_per_sqrt_h * math.sqrt(max(inductance_h, 0.0))

    def inductor_rp(self, inductance_h: float) -> float:
        """
        Parallel loss resistance standing in for core and dielectric loss.

        R_p = Q * sqrt(L/Cp) is the impedance the tank presents AT
        resonance, so this value is directly the height of the self-resonant
        impedance peak - set R_p to 500 kohm and the peak measures 497 kohm.

        Returns 0.0 when there is no winding capacitance: no tank, so
        nothing to damp. Does not affect the SRF itself, only its sharpness.
        """
        if (self.inductor_q_at_srf <= 0 or self.inductor_winding_c <= 0
                or inductance_h <= 0):
            return 0.0
        return self.inductor_q_at_srf * math.sqrt(
            inductance_h / self.inductor_winding_c)

    def inductor_srf_hz(self, inductance_h: float) -> float | None:
        """
        Predicted self-resonant frequency, 1 / (2*pi*sqrt(L*Cp)).

        This is the number to check the model against: pick any real
        inductor, read its datasheet SRF, and compare. It is a single
        unambiguous figure that validates the winding-capacitance guess
        independently of everything else in the project.
        """
        if self.inductor_winding_c <= 0 or inductance_h <= 0:
            return None
        return 1.0 / (2 * math.pi * math.sqrt(inductance_h * self.inductor_winding_c))

    def capacitor_srf_hz(self, capacitance_f: float) -> float | None:
        """Where a capacitor stops being a capacitor, from its ESL."""
        if self.capacitor_esl <= 0 or capacitance_f <= 0:
            return None
        return 1.0 / (2 * math.pi * math.sqrt(capacitance_f * self.capacitor_esl))

    @property
    def is_ideal(self) -> bool:
        return self.name == "ideal"

    def describe(self) -> str:
        """One-paragraph summary for the design prompt."""
        if self.is_ideal:
            return "Components are ideal: no parasitics of any kind."
        bits = [self.description]
        if self.inductor_winding_c:
            bits.append(
                f"inductors carry about {self.inductor_winding_c * 1e12:.0f} pF of "
                f"winding capacitance, so a 1 mH part self-resonates near "
                f"{self.inductor_srf_hz(1e-3) / 1e3:.0f} kHz and behaves as a "
                f"capacitor above that"
            )
        if self.capacitor_esl:
            bits.append(f"capacitors have about {self.capacitor_esl * 1e9:.1f} nH of ESL")
        if self.interconnect_l_per_terminal:
            bits.append(
                f"each connection adds roughly "
                f"{self.interconnect_l_per_terminal * 1e9:.0f} nH"
            )
        # Only worth warning about when it is actually material. A ground
        # plane cuts input-output coupling to a few tens of femtofarads,
        # where it changes nothing and claiming a "floor" would mislead.
        if self.io_coupling_c >= 0.1e-12:
            bits.append(
                f"and input couples to output through about "
                f"{self.io_coupling_c * 1e12:.1f} pF of stray capacitance, which puts a "
                f"floor on achievable stopband attenuation however many poles are used"
            )
        return "; ".join(bits) + "."


# ---------------------------------------------------------------------
# The profiles
# ---------------------------------------------------------------------

IDEAL = TechnologyProfile(
    name="ideal",
    description="Mathematically perfect components",
)

SMD_PCB = TechnologyProfile(
    name="smd_pcb",
    description=(
        "Surface-mount parts (0603/0805) soldered to a two-layer board with a "
        "ground plane, components a few millimetres apart"
    ),
    # Small SMD ferrite/wirewound chokes wind fewer turns than leaded ones.
    inductor_winding_c=3e-12,
    inductor_dcr_per_sqrt_h=300.0,      # ~9.5 ohm at 1 mH, ~30 ohm at 10 mH
    inductor_q_at_srf=20.0,             # decent ferrite/wirewound SMD part
    capacitor_esl=0.8e-9,               # 0603 MLCC, vendor app-note figure
    capacitor_esr=0.02,
    resistor_series_l=0.4e-9,
    resistor_shunt_c=0.2e-12,
    interconnect_l_per_terminal=4e-9,   # ~4 mm of trace at ~1 nH/mm
    interconnect_node_c=0.3e-12,        # to the ground plane
    contact_resistance=0.0,             # soldered
    io_coupling_c=0.02e-12,             # a ground plane between them helps a lot
    max_practical_inductance=10e-3,
)

BREADBOARD = TechnologyProfile(
    name="breadboard",
    description=(
        "Leaded parts in a solderless breadboard with jumper wires, no ground "
        "plane, 2.54 mm strip pitch"
    ),
    # Leaded mH-class chokes: more turns, so more inter-winding capacitance.
    inductor_winding_c=15e-12,
    inductor_dcr_per_sqrt_h=300.0,
    inductor_q_at_srf=10.0,             # cheap leaded choke, lossier core
    capacitor_esl=4e-9,                 # leads dominate over the body
    capacitor_esr=0.05,
    resistor_series_l=8e-9,
    resistor_shunt_c=0.5e-12,
    interconnect_l_per_terminal=15e-9,  # lead + strip + jumper
    interconnect_node_c=2e-12,          # strip to everything around it
    contact_resistance=0.03,            # per spring contact, and variable
    io_coupling_c=2e-12,                # adjacent strips, no plane between
    max_practical_inductance=float("inf"),
)

PROFILES: dict[str, TechnologyProfile] = {
    p.name: p for p in (IDEAL, SMD_PCB, BREADBOARD)
}


def get_profile(name: str | TechnologyProfile | None) -> TechnologyProfile:
    """Look up a profile by name. None or 'ideal' gives the ideal one."""
    if name is None:
        return IDEAL
    if isinstance(name, TechnologyProfile):
        return name
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(
            f"Unknown technology profile {name!r}. Available: {sorted(PROFILES)}"
        ) from None
