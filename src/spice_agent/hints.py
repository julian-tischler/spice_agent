"""
The hint catalogue: every piece of ADVICE or EXPLANATION the harness gives
the model, in one place, each with when it applies, where it stops being
valid, why it holds and where it comes from.

    python -m spice_agent.hints            # regenerate HINTS.md from this file

Governed by the rule in ARCHITECTURE.md: feedback and hints convey hard facts
only - measured on this design, or read from the catalogue or the spec - and
never claims about designs that were not simulated. Rules of thumb are
allowed, but always labelled as such: render() prefixes them, so the label
cannot be forgotten.

What is NOT in here: plain measurement readouts ("cutoff 9987 Hz vs target
10000 Hz"), which are formatting of numbers, validation errors about the
JSON itself, and the descriptions rendered from data that lives elsewhere
(TechnologyProfile.describe(), OpampModel.summary()).

The caller decides WHEN a hint fires - the measurement stays where it is
made (verify.py, diagnosis.py, bias.py, ...) - and asks this module only
for the words: render("stopband.rolloff_per_order"). That keeps the trigger
logic next to the check it belongs to, and the wording auditable here.
"""

import argparse
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------
# Kinds
# ---------------------------------------------------------------------

FACT = "fact"                    # exact maths or physics for the stated topology
DERIVED = "derived"              # computed from a measurement of THIS design
CATALOGUE = "catalogue"          # read from the library, a technology profile or the spec
EXPLANATION = "explanation"      # the physical meaning of a measured observation
RULE_OF_THUMB = "rule_of_thumb"  # standard engineering guidance, not a verified fact

KINDS = (FACT, DERIVED, CATALOGUE, EXPLANATION, RULE_OF_THUMB)
RULE_OF_THUMB_PREFIX = "rule of thumb: "


@dataclass(frozen=True)
class Hint:
    id: str
    kind: str
    text: str                    # str.format template
    applies_when: str            # when the caller emits it
    valid_for: str               # where it holds - and where it stops holding
    why: str                     # the reason it is true / useful
    source: str                  # where it comes from
    example: dict = field(default_factory=dict)   # values for a sample render

    def render(self, capital: bool = False, **values) -> str:
        text = self.text.format(**values)
        if self.kind == RULE_OF_THUMB:
            text = RULE_OF_THUMB_PREFIX + text
        return text[:1].upper() + text[1:] if capital else text


_HINTS: list[Hint] = []


def _h(*args, **kwargs) -> None:
    _HINTS.append(Hint(*args, **kwargs))


# =====================================================================
# Prompt: passive topology hints
# =====================================================================

_h("prompt.passive.rc_lowpass", FACT,
   "A 1st-order RC low-pass is a series resistor from 'in' to 'out' and a capacitor "
   "from 'out' to ground: fc = 1 / (2*pi*R*C).",
   applies_when="Lowpass spec: the passive option in the prompt.",
   valid_for="Ideal source and unloaded output. A source resistance adds to R; a load "
             "forms a divider and moves fc.",
   why="The RC divider's pole is at 1/(2*pi*R*C).",
   source="Circuit theory.")

_h("prompt.order_rolloff_lowpass", RULE_OF_THUMB,
   "ORDER SETS THE ROLLOFF: an n-th order filter falls off about 20*n dB per decade "
   "past the cutoff, so if a stopband attenuation is specified, choose the order from "
   "it first and only then pick component values.",
   applies_when="Lowpass spec: the passive option in the prompt.",
   valid_for="All-pole responses (Butterworth, Bessel, Chebyshev-I) well past the "
             "cutoff. NOT elliptic / Chebyshev-II, whose transmission zeros beat it "
             "near the edge; and not close to the cutoff, where the asymptote is not "
             "reached yet.",
   why="An n-th order all-pole response falls as 1/f^n far from the cutoff, i.e. "
       "20*n dB per decade.",
   source="Standard filter theory (e.g. Zverev, Handbook of Filter Synthesis).")

_h("prompt.passive.lc_ladder_lowpass", FACT,
   "Higher orders are built as an LC ladder: series inductors along the signal path "
   "from 'in' to 'out' with shunt capacitors to ground between them (L-C = 2nd order, "
   "C-L-C or L-C-L = 3rd, and so on). For a Butterworth (maximally flat) response of "
   "order n with cutoff fc into a reference impedance R0, the normalised element "
   "values are g_k = 2*sin((2k-1)*pi/(2n)), scaled as L_k = g_k*R0/(2*pi*fc) and "
   "C_k = g_k/(2*pi*fc*R0).",
   applies_when="Lowpass spec: the passive option in the prompt.",
   valid_for="Doubly terminated in equal resistances R0 (source and load). With an "
             "ideal source and open output (the default spec) the same values give an "
             "undamped, peaking filter - the termination note says so.",
   why="The g_k are the Butterworth prototype element values; impedance and "
       "frequency scaling give L_k, C_k.",
   source="Standard filter tables (e.g. Williams & Taylor, Electronic Filter Design "
          "Handbook).")

_h("prompt.passive.rc_highpass", FACT,
   "A 1st-order RC high-pass is a series capacitor from 'in' to 'out' and a resistor "
   "from 'out' to ground: fc = 1 / (2*pi*R*C).",
   applies_when="Highpass spec: the passive option in the prompt.",
   valid_for="Ideal source and unloaded output (a load appears in parallel with R).",
   why="The CR divider's zero-pole pair puts the -3 dB point at 1/(2*pi*R*C).",
   source="Circuit theory.")

_h("prompt.order_rolloff_highpass", RULE_OF_THUMB,
   "ORDER SETS THE ROLLOFF: an n-th order high-pass rises at about 20*n dB per decade "
   "below the cutoff, so pick the order from any stopband requirement first.",
   applies_when="Highpass spec: the passive option in the prompt.",
   valid_for="All-pole responses well below the cutoff; not elliptic / Chebyshev-II.",
   why="Mirror image of the lowpass asymptote: 20*n dB per decade.",
   source="Standard filter theory.")

_h("prompt.passive.ladder_highpass", FACT,
   "Higher orders use series capacitors along the path with shunt inductors to ground.",
   applies_when="Highpass spec: the passive option in the prompt.",
   valid_for="Passive LC high-pass ladders (the lowpass-to-highpass transformation).",
   why="The LP->HP transformation swaps series L for series C and shunt C for shunt L.",
   source="Standard filter theory.")

_h("prompt.passive.series_rlc_bandpass", FACT,
   "A series RLC band-pass puts L and C in series from 'in' to 'out' with a resistor "
   "from 'out' to ground. Centre f0 = 1 / (2*pi*sqrt(L*C)), -3dB bandwidth = "
   "R / (2*pi*L).",
   applies_when="Bandpass spec: the passive option in the prompt.",
   valid_for="R is the TOTAL series resistance of the loop: it includes the source "
             "resistance and the parts' own losses (inductor winding resistance), which "
             "widen the band and lower the peak below 0 dB.",
   why="Series resonance: X_L = X_C at f0; the half-power points are where the "
       "reactance equals R.",
   source="Circuit theory.")

_h("prompt.passive.series_rlc_notch", FACT,
   "A series-RLC notch puts a resistor R from 'in' to 'out' and a series L + C from "
   "'out' to ground (the LC branch is a short at resonance): f0 = 1 / (2*pi*sqrt(L*C)), "
   "and the notch is about R / (2*pi*L) wide, so a smaller R narrows it. Its depth is "
   "set by R against the branch's own loss resistance r - about 20*log10((R + r) / r) "
   "dB - so with real parts a smaller R also makes it shallower. An RC-only "
   "alternative is the twin-T.",
   applies_when="Notch spec: the passive option in the prompt.",
   valid_for="Ideal source and unloaded output. With ideal (lossless) parts r = 0 and "
             "the depth is unlimited; on a real technology r is mostly the inductor's "
             "winding resistance.",
   why="At f0 the branch impedance is just r, so the divider gives r / (R + r); the "
       "half-power points are where the branch reactance equals R. Replaces an earlier "
       "hint ('a smaller R gives a narrower, deeper notch') that was wrong about depth "
       "and made a live run cut R to 10 ohm and lose the notch (2026-11-19).",
   source="Circuit theory.")

# =====================================================================
# Prompt: active topology hints
# =====================================================================

_h("prompt.active.sallen_key_lowpass", FACT,
   "Unity-gain Sallen-Key low-pass (2nd order): R1 from 'in' to n1, R2 from n1 to n2, "
   "C2 from n2 to ground, C1 from n1 to 'out' (the feedback capacitor), and an op-amp "
   "follower (in+ = n2, in- = out, out = out). With R1 = R2 = R: "
   "f0 = 1/(2*pi*R*sqrt(C1*C2)) and Q = 0.5*sqrt(C1/C2), so Butterworth is C1 = 2*C2. "
   "Cascade stages (and/or a first-order RC) for higher orders.",
   applies_when="Lowpass spec with op-amps allowed: the active option in the prompt.",
   valid_for="An op-amp ideal at f0 (open-loop gain >> 1 there). A finite gain-"
             "bandwidth shifts f0 and Q; output impedance gives high-frequency "
             "feedthrough.",
   why="Standard unity-gain Sallen-Key equations for equal resistors.",
   source="Sallen & Key (1955); e.g. TI 'Analysis of the Sallen-Key Architecture' "
          "(SLOA024).")

_h("prompt.active.sallen_key_highpass", FACT,
   "Unity-gain Sallen-Key high-pass (2nd order): C1 from 'in' to n1, C2 from n1 to n2, "
   "R2 from n2 to ground, R1 from n1 to 'out', op-amp follower (in+ = n2, in- = out, "
   "out = out). With C1 = C2 = C: f0 = 1/(2*pi*C*sqrt(R1*R2)) and Q = 0.5*sqrt(R2/R1), "
   "so Butterworth is R1 = R2/2.",
   applies_when="Highpass spec with op-amps allowed: the active option in the prompt.",
   valid_for="An op-amp ideal over the passband; its gain-bandwidth eventually turns "
             "the high-pass into a band-pass.",
   why="Standard unity-gain Sallen-Key equations for equal capacitors.",
   source="Sallen & Key (1955).")

_h("prompt.active.mfb_bandpass", FACT,
   "Multiple-feedback (MFB) band-pass, inverting: R1 from 'in' to n1, R2 from n1 to "
   "ground, C1 from n1 to n2, C2 from n1 to 'out', R3 from n2 to 'out', op-amp in- = n2, "
   "in+ = ground ('0'), out = out. With C1 = C2 = C: f0 = 1/(2*pi*C) * "
   "sqrt((R1+R2)/(R1*R2*R3)), bandwidth = 1/(pi*R3*C), centre gain = R3/(2*R1).",
   applies_when="Bandpass spec with op-amps allowed: the active option in the prompt.",
   valid_for="An op-amp whose open-loop gain at f0 is well above 2*Q^2 (see "
             "prompt.tradeoff.opamp_gain); below that f0, bandwidth and gain all shift.",
   why="Standard MFB (Deliyannis) band-pass equations for equal capacitors.",
   source="Deliyannis (1968); e.g. TI 'Active Filter Design Techniques' (SLOD006).")

_h("prompt.active.notch_buffer", EXPLANATION,
   "An op-amp follower can buffer a passive notch (e.g. a twin-T) so that what follows "
   "does not load it, or feed the twin-T's common node back from the output to sharpen "
   "it.",
   applies_when="Notch spec with op-amps allowed: the active option in the prompt.",
   valid_for="Twin-T and similar passive notches; the bootstrapped twin-T trades depth "
             "stability for a narrower notch.",
   why="A buffer removes loading; positive feedback to the common node raises the "
       "notch Q.",
   source="Standard active-filter practice.")

# =====================================================================
# Prompt: surroundings, parts, trade-offs, construction
# =====================================================================

_h("prompt.termination.unterminated", FACT,
   "- SOURCE/LOAD: 'in' is driven by an ideal voltage source (0 ohm) and 'out' is not "
   "loaded. Textbook LC ladder values (g_k tables) assume resistive terminations R0 at "
   "the ends; without them an LC filter is undamped and peaks in the passband, so "
   "include your own terminating / damping resistance if you use such a table.",
   applies_when="The spec sets no source or load resistance (the default).",
   valid_for="Passive LC ladders; RC filters and active stages are not affected the "
             "same way.",
   why="The ladder's damping comes entirely from its terminations; without them the "
       "LC sections ring. A textbook 3rd-order ladder peaked +3.9 dB from an ideal "
       "source in the tests.",
   source="Filter tables are tabulated for terminated ladders.")

_h("prompt.termination.fixed", CATALOGUE,
   "- SOURCE/LOAD (fixed by the test harness, do NOT add them yourself): V1 drives 'in' "
   "through {src} source resistance, and 'out' is loaded by {load}. Design for these "
   "terminations - e.g. use them as R0 in ladder tables. Do not use the ids "
   "{source_id}/{load_id} or the node '{source_node}'. The passband level is whatever "
   "the source/load divider gives; the checks are relative to it.",
   applies_when="The spec sets a source and/or load resistance.",
   valid_for="Always: these are the spec's own numbers.",
   why="The harness inserts them; the model must design for them, not add them.",
   source="The spec (source_ohm, load_ohm).",
   example={"src": "50 ohm", "load": "a 1000 ohm resistor from 'out' to ground",
            "source_id": "R_SOURCE", "load_id": "R_LOAD", "source_node": "src_ext"})

_h("prompt.parts.passive_only", CATALOGUE,
   "- PARTS: resistors, capacitors and inductors only ({why}).",
   applies_when="Op-amps are not allowed: no rails, or --passive-only.",
   valid_for="Always: a property of the spec.",
   why="The model must know the allowed parts before it designs.",
   source="The spec (supply rails, allow_active).",
   example={"why": "no supply rails are provided"})

_h("prompt.parts.active", CATALOGUE,
   "- PARTS: resistors, capacitors, inductors AND op-amps. Neither passive nor active "
   "is preferred - choose by the requirements (see TRADE-OFFS).\n"
   "  Op-amps: the harness holds the supply rails at node '{vcc}' = {pos} V and node "
   "'{vee}' = {neg} V - connect op-amp supply pins to them, never add a supply source. "
   "An op-amp is {{\"id\": \"U1\", \"type\": \"opamp\", \"part\": <grade>, \"pins\": "
   "{{\"in+\": node, \"in-\": node, \"vcc\": \"{vcc}\", \"vee\": \"{vee}\", \"out\": "
   "node}}}}. Grades ({physics}; the supply rating applies whatever the technology):\n"
   "{grades}\n"
   "  The DC operating point is checked: every op-amp input needs a DC path (its bias "
   "current must flow somewhere), and the DC output must stay inside the part's swing, "
   "or the design fails.",
   applies_when="Rails are given and op-amps are allowed.",
   valid_for="Always: rails, grades and checks are the spec's and the library's.",
   why="Stated neutrally: with a one-sided prompt the model used an op-amp first in all "
       "nine powered live runs (2026-11-23), whatever the requirements said.",
   source="The spec (rails) and the op-amp library.",
   example={"vcc": "vcc", "vee": "vee", "pos": "12", "neg": "-12",
            "physics": "each op-amp is simulated as the grade you choose",
            "grades": "    * OPAMP_GP1: ..."})

_h("prompt.tradeoff.gain_needs_active", FACT,
   "a gain of {gain_db} dB needs an op-amp - passive parts cannot amplify",
   applies_when="TRADE-OFFS note; the spec asks for passband gain above 0 dB.",
   valid_for="Always: a passive network's voltage gain at its passband level cannot "
             "exceed 0 dB (resonant voltage step-up inside a high-Q network aside).",
   why="Passive parts only dissipate or store energy.",
   source="Circuit theory.",
   example={"gain_db": "6"})

_h("prompt.tradeoff.gain_either", FACT,
   "the gain asked for is reachable with either; a passive filter can only attenuate, "
   "and loses a little to its parts' own resistance",
   applies_when="TRADE-OFFS note; no gain above 0 dB is asked for.",
   valid_for="Always.",
   why="0 dB or less is reachable passively; real parts add series resistance.",
   source="Circuit theory.")

_h("prompt.tradeoff.load_isolation", EXPLANATION,
   "'out' drives a {load} ohm load: a passive filter's response depends on it, an "
   "op-amp output isolates the filter from it",
   applies_when="TRADE-OFFS note; the spec sets a load resistance.",
   valid_for="While the op-amp's output resistance is small against the load.",
   why="A passive network's transfer function includes its load; an op-amp output is "
       "(nearly) a voltage source.",
   source="Circuit theory.",
   example={"load": "1000"})

_h("prompt.tradeoff.inductor_size", DERIVED,
   "at {f} Hz an L-C filter at a 1 kohm impedance level needs about {l_1k}{q_part}",
   applies_when="TRADE-OFFS note (every powered spec).",
   valid_for="An order-of-magnitude estimate: L = R/(2*pi*f) at R = 1 kohm; a different "
             "impedance level scales L proportionally.",
   why="Lets the model see whether an inductor-based design is practical at this "
       "frequency before trying one.",
   source="Computed from the spec's target frequency.",
   example={"f": "50000", "l_1k": "3.18 mH", "q_part": ""})

_h("prompt.tradeoff.q_scaling", FACT,
   " (a series-RLC band-pass with Q = {q}: about {l_q}, since L scales with Q)",
   applies_when="Inside prompt.tradeoff.inductor_size for a bandpass spec.",
   valid_for="Series RLC at a fixed impedance level: Q = 2*pi*f0*L / R.",
   why="Narrow bands need large L at a given resistance.",
   source="Circuit theory.",
   example={"q": "10", "l_q": "31.8 mH"})

_h("prompt.tradeoff.inductor_limit", CATALOGUE,
   "; inductors are available only up to {limit} on {technology} (enforced)",
   applies_when="Inside prompt.tradeoff.inductor_size on a real technology.",
   valid_for="Always: the technology profile's inductor_range.",
   why="Larger parts do not exist in that construction; the check is enforced.",
   source="TechnologyProfile.inductor_range.",
   example={"limit": "10 mH", "technology": "smd_pcb"})

_h("prompt.tradeoff.inductor_any", CATALOGUE,
   "; inductors of any size are available on ideal technology",
   applies_when="Inside prompt.tradeoff.inductor_size on ideal technology.",
   valid_for="Ideal technology only.",
   why="The ideal profile has no availability limit.",
   source="TechnologyProfile (ideal).")

_h("prompt.tradeoff.budget", CATALOGUE,
   "at most {ma} mA may be drawn from each rail - op-amps consume their supply current "
   "whether or not they are needed; passive parts draw nothing",
   applies_when="TRADE-OFFS note; the spec sets a supply-current budget.",
   valid_for="Always: the spec's budget and the grades' catalogue currents.",
   why="Makes the cost of an op-amp visible before the design is made.",
   source="The spec (max_supply_current_a).",
   example={"ma": "1"})

_h("prompt.tradeoff.opamp_swing", FACT,
   "an op-amp output only swings within its rails",
   applies_when="TRADE-OFFS note (op-amps allowed).",
   valid_for="Always; how close to the rails is the grade's output headroom.",
   why="The output stage is powered from the rails.",
   source="Op-amp fundamentals.")

_h("prompt.tradeoff.opamp_gain", RULE_OF_THUMB,
   "its open-loop gain (GBW / frequency) should be well above the stage's own gain - "
   "for a high-Q stage well above 2*Q^2",
   applies_when="TRADE-OFFS note (op-amps allowed).",
   valid_for="Single-op-amp stages; the 2*Q^2 figure is the MFB band-pass requirement. "
             "Sallen-Key stages need less, multi-amplifier topologies differ. 'Well "
             "above' means roughly 10x for few-percent accuracy.",
   why="A finite open-loop gain shifts f0, Q and gain; the error grows as the margin "
       "shrinks.",
   source="Active-filter design notes (e.g. TI SLOD006, ch. 16).")

_h("prompt.tradeoff.passive_no_power", FACT,
   "passive parts need no power and have no swing limit",
   applies_when="TRADE-OFFS note (op-amps allowed).",
   valid_for="Always (within the parts' voltage ratings, which are not modelled).",
   why="R, L and C need no supply.",
   source="Circuit theory.")

_h("prompt.technology.srf", EXPLANATION,
   "An inductor behaves as a capacitor above its self-resonant frequency, so "
   "attenuation stops improving - and can get worse - beyond that point.",
   applies_when="Construction note on a real technology.",
   valid_for="Every real inductor; the frequency follows from its winding capacitance.",
   why="The winding capacitance resonates with the inductance; above that the "
       "capacitive path dominates.",
   source="TechnologyProfile.inductor_winding_c; any inductor datasheet's SRF.")

_h("prompt.technology.smaller_inductors", RULE_OF_THUMB,
   "prefer smaller inductances (which self-resonate higher) and keep the "
   "self-resonance well above the band you must attenuate.",
   applies_when="Construction note on a real technology.",
   valid_for="Inductor-based designs whose stopband reaches the inductors' "
             "self-resonance; irrelevant for RC and active designs.",
   why="SRF = 1/(2*pi*sqrt(L*Cw)) rises as L falls (Cw roughly constant per family).",
   source="Inductor application notes.")

_h("prompt.technology.value_ranges", CATALOGUE,
   " Parts available as {technology}: resistors {r}, capacitors {c} ({dielectric}), "
   "inductors {l}; a design that needs a value outside these fails.",
   applies_when="Construction note on a technology with value limits (every real one).",
   valid_for="Always: TechnologyProfile.resistor_range / capacitor_range / "
             "inductor_range.",
   why="Enforced by simulation/availability.py, so the model should know the ranges "
       "before it designs, not learn them from a failure.",
   source="TechnologyProfile (representative catalogue figures; capacitors limited to "
          "stable dielectrics, whose value does not move with voltage or temperature).",
   example={"technology": "smd_pcb", "r": "1 ohm to 10 Mohm", "c": "1 pF to 100 nF",
            "dielectric": "C0G/NP0 ceramic", "l": "1 nH to 10 mH"})

_h("prompt.series.required", CATALOGUE,
   "STANDARD VALUES (required, checked): {lines}. A value of any listed part type "
   "outside its series fails the design; types not listed may take any value.",
   applies_when="The spec sets value_series (--series).",
   valid_for="Always: enforced by simulation/availability.py on every technology.",
   why="The model must know the allowed values before it designs, not learn them "
       "from a failure.",
   source="DesignSpec.value_series and the IEC 60063 tables.",
   example={"lines": "resistors from E12: 1, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, "
                     "5.6, 6.8, 8.2 (times any power of ten)"})

_h("prompt.series.line", CATALOGUE,
   "{types} from {series}: {values} (times any power of ten)",
   applies_when="Inside prompt.series.required, once per series.",
   valid_for="Always: the series' published values.",
   why="States every allowed mantissa; E48 and E96 are not something to recall.",
   source="IEC 60063 (models/series.py).",
   example={"types": "resistors", "series": "E12",
            "values": "1, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2"})

_h("prompt.realistic_values", RULE_OF_THUMB,
   "keep component values realistic (resistors in ohms to megaohms, capacitors in pF "
   "to uF, inductors in uH to H).",
   applies_when="End of every prompt.",
   valid_for="Discrete-part designs; the enforced limits (inductance per technology) "
             "are stricter where they exist.",
   why="Values far outside these ranges are unavailable or dominated by parasitics.",
   source="Component availability in practice.")

# =====================================================================
# Verdict (simulation/verify.py)
# =====================================================================

_h("verdict.scale_factor", DERIVED,
   "to move the {what} onto target, multiply EVERY inductor and capacitor value by "
   "{k} and keep the resistors - that shifts the response by 1/{k} (exactly for ideal "
   "R, L and C; parasitics and op-amp limits do not scale with it)",
   applies_when="A frequency (cutoff, centre, notch) is outside its tolerance.",
   valid_for="Exact for ideal passive networks; on a real board or with op-amps the "
             "shift is close but not exact, since those effects do not scale.",
   why="Scaling every L and C by k maps H(f) to H(k*f): k = measured / target puts the "
       "feature on target.",
   source="Frequency scaling of RLC networks.",
   example={"what": "cutoff", "k": "0.601"})

_h("verdict.wrong_way", EXPLANATION,
   "Response rises with frequency - this looks like a {other}, not a {ftype}. Check the "
   "topology (which element is in series vs shunt to ground).",
   applies_when="A lowpass/highpass whose window edges have the wrong gain order.",
   valid_for="Always: measured on the response.",
   why="Swapped series/shunt elements turn a lowpass into a highpass and vice versa.",
   source="Measured.",
   example={"other": "highpass", "ftype": "lowpass"})

_h("verdict.no_crossing", EXPLANATION,
   "No usable -3dB crossing in the judged range ({window}): gain is {pb} dB in the "
   "passband and {far} dB at the far edge of that range. The cutoff may be outside it, "
   "or the circuit isn't attenuating enough to be a {ftype}.",
   applies_when="No -3 dB point, or under 10 dB of attenuation at the far edge.",
   valid_for="Always: measured.",
   why="Without a crossing there is no cutoff to judge.",
   source="Measured.",
   example={"window": "100 Hz-1 MHz", "pb": "0.0", "far": "-2.0", "ftype": "lowpass"})

_h("stopband.edge_shortfall", DERIVED,
   "(1) short by {short} dB at the stopband edge ({atten} dB at {edge} Hz): the rolloff "
   "is too shallow at the stopband edge, {decades} past the cutoff",
   applies_when="The stopband fails at its edge (not only deeper in the band).",
   valid_for="Always: measured.",
   why="An edge shortfall means the response does not fall fast enough between cutoff "
       "and edge.",
   source="Measured.",
   example={"short": "20.0", "atten": "20.0", "edge": "100000", "decades": "1.00 decade"})

_h("stopband.effective_order", DERIVED,
   "at the edge it rolls off like a ~{order}-order filter",
   applies_when="With stopband.edge_shortfall.",
   valid_for="The order of the Butterworth that would give the measured attenuation at "
             "the edge - a yardstick, not the design's actual order.",
   why="Puts the measured steepness in the same units as the suggestion that follows.",
   source="Measured attenuation, inverted through the Butterworth magnitude formula.",
   example={"order": "1.0"})

_h("stopband.butterworth_orders", FACT,
   "a Butterworth of order {n} gives {atten} dB there{lower}",
   applies_when="With stopband.edge_shortfall; n is the lowest order giving the "
                "requirement plus a 3 dB margin.",
   valid_for="Exact for Butterworth responses; other response types of the same order "
             "give more (Chebyshev, elliptic) or less (Bessel) at the edge.",
   why="|H|^2 = 1/(1 + (f/fc)^(2n)).",
   source="Butterworth magnitude formula.",
   example={"n": "3", "atten": "60.0", "lower": " (order 2 gives only 40.0 dB)"})

_h("stopband.rolloff_per_order", RULE_OF_THUMB,
   "each filter order adds about 20 dB per decade",
   applies_when="With stopband.edge_shortfall.",
   valid_for="All-pole responses well past the cutoff; not elliptic / Chebyshev-II.",
   why="The asymptote of an n-th order all-pole response is 20*n dB per decade.",
   source="Standard filter theory.")

_h("stopband.two_problems", FACT,
   "TWO separate stopband problems - both must be fixed, fixing one alone will not pass",
   applies_when="The stopband fails both at the edge and deeper in the band.",
   valid_for="Always: both are separate measured failures of one requirement.",
   why="Reporting only one made a live run chase the wrong fix (2026-11-12).",
   source="Measured.")

_h("stopband.recovers", EXPLANATION,
   "({n}) short by {short} dB, and NOT at the band edge: attenuation is {edge_atten} dB "
   "at {edge} Hz but degrades to {worst} dB at {worst_hz} Hz, so the response recovers "
   "inside the stopband instead of staying down - a resonance or a path around the "
   "filter; {tail}",
   applies_when="The worst stopband point is inside the band and worse than the edge.",
   valid_for="Always: measured. A recovering response can only come from a resonance "
             "or a path that bypasses the filter.",
   why="Distinguishes this from an edge shortfall, which needs a different fix.",
   source="Measured.",
   example={"n": "2", "short": "15.0", "edge_atten": "40.0", "edge": "100000",
            "worst": "25.0", "worst_hz": "100000000",
            "tail": "a separate problem from (1), located deeper in the band"})

_h("stopband.recovers_separate", FACT,
   "a separate problem from (1), located deeper in the band",
   applies_when="Tail of stopband.recovers when the edge is short as well.",
   valid_for="Always: measured.", why="Both failures are reported independently.",
   source="Measured.")

_h("stopband.recovers_edge_ok", FACT,
   "the edge itself meets the requirement",
   applies_when="Tail of stopband.recovers when the edge passes.",
   valid_for="Always: measured.", why="Tells the model the edge needs no change.",
   source="Measured.")

_h("verdict.peaking", EXPLANATION,
   "passband peaks {peak} dB above its level at {where} (limit {limit} dB) - a "
   "resonance that nothing damps enough; {tip}",
   applies_when="Passband gain rises more than max_peaking_db above its reference.",
   valid_for="Always: measured. The peak may come from an L-C section or from a high-Q "
             "active stage.",
   why="A live run passed with +12 dB of peaking before this check existed.",
   source="Measured.",
   example={"peak": "+12.4", "where": "6.31 kHz", "limit": "3", "tip": "..."})

_h("verdict.peaking_terminated", FACT,
   "check the element values against the fixed source/load resistances - ladder tables "
   "assume the terminations they were computed for",
   applies_when="Tip in verdict.peaking when the spec sets terminations.",
   valid_for="Terminated designs.",
   why="Tabulated ladder values only give a flat response at their own terminations.",
   source="Filter tables.")

_h("verdict.peaking_unterminated", FACT,
   "nothing damps it from outside: the source is ideal (0 ohm) and 'out' is unloaded, "
   "so the circuit's own resonances - an L-C section, or a high-Q active stage - need "
   "their own damping resistance or a lower Q",
   applies_when="Tip in verdict.peaking when the spec sets no terminations.",
   valid_for="Always for an ideal source and open output.",
   why="With no external resistance, all damping must come from inside the circuit.",
   source="Circuit theory.")

_h("verdict.gain_active_levers", FACT,
   "the gain is set by the gain-setting resistors (e.g. 1 + Rf/Rg for a non-inverting "
   "stage, R3/(2*R1) at the centre of an MFB band-pass, where R3 also sets the "
   "bandwidth) or by a source/load divider",
   applies_when="The passband-gain target fails and op-amps are allowed.",
   valid_for="The named topologies; the verdict does not know which one is used, so it "
             "names examples rather than claiming one.",
   why="Gives the levers without asserting which applies.",
   source="Standard op-amp stage equations.")

_h("verdict.gain_passive_levers", FACT,
   "with passive parts only the gain can be at most 0 dB, and what is lost goes to "
   "resistance in the signal path (series resistors, the inductors' winding resistance) "
   "or to a divider against what follows",
   applies_when="The passband-gain target fails and op-amps are not allowed.",
   valid_for="Passive networks.",
   why="Op-amp levers would be advice the model cannot act on (seen live, 2026-11-23).",
   source="Circuit theory.")

_h("verdict.gain_real_board", EXPLANATION,
   "on a real board parasitics can also erode it - see the diagnosis",
   applies_when="Appended to the gain levers on a real technology.",
   valid_for="Real technologies; the diagnosis then says whether they did.",
   why="A gain shortfall can come from stray capacitance on high-impedance nodes "
       "(seen live, 2026-11-23).",
   source="Measured by the diagnosis.")

_h("verdict.bandpass_shape", EXPLANATION,
   "Response is not band-pass shaped within the judged range: the edges are only "
   "{lo_rej} dB ({lo}) and {hi_rej} dB ({hi}) below the peak; both sides need to roll "
   "off by at least {need} dB",
   applies_when="A bandpass with less than 10 dB of rejection at a window edge.",
   valid_for="Always: measured inside the judged window.",
   why="A peak without rolloff on both sides is not a band-pass.",
   source="Measured.",
   example={"lo_rej": "53.0", "lo": "1 kHz", "hi_rej": "2.4", "hi": "10 MHz", "need": "10"})

_h("verdict.notch_symmetry", EXPLANATION,
   "gain differs between the low edge ({g_lo} dB at {lo}) and the high edge ({g_hi} dB "
   "at {hi}) of the judged range - a notch should pass both sides equally; this looks "
   "more like a lowpass/highpass",
   applies_when="A notch whose window edges differ by more than 3 dB.",
   valid_for="Always: measured inside the judged window.",
   why="A notch passes both sides; a difference means a slope, not a notch.",
   source="Measured.",
   example={"g_lo": "-0.4", "lo": "500 Hz", "g_hi": "-18.8", "hi": "100 MHz"})

# =====================================================================
# Diagnosis (simulation/diagnosis.py)
# =====================================================================

_h("parasitic.coupling.why", EXPLANATION,
   "signal reaches 'out' through stray capacitance from 'in', a path that goes around "
   "the filter rather than through it",
   applies_when="Input-output stray coupling is the limiting parasitic group.",
   valid_for="Technologies with input-output coupling (breadboard ~2 pF).",
   why="The coupling capacitance is a direct in-to-out path.",
   source="TechnologyProfile.io_coupling_c; measured by the diagnosis.")

_h("parasitic.coupling.remedy", RULE_OF_THUMB,
   "lower the high-frequency impedance at 'out' - end the filter in a shunt capacitor "
   "there (a ladder ending in a series inductor leaves 'out' with nothing but the load "
   "to ground), ideally several in parallel, since N in parallel divide their lead "
   "inductance by N",
   applies_when="After parasitic.coupling.why.",
   valid_for="Passive ladders and other filters whose output node is high-impedance at "
             "high frequency.",
   why="The coupled signal divides against the output node's impedance to ground.",
   source="Layout practice.")

_h("parasitic.winding_c.why", EXPLANATION,
   "an inductor becomes a capacitor above its self-resonance, and smaller inductors "
   "resonate higher",
   applies_when="Inductor winding capacitance is the limiting parasitic group.",
   valid_for="Real inductors.",
   why="SRF = 1/(2*pi*sqrt(L*Cw)).",
   source="TechnologyProfile.inductor_winding_c.")

_h("parasitic.winding_c.remedy", RULE_OF_THUMB,
   "lower the impedance level (smaller L with proportionally larger C and smaller R "
   "keeps the same cutoff) - but not so far that the parts' own series resistance (a "
   "few ohm for a leaded inductor) becomes comparable to it - or split one inductor "
   "into several smaller ones in series",
   applies_when="After parasitic.winding_c.why.",
   valid_for="Inductor-based designs; the series-split helps only while the smaller "
             "parts' winding capacitances do not add up in another path.",
   why="A smaller L self-resonates higher; impedance scaling keeps the response.",
   source="Inductor application notes.")

_h("parasitic.losses.why", EXPLANATION,
   "the parts' own series resistance is significant against the circuit's impedance "
   "level: an inductor's winding resistance grows with its inductance and adds to "
   "every resistor in series with it",
   applies_when="Losses (winding resistance, ESR, contact resistance) are the limiting "
                "group.",
   valid_for="Real technologies.",
   why="A 210 uH inductor's 4.35 ohm moved a 10 ohm design's cutoff from 10 to 6 kHz "
       "(live, 2026-11-19).",
   source="TechnologyProfile.inductor_dcr_per_sqrt_h.")

_h("parasitic.losses.remedy", RULE_OF_THUMB,
   "raise the impedance level (larger L, smaller C, larger R for the same cutoff) so "
   "those few ohms no longer matter, or account for them in the resistor values",
   applies_when="After parasitic.losses.why.",
   valid_for="Until the larger L runs into its self-resonance or the technology's "
             "inductance limit - the two remedies pull in opposite directions.",
   why="Fixed loss resistances matter less against a higher impedance level.",
   source="Circuit practice.")

_h("parasitic.esl.why", EXPLANATION,
   "a capacitor becomes an inductor above its self-resonance",
   applies_when="Capacitor ESL is the limiting group.",
   valid_for="Real capacitors.", why="ESL resonates with C.",
   source="TechnologyProfile.capacitor_esl.")

_h("parasitic.esl.remedy", RULE_OF_THUMB,
   "use several smaller capacitors in parallel instead of one large one",
   applies_when="After parasitic.esl.why.",
   valid_for="Shunt capacitors at high frequency.",
   why="Parallel parts divide the ESL; smaller C self-resonates higher.",
   source="Decoupling practice.")

_h("parasitic.leads.why", EXPLANATION,
   "every connection adds series inductance, which spoils shunt capacitors at high "
   "frequency",
   applies_when="Lead / interconnect inductance is the limiting group.",
   valid_for="Real technologies.", why="Lead inductance is in series with each part.",
   source="TechnologyProfile.interconnect_l_per_terminal.")

_h("parasitic.leads.remedy", RULE_OF_THUMB,
   "parallel shunt paths divide it",
   applies_when="After parasitic.leads.why.",
   valid_for="Shunt paths; series paths add their lead inductance.",
   why="N equal parallel paths divide the inductance by N.",
   source="Layout practice.")

_h("parasitic.node_c.why", EXPLANATION,
   "every node has stray capacitance to ground (and resistors have some across them), "
   "which loads HIGH-IMPEDANCE nodes, and a high-Q stage magnifies it",
   applies_when="Stray node capacitance is the limiting group.",
   valid_for="Real technologies.",
   why="A 530 kohm node lost 3 dB of gain to 0.3 pF at 50 kHz in a Q = 10 MFB (live, "
       "2026-11-23).",
   source="TechnologyProfile.interconnect_node_c / resistor_shunt_c.")

_h("parasitic.node_c.remedy", RULE_OF_THUMB,
   "nodes at hundreds of kilohms are the ones affected - Lower the impedance level, "
   "smaller resistors with proportionally larger capacitors keep the same frequencies",
   applies_when="After parasitic.node_c.why.",
   valid_for="Where the stray capacitance's reactance at the frequency of interest is "
             "within ~100x of the node impedance.",
   why="Stray C matters once 1/(2*pi*f*C) approaches the node impedance.",
   source="Circuit practice.")

_h("diag.grade.faster", CATALOGUE,
   "faster grades rated for {rails}{within}: {names}",
   applies_when="The per-component check names an op-amp and faster grades fit the "
                "rails and budget.",
   valid_for="Always: the library's ratings against the spec.",
   why="Only grades the model can actually use are named.",
   source="Op-amp library (gbw_hz, supply range, quiescent_a).",
   example={"rails": "2.5 V / -2.5 V", "within": "", "names": "OPAMP_RR10 (10 MHz)"})

_h("diag.grade.less_q_otherwise", RULE_OF_THUMB,
   "otherwise less gain or Q per stage",
   applies_when="After diag.grade.faster.",
   valid_for="Single-stage active filters limited by open-loop gain.",
   why="The required open-loop gain grows with the stage's gain and Q.",
   source="Active-filter design notes.")

_h("diag.grade.none", CATALOGUE,
   "no grade faster than {part} ({gbw} MHz) is rated for {rails}{within}",
   applies_when="The per-component check names an op-amp and no faster grade fits.",
   valid_for="Always: the library's ratings against the spec.",
   why="'Choose a faster grade' with none available sent a live run (2026-11-23) off "
       "to a passive design it could not finish.",
   source="Op-amp library.",
   example={"part": "OPAMP_FET3", "gbw": "3", "rails": "12 V / -12 V", "within": ""})

_h("diag.grade.less_q", RULE_OF_THUMB,
   "lower the gain or Q per stage - e.g. split it into two lower-Q stages - or use a "
   "design that needs no op-amp",
   applies_when="After diag.grade.none.",
   valid_for="Op-amp-limited high-Q or high-gain stages.",
   why="Lower Q per stage lowers the open-loop gain each stage needs.",
   source="Active-filter design notes.")

_h("diag.ideal_pass_active", DERIVED,
   "With ideal parts - ideal op-amps included - this exact design would PASS, so the "
   "failure comes from the real parts: the board's parasitics or the chosen op-amp "
   "grade's own limits (gain-bandwidth, output resistance, bias current); the "
   "per-component check below says which",
   applies_when="Diagnosis: ideal parts pass and the circuit has op-amps.",
   valid_for="Always: from the ideal re-simulation.",
   why="Separates construction from design.", source="Measured (ideal re-simulation).")

_h("diag.ideal_pass", DERIVED,
   "With ideal parts this exact design would PASS, so the failure is caused by the "
   "construction parasitics, not by the design itself",
   applies_when="Diagnosis: ideal parts pass, passive circuit.",
   valid_for="Always: from the ideal re-simulation.",
   why="Separates construction from design.", source="Measured (ideal re-simulation).")

_h("diag.design_only", DERIVED,
   "The {checks} failure is in the design itself - ideal parts fail it too - so fix "
   "that first (see above); parasitics are not the cause",
   applies_when="Diagnosis: every failed check also fails with ideal parts.",
   valid_for="Always: from the ideal re-simulation.",
   why="Keeps parasitics from being blamed for design problems.",
   source="Measured (ideal re-simulation).", example={"checks": "peaking"})

_h("diag.mixed", DERIVED,
   "Two kinds of failure: {design} fails even with ideal parts (a design problem, see "
   "above), while {built} fails only because of the construction parasitics",
   applies_when="Diagnosis: some failed checks are design, some construction.",
   valid_for="Always: from the ideal re-simulation.",
   why="Each kind needs a different fix.", source="Measured (ideal re-simulation).",
   example={"design": "bandwidth", "built": "passband gain"})

_h("diag.built_only", DERIVED,
   "The {built} failure is caused by the construction parasitics: ideal parts pass "
   "it{ideal_only}",
   applies_when="Diagnosis: the failed checks pass with ideal parts, though ideal parts "
                "fail some other check.",
   valid_for="Always: from the ideal re-simulation.",
   why="Replaces a confusing 'ideal parts would still fail a check' opening.",
   source="Measured (ideal re-simulation).",
   example={"built": "passband gain", "ideal_only": ""})

_h("diag.ideal_only", DERIVED,
   " (they would miss {checks} instead, which this design passes as built - mind it "
   "when changing values)",
   applies_when="Inside diag.built_only when ideal parts fail another check.",
   valid_for="Always: from the ideal re-simulation.",
   why="The parasitics happen to help that check.", source="Measured.",
   example={"checks": "frequency"})

_h("diag.per_component.fixer", DERIVED,
   "Per component (each made ideal on its own, the rest as built): {labels}{verb}",
   applies_when="Diagnosis level 'full': making one part ideal makes the design pass.",
   valid_for="Always: from the per-component re-simulations.",
   why="Names the part the model can change.", source="Measured.",
   example={"labels": "L1 (100u)",
            "verb": " alone would make the design PASS - that is the part to change"})

_h("diag.per_component.partial", DERIVED,
   "Per component (each made ideal on its own, the rest as built): {items}; no single "
   "part fixes it alone",
   applies_when="Diagnosis level 'full': parts recover margin but none passes alone.",
   valid_for="Always: measured.", why="Ranks the parts by what they cost.",
   source="Measured.", example={"items": "R3 (530k) +3.0 dB"})

_h("diag.per_component.none", DERIVED,
   "Per component: no single part, made ideal on its own, recovers more than {limit} "
   "{unit} - the damage comes from several parts or the board's parasitics together",
   applies_when="Diagnosis level 'full': no part recovers a meaningful margin alone.",
   valid_for="Always: measured. Replaces 'the cause is the layout as a whole', an "
             "inference stated as fact.",
   why="Says what was measured without guessing the mechanism.", source="Measured.",
   example={"limit": "1", "unit": "dB"})

_h("diag.srf_below_stopband", DERIVED,
   "below the stopband: {above} across the whole stopband",
   applies_when="A part's computed self-resonance lies below a lowpass stopband.",
   valid_for="Computed from the part value and the technology profile.",
   why="The part does not behave as its type anywhere in the stopband.",
   source="TechnologyProfile; the part's value.", example={"above": "acts as a capacitor"})

_h("diag.srf_inside", DERIVED,
   "inside the {where}: {above} above it",
   applies_when="A part's computed self-resonance lies inside the judged band or "
                "stopband.",
   valid_for="Computed from the part value and the technology profile.",
   why="Above it the part behaves as the opposite reactance.",
   source="TechnologyProfile; the part's value.",
   example={"where": "stopband", "above": "acts as a capacitor"})

# =====================================================================
# Bias, budget, availability (simulation/bias.py, availability.py)
# =====================================================================

_h("bias.saturated", DERIVED,
   "its output sits at {v_out} V at DC, at its swing limit ({lo} .. {hi} V): it is "
   "SATURATED, so the AC response describes a circuit that would not work",
   applies_when="An op-amp's DC output is within 0.1 V of its swing limit.",
   valid_for="Always: from the operating point and the grade's (or the ideal part's) "
             "swing.",
   why="An AC sweep linearises around the bias point and looks plausible even then.",
   source="Measured (operating point).",
   example={"v_out": "-10.53", "lo": "-10.50", "hi": "+10.50"})

_h("bias.saturated_causes", RULE_OF_THUMB,
   "the usual causes are an input with no DC path, no DC negative feedback (a "
   "capacitor-only feedback path leaves the full open-loop gain at DC), or positive "
   "feedback",
   applies_when="After bias.saturated.",
   valid_for="Linear op-amp stages; comparators and oscillators saturate by design.",
   why="These are the common ways a linear stage loses its DC operating point.",
   source="Op-amp application practice.")

_h("bias.no_dc_path", EXPLANATION,
   "{pin} (node '{node}') has no DC path - only capacitors reach it - so the input bias "
   "current{bias} has nowhere to go; add a resistor from it to ground or to the signal "
   "path",
   applies_when="An op-amp input node has no resistive/inductive path to a held node.",
   valid_for="Every technology: even an ideal input leaves the node's DC level "
             "undefined.",
   why="Found by a DC-path graph of the circuit.",
   source="Circuit analysis.",
   example={"pin": "in+", "node": "n1", "bias": " (5e-08 A)"})

_h("bias.supply_rating", CATALOGUE,
   "{part} is rated for {lo}-{hi} V total supply, the rails give {total} V - choose "
   "another grade",
   applies_when="The rails are outside the grade's rated total supply.",
   valid_for="Every technology: a catalogue property of the part.",
   why="An ideal simulation of a part the rails would destroy is not a design.",
   source="Op-amp library (supply_min_v / supply_max_v).",
   example={"part": "OPAMP_RR10", "lo": "2.7", "hi": "5.5", "total": "24"})

_h("bias.pins_not_on_rails", FACT,
   "its supply pins go to '{vcc}'/'{vee}', not to the rails '{rail_p}'/'{rail_n}'",
   applies_when="An op-amp's supply pins are not wired to vcc / vee.",
   valid_for="Always.", why="The part would be unpowered or wrongly powered.",
   source="The circuit as proposed.",
   example={"vcc": "vcc", "vee": "0", "rail_p": "vcc", "rail_n": "vee"})

_h("bias.input_range", DERIVED,
   "{pin} sits at {v} V, outside the input range {lo} .. {hi} V",
   applies_when="An op-amp input's DC level is outside its common-mode range.",
   valid_for="Always: from the operating point and the grade's input headroom.",
   why="Outside its range the input stage stops working.", source="Measured.",
   example={"pin": "in+", "v": "-10.63", "lo": "-10.50", "hi": "+10.50"})

_h("bias.no_operating_point", RULE_OF_THUMB,
   "the DC operating point could not be solved, so no bias point exists for the active "
   "parts - the usual causes are a floating node or a loop with no DC feedback",
   applies_when="ngspice produced no operating point for an active circuit.",
   valid_for="Circuits with op-amps.",
   why="Those are the common reasons a DC solution does not exist.",
   source="SPICE practice.")

_h("budget.over", DERIVED,
   "draws {ma} mA from the {rail} rail (limit {limit} mA){parts}",
   applies_when="The current drawn from a rail exceeds max_supply_current_a.",
   valid_for="Measured on real technologies; on ideal technology the grades' catalogue "
             "currents are added.",
   why="Makes a power budget bind on the grade choice.",
   source="Measured (operating point) and the op-amp library.",
   example={"ma": "1.40", "rail": "+12 V", "limit": "1",
            "parts": "; op-amps alone: U1 (OPAMP_FET3) 1.4 mA"})

_h("budget.levers", FACT,
   "use a lower-power grade, fewer op-amps, higher-value resistors between the rails, "
   "or a passive design",
   applies_when="After budget.over.",
   valid_for="Always: these are what draws current from the rails.",
   why="Each one reduces the measured rail current.", source="Circuit theory.")

_h("availability.too_large", CATALOGUE,
   "{id} = {value} exceeds the {limit} available on {technology}",
   applies_when="A resistor, capacitor or inductor is above its type's range on the "
                "spec's technology.",
   valid_for="Always: the technology profile's range for that part type.",
   why="A response achieved with a part that does not exist is not a design.",
   source="TechnologyProfile.resistor_range / capacitor_range / inductor_range.",
   example={"id": "L1", "value": "22 mH", "limit": "10 mH", "technology": "smd_pcb"})

_h("availability.too_small", CATALOGUE,
   "{id} = {value} is below the {limit} available on {technology}",
   applies_when="A resistor, capacitor or inductor is below its type's range on the "
                "spec's technology.",
   valid_for="Always: the technology profile's range for that part type.",
   why="A response achieved with a part that does not exist is not a design.",
   source="TechnologyProfile.resistor_range / capacitor_range / inductor_range.",
   example={"id": "C1", "value": "0.5 pF", "limit": "1 pF", "technology": "smd_pcb"})

_h("availability.levers.inductor_smaller", FACT,
   "use a smaller inductor (raise the capacitance, or lower the impedance level) or a "
   "design without it",
   applies_when="After availability.too_large for an inductor.",
   valid_for="Always: L at a given frequency falls as C or the impedance level does.",
   why="f0 = 1/(2*pi*sqrt(L*C)); impedance scaling moves L and R together.",
   source="Circuit theory.")

_h("availability.levers.inductor_larger", FACT,
   "use a larger inductor (lower the capacitance, or raise the impedance level)",
   applies_when="After availability.too_small for an inductor.",
   valid_for="Always: L at a given frequency rises as C falls or the impedance level "
             "rises.",
   why="f0 = 1/(2*pi*sqrt(L*C)); impedance scaling moves L and R together.",
   source="Circuit theory.")

_h("availability.levers.capacitor_smaller", FACT,
   "use a smaller capacitor (raise the inductance or resistance it works with, or "
   "raise the impedance level)",
   applies_when="After availability.too_large for a capacitor.",
   valid_for="Always: at a fixed frequency C falls as the R or L it is paired with "
             "rises (f = 1/(2*pi*R*C), 1/(2*pi*sqrt(L*C))).",
   why="Those products set the frequency; only the split between the parts is free.",
   source="Circuit theory.")

_h("availability.levers.capacitor_larger", FACT,
   "use a larger capacitor (lower the inductance or resistance it works with, or "
   "lower the impedance level)",
   applies_when="After availability.too_small for a capacitor.",
   valid_for="Always: at a fixed frequency C rises as the R or L it is paired with "
             "falls.",
   why="Those products set the frequency; only the split between the parts is free.",
   source="Circuit theory.")

_h("availability.levers.resistor_smaller", FACT,
   "use a smaller resistor (lower the impedance level)",
   applies_when="After availability.too_large for a resistor.",
   valid_for="Always: scaling the impedance level down lowers every R.",
   why="See availability.impedance_scaling.",
   source="Circuit theory.")

_h("availability.levers.resistor_larger", FACT,
   "use a larger resistor (raise the impedance level)",
   applies_when="After availability.too_small for a resistor.",
   valid_for="Always: scaling the impedance level up raises every R.",
   why="See availability.impedance_scaling.",
   source="Circuit theory.")

_h("availability.not_in_series", CATALOGUE,
   "{id} = {value} is not an {series} value; nearest {below} ({below_pct}) and "
   "{above} ({above_pct})",
   applies_when="The spec requires a value series for this part type "
                "(DesignSpec.value_series) and the part's value is not in it.",
   valid_for="Always: membership is a table lookup within 0.1 % of the value.",
   why="A response achieved with a value nobody sells is not a design. The two "
       "neighbours are stated so the model knows its real choices; the harness never "
       "picks one or simulates it.",
   source="IEC 60063 tables (models/series.py).",
   example={"id": "R1", "value": "1.59k", "series": "E24", "below": "1.5 kohm",
            "below_pct": "-5.7%", "above": "1.6 kohm", "above_pct": "+0.6%"})

_h("availability.impedance_scaling", FACT,
   "impedance scaling (every R and L times k, every C divided by k) leaves the "
   "transfer function unchanged - exactly for ideal R, L, C and op-amps, and only "
   "while the spec sets no source or load resistance, since those stay fixed",
   applies_when="After any availability violation, to explain 'impedance level'.",
   valid_for="Exact for ideal parts with no fixed terminations; parasitics and the "
             "spec's source/load resistances do not scale, so a real design moves.",
   why="Every impedance in the network is multiplied by k, so all voltage ratios stay "
       "the same.",
   source="Circuit theory (impedance scaling).")

# =====================================================================
# Loop (agent/loop.py)
# =====================================================================

_h("loop.repeated_design", FACT,
   "REPEATED DESIGN: this circuit is identical to iteration {n} (only ids, order or "
   "title may differ), so it was not simulated again. That attempt failed with: "
   "{message} Submitting it again cannot pass - change the design.",
   applies_when="A proposal equals an earlier one by design_key.",
   valid_for="Always: an identical circuit simulates identically.",
   why="A live run resubmitted the same bandpass three times (2026-11-19).",
   source="The run's own history.",
   example={"n": "2", "message": "Simulated bandpass: ... FAIL."})

_h("loop.reject_ideal_opamp", CATALOGUE,
   "Circuit definition error: op-amp part(s) {parts} are not grades you can choose; "
   "pick one of {grades} (how ideal the op-amp behaves follows the technology, not the "
   "part name).",
   applies_when="The model names a part outside SELECTABLE_OPAMPS.",
   valid_for="Always.",
   why="Offered next to real grades, a model picks perfection (all four live runs, "
       "2026-11-23).",
   source="Op-amp library (SELECTABLE_OPAMPS).",
   example={"parts": "['OPAMP_IDEAL']", "grades": "['OPAMP_GP1', 'OPAMP_FET3']"})

_h("loop.reject_passive_only", CATALOGUE,
   "Circuit definition error: this spec allows passive parts only (resistors, "
   "capacitors, inductors) - no op-amps.",
   applies_when="The model uses an op-amp although the spec allows passive parts only.",
   valid_for="Always: a property of the spec.",
   why="--passive-only keeps rails in the spec but forbids active parts.",
   source="The spec (allow_active).")


# ---------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------

HINTS: dict[str, Hint] = {}
for _hint in _HINTS:
    if _hint.id in HINTS:
        raise ValueError(f"duplicate hint id {_hint.id!r}")
    HINTS[_hint.id] = _hint


def render(hint_id: str, capital: bool = False, **values) -> str:
    """The text of hint `hint_id` with `values` filled in; rules of thumb come
    prefixed with 'rule of thumb: ' (capitalised with capital=True)."""
    return HINTS[hint_id].render(capital=capital, **values)


# ---------------------------------------------------------------------
# HINTS.md
# ---------------------------------------------------------------------

_KIND_TITLES = {
    FACT: "fact (exact maths or physics for the stated topology)",
    DERIVED: "derived (computed from a measurement of this design)",
    CATALOGUE: "catalogue (library, technology profile or spec)",
    EXPLANATION: "explanation (physical meaning of a measurement)",
    RULE_OF_THUMB: "rule of thumb (guidance, always labelled as such)",
}


def markdown() -> str:
    """HINTS.md, generated from the catalogue."""
    lines = [
        "# Hint catalogue",
        "",
        "Generated from `src/spice_agent/hints.py` by `python -m spice_agent.hints` - "
        "edit the catalogue, not this file.",
        "",
        "Every piece of advice or explanation the harness gives the model, with when it "
        "applies, where it stops being valid, why it holds and where it comes from. The "
        "rule behind it is in ARCHITECTURE.md: feedback conveys hard facts only, and "
        "rules of thumb are always labelled.",
        "",
        "| Kind | Count |",
        "|---|---|",
    ]
    for kind in KINDS:
        n = sum(1 for h in HINTS.values() if h.kind == kind)
        lines.append(f"| {_KIND_TITLES[kind]} | {n} |")
    section = None
    for h in HINTS.values():
        top = h.id.split(".")[0]
        if top != section:
            section = top
            lines += ["", f"## {top}", ""]
        sample = h.render(**h.example) if h.example or "{" not in h.text else h.text
        lines += [
            f"### `{h.id}` - {h.kind.replace('_', ' ')}",
            "",
            "> " + sample.replace("\n", "\n> "),
            "",
            f"- **Applies when:** {h.applies_when}",
            f"- **Valid for:** {h.valid_for}",
            f"- **Why:** {h.why}",
            f"- **Source:** {h.source}",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Write HINTS.md from the hint catalogue.")
    p.add_argument("-o", "--output", default="HINTS.md")
    a = p.parse_args(argv)
    Path(a.output).write_text(markdown(), encoding="utf-8")
    print(f"Wrote {a.output} ({len(HINTS)} hints)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
