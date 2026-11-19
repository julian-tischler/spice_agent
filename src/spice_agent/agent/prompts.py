"""
Building the design brief the model receives.

Pure string construction from a DesignSpec - no API calls, no state - so
the exact text sent can be asserted in a test.
"""

from spice_agent.agent.analysis import analysis_for
from spice_agent.circuit import LOAD_ID, SOURCE_ID, SOURCE_NODE, get_profile
from spice_agent.simulation import DesignSpec


# Short topology reminders per filter type so the model starts from a
# sensible structure instead of re-deriving it. Values are for it to work out.
TOPOLOGY_HINTS = {
    "lowpass": (
        "A 1st-order RC low-pass is a series resistor from 'in' to 'out' "
        "and a capacitor from 'out' to ground: fc = 1 / (2*pi*R*C). "
        "ORDER SETS THE ROLLOFF: an n-th order filter falls off about "
        "20*n dB per decade past the cutoff, so if a stopband attenuation "
        "is specified, choose the order from it first and only then pick "
        "component values. Higher orders are built as an LC ladder: series "
        "inductors along the signal path from 'in' to 'out' with shunt "
        "capacitors to ground between them (L-C = 2nd order, C-L-C or "
        "L-C-L = 3rd, and so on). For a Butterworth (maximally flat) "
        "response of order n with cutoff fc into a reference impedance R0, "
        "the normalised element values are g_k = 2*sin((2k-1)*pi/(2n)), "
        "scaled as L_k = g_k*R0/(2*pi*fc) and C_k = g_k/(2*pi*fc*R0)."
    ),
    "highpass": (
        "A 1st-order RC high-pass is a series capacitor from 'in' to 'out' "
        "and a resistor from 'out' to ground: fc = 1 / (2*pi*R*C). "
        "ORDER SETS THE ROLLOFF: an n-th order high-pass rises at about "
        "20*n dB per decade below the cutoff, so pick the order from any "
        "stopband requirement first. Higher orders use series capacitors "
        "along the path with shunt inductors to ground."
    ),
    "bandpass": (
        "A series RLC band-pass puts L and C in series from 'in' to 'out' "
        "with a resistor from 'out' to ground. Centre f0 = 1 / (2*pi*sqrt(L*C)), "
        "-3dB bandwidth = R / (2*pi*L)."
    ),
    "notch": (
        "A series-RLC notch puts a resistor from 'in' to 'out' and a "
        "series L + C from 'out' to ground (the LC tank is a short at "
        "resonance): f0 = 1 / (2*pi*sqrt(L*C)). A smaller R gives a "
        "narrower, deeper notch. An RC-only alternative is the twin-T."
    ),
}


def build_termination_note(spec: DesignSpec) -> str:
    """
    What surrounds the filter. The harness, not the model, owns the source
    and load: with none set the source is ideal and 'out' is unloaded, and
    the ladder tables in the hint would give an undamped, peaking filter -
    so the model is told so rather than left to find out from the verdict.
    """
    if not spec.terminated:
        return (
            "- SOURCE/LOAD: 'in' is driven by an ideal voltage source (0 ohm) and "
            "'out' is not loaded. Textbook LC ladder values (g_k tables) assume "
            "resistive terminations R0 at the ends; without them an LC filter is "
            "undamped and peaks in the passband, so include your own terminating / "
            "damping resistance if you use such a table.\n"
        )
    src = f"{spec.source_ohm:g} ohm" if spec.source_ohm > 0 else "an ideal (0 ohm)"
    load = (f"a {spec.load_ohm:g} ohm resistor from 'out' to ground"
            if spec.load_ohm is not None else "nothing (unloaded)")
    return (
        f"- SOURCE/LOAD (fixed by the test harness, do NOT add them yourself): "
        f"V1 drives 'in' through {src} source resistance, and 'out' is loaded by "
        f"{load}. Design for these terminations - e.g. use them as R0 in ladder "
        f"tables. Do not use the ids {SOURCE_ID}/{LOAD_ID} or the node "
        f"'{SOURCE_NODE}'. The passband level is whatever the source/load divider "
        f"gives; the checks are relative to it.\n"
    )


def build_technology_note(spec: DesignSpec) -> str:
    """
    Tell the model how the circuit will be built, when that matters.

    This mirrors real practice: a designer does not pick L and C from a
    formula alone, they pick parts whose parasitics do not ruin the band
    of interest. The usual rule of thumb is to keep an inductor's
    self-resonant frequency well clear of the highest frequency that
    matters - so the model needs to know what it is building on.
    """
    profile = get_profile(spec.technology)
    if profile.is_ideal:
        return ""
    return (
        f"- CONSTRUCTION: this will be built as {profile.name}. "
        f"{profile.describe()}\n"
        f"  Components are therefore NOT ideal, and the simulation includes "
        f"these parasitics. An inductor behaves as a capacitor above its "
        f"self-resonant frequency, so attenuation stops improving - and can "
        f"get worse - beyond that point. Prefer smaller inductances (which "
        f"self-resonate higher) and keep the self-resonance well above the "
        f"band you must attenuate."
    )


def build_system_prompt(spec: DesignSpec) -> str:
    analysis = analysis_for(spec)
    return f"""You are an analog circuit design agent. Design a passive filter \
(resistors, capacitors, inductors only) meeting this spec:

- {spec.describe()}
- {"Do not add loss beyond the given source/load divider" if spec.terminated else "Unity passband gain (0 dB) — do not add a resistive divider in the passband"}
- Topology: single voltage source 'V1' (nodes ['in','0'], value 'AC 1'), \
then passives forming the filter from node 'in' to node 'out' (node '0' is ground)
- The simulation sweeps {analysis['fstart']} Hz to {analysis['fstop']}; \
the target frequency must land well inside that range.
- Judged from {spec.judge_window()[0]:g} Hz to {spec.judge_window()[1]:g} Hz: \
every requirement is checked inside that range; the response outside it is \
reported to you but cannot fail the design.
{build_termination_note(spec)}{build_technology_note(spec)}
Hint: {TOPOLOGY_HINTS[spec.filter_type]}

Call the simulate_circuit tool with your proposed component values. You'll \
get back the ACTUAL simulated response from ngspice — not a guess. If it \
doesn't pass, revise your design based on the real result and try again. \
The result's message says what failed and how to fix it; act on its concrete \
numbers (value scale factor, suggested order, limiting parasitic) rather than \
guessing. Keep component values realistic (resistors in ohms to megaohms, \
capacitors in pF to uF, inductors in uH to H)."""
