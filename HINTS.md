# Hint catalogue

Generated from `src/spice_agent/hints.py` by `python -m spice_agent.hints` - edit the catalogue, not this file.

Every piece of advice or explanation the harness gives the model, with when it applies, where it stops being valid, why it holds and where it comes from. The rule behind it is in ARCHITECTURE.md: feedback conveys hard facts only, and rules of thumb are always labelled.

| Kind | Count |
|---|---|
| fact (exact maths or physics for the stated topology) | 27 |
| derived (computed from a measurement of this design) | 18 |
| catalogue (library, technology profile or spec) | 13 |
| explanation (physical meaning of a measurement) | 17 |
| rule of thumb (guidance, always labelled as such) | 16 |

## prompt

### `prompt.passive.rc_lowpass` - fact

> A 1st-order RC low-pass is a series resistor from 'in' to 'out' and a capacitor from 'out' to ground: fc = 1 / (2*pi*R*C).

- **Applies when:** Lowpass spec: the passive option in the prompt.
- **Valid for:** Ideal source and unloaded output. A source resistance adds to R; a load forms a divider and moves fc.
- **Why:** The RC divider's pole is at 1/(2*pi*R*C).
- **Source:** Circuit theory.

### `prompt.order_rolloff_lowpass` - rule of thumb

> rule of thumb: ORDER SETS THE ROLLOFF: an n-th order filter falls off about 20*n dB per decade past the cutoff, so if a stopband attenuation is specified, choose the order from it first and only then pick component values.

- **Applies when:** Lowpass spec: the passive option in the prompt.
- **Valid for:** All-pole responses (Butterworth, Bessel, Chebyshev-I) well past the cutoff. NOT elliptic / Chebyshev-II, whose transmission zeros beat it near the edge; and not close to the cutoff, where the asymptote is not reached yet.
- **Why:** An n-th order all-pole response falls as 1/f^n far from the cutoff, i.e. 20*n dB per decade.
- **Source:** Standard filter theory (e.g. Zverev, Handbook of Filter Synthesis).

### `prompt.passive.lc_ladder_lowpass` - fact

> Higher orders are built as an LC ladder: series inductors along the signal path from 'in' to 'out' with shunt capacitors to ground between them (L-C = 2nd order, C-L-C or L-C-L = 3rd, and so on). For a Butterworth (maximally flat) response of order n with cutoff fc into a reference impedance R0, the normalised element values are g_k = 2*sin((2k-1)*pi/(2n)), scaled as L_k = g_k*R0/(2*pi*fc) and C_k = g_k/(2*pi*fc*R0).

- **Applies when:** Lowpass spec: the passive option in the prompt.
- **Valid for:** Doubly terminated in equal resistances R0 (source and load). With an ideal source and open output (the default spec) the same values give an undamped, peaking filter - the termination note says so.
- **Why:** The g_k are the Butterworth prototype element values; impedance and frequency scaling give L_k, C_k.
- **Source:** Standard filter tables (e.g. Williams & Taylor, Electronic Filter Design Handbook).

### `prompt.passive.rc_highpass` - fact

> A 1st-order RC high-pass is a series capacitor from 'in' to 'out' and a resistor from 'out' to ground: fc = 1 / (2*pi*R*C).

- **Applies when:** Highpass spec: the passive option in the prompt.
- **Valid for:** Ideal source and unloaded output (a load appears in parallel with R).
- **Why:** The CR divider's zero-pole pair puts the -3 dB point at 1/(2*pi*R*C).
- **Source:** Circuit theory.

### `prompt.order_rolloff_highpass` - rule of thumb

> rule of thumb: ORDER SETS THE ROLLOFF: an n-th order high-pass rises at about 20*n dB per decade below the cutoff, so pick the order from any stopband requirement first.

- **Applies when:** Highpass spec: the passive option in the prompt.
- **Valid for:** All-pole responses well below the cutoff; not elliptic / Chebyshev-II.
- **Why:** Mirror image of the lowpass asymptote: 20*n dB per decade.
- **Source:** Standard filter theory.

### `prompt.passive.ladder_highpass` - fact

> Higher orders use series capacitors along the path with shunt inductors to ground.

- **Applies when:** Highpass spec: the passive option in the prompt.
- **Valid for:** Passive LC high-pass ladders (the lowpass-to-highpass transformation).
- **Why:** The LP->HP transformation swaps series L for series C and shunt C for shunt L.
- **Source:** Standard filter theory.

### `prompt.passive.series_rlc_bandpass` - fact

> A series RLC band-pass puts L and C in series from 'in' to 'out' with a resistor from 'out' to ground. Centre f0 = 1 / (2*pi*sqrt(L*C)), -3dB bandwidth = R / (2*pi*L).

- **Applies when:** Bandpass spec: the passive option in the prompt.
- **Valid for:** R is the TOTAL series resistance of the loop: it includes the source resistance and the parts' own losses (inductor winding resistance), which widen the band and lower the peak below 0 dB.
- **Why:** Series resonance: X_L = X_C at f0; the half-power points are where the reactance equals R.
- **Source:** Circuit theory.

### `prompt.passive.series_rlc_notch` - fact

> A series-RLC notch puts a resistor R from 'in' to 'out' and a series L + C from 'out' to ground (the LC branch is a short at resonance): f0 = 1 / (2*pi*sqrt(L*C)), and the notch is about R / (2*pi*L) wide, so a smaller R narrows it. Its depth is set by R against the branch's own loss resistance r - about 20*log10((R + r) / r) dB - so with real parts a smaller R also makes it shallower. An RC-only alternative is the twin-T.

- **Applies when:** Notch spec: the passive option in the prompt.
- **Valid for:** Ideal source and unloaded output. With ideal (lossless) parts r = 0 and the depth is unlimited; on a real technology r is mostly the inductor's winding resistance.
- **Why:** At f0 the branch impedance is just r, so the divider gives r / (R + r); the half-power points are where the branch reactance equals R. Replaces an earlier hint ('a smaller R gives a narrower, deeper notch') that was wrong about depth and made a live run cut R to 10 ohm and lose the notch (2026-11-19).
- **Source:** Circuit theory.

### `prompt.active.sallen_key_lowpass` - fact

> Unity-gain Sallen-Key low-pass (2nd order): R1 from 'in' to n1, R2 from n1 to n2, C2 from n2 to ground, C1 from n1 to 'out' (the feedback capacitor), and an op-amp follower (in+ = n2, in- = out, out = out). With R1 = R2 = R: f0 = 1/(2*pi*R*sqrt(C1*C2)) and Q = 0.5*sqrt(C1/C2), so Butterworth is C1 = 2*C2. Cascade stages (and/or a first-order RC) for higher orders.

- **Applies when:** Lowpass spec with op-amps allowed: the active option in the prompt.
- **Valid for:** An op-amp ideal at f0 (open-loop gain >> 1 there). A finite gain-bandwidth shifts f0 and Q; output impedance gives high-frequency feedthrough.
- **Why:** Standard unity-gain Sallen-Key equations for equal resistors.
- **Source:** Sallen & Key (1955); e.g. TI 'Analysis of the Sallen-Key Architecture' (SLOA024).

### `prompt.active.sallen_key_highpass` - fact

> Unity-gain Sallen-Key high-pass (2nd order): C1 from 'in' to n1, C2 from n1 to n2, R2 from n2 to ground, R1 from n1 to 'out', op-amp follower (in+ = n2, in- = out, out = out). With C1 = C2 = C: f0 = 1/(2*pi*C*sqrt(R1*R2)) and Q = 0.5*sqrt(R2/R1), so Butterworth is R1 = R2/2.

- **Applies when:** Highpass spec with op-amps allowed: the active option in the prompt.
- **Valid for:** An op-amp ideal over the passband; its gain-bandwidth eventually turns the high-pass into a band-pass.
- **Why:** Standard unity-gain Sallen-Key equations for equal capacitors.
- **Source:** Sallen & Key (1955).

### `prompt.active.mfb_bandpass` - fact

> Multiple-feedback (MFB) band-pass, inverting: R1 from 'in' to n1, R2 from n1 to ground, C1 from n1 to n2, C2 from n1 to 'out', R3 from n2 to 'out', op-amp in- = n2, in+ = ground ('0'), out = out. With C1 = C2 = C: f0 = 1/(2*pi*C) * sqrt((R1+R2)/(R1*R2*R3)), bandwidth = 1/(pi*R3*C), centre gain = R3/(2*R1).

- **Applies when:** Bandpass spec with op-amps allowed: the active option in the prompt.
- **Valid for:** An op-amp whose open-loop gain at f0 is well above 2*Q^2 (see prompt.tradeoff.opamp_gain); below that f0, bandwidth and gain all shift.
- **Why:** Standard MFB (Deliyannis) band-pass equations for equal capacitors.
- **Source:** Deliyannis (1968); e.g. TI 'Active Filter Design Techniques' (SLOD006).

### `prompt.active.notch_buffer` - explanation

> An op-amp follower can buffer a passive notch (e.g. a twin-T) so that what follows does not load it, or feed the twin-T's common node back from the output to sharpen it.

- **Applies when:** Notch spec with op-amps allowed: the active option in the prompt.
- **Valid for:** Twin-T and similar passive notches; the bootstrapped twin-T trades depth stability for a narrower notch.
- **Why:** A buffer removes loading; positive feedback to the common node raises the notch Q.
- **Source:** Standard active-filter practice.

### `prompt.termination.unterminated` - fact

> - SOURCE/LOAD: 'in' is driven by an ideal voltage source (0 ohm) and 'out' is not loaded. Textbook LC ladder values (g_k tables) assume resistive terminations R0 at the ends; without them an LC filter is undamped and peaks in the passband, so include your own terminating / damping resistance if you use such a table.

- **Applies when:** The spec sets no source or load resistance (the default).
- **Valid for:** Passive LC ladders; RC filters and active stages are not affected the same way.
- **Why:** The ladder's damping comes entirely from its terminations; without them the LC sections ring. A textbook 3rd-order ladder peaked +3.9 dB from an ideal source in the tests.
- **Source:** Filter tables are tabulated for terminated ladders.

### `prompt.termination.fixed` - catalogue

> - SOURCE/LOAD (fixed by the test harness, do NOT add them yourself): V1 drives 'in' through 50 ohm source resistance, and 'out' is loaded by a 1000 ohm resistor from 'out' to ground. Design for these terminations - e.g. use them as R0 in ladder tables. Do not use the ids R_SOURCE/R_LOAD or the node 'src_ext'. The passband level is whatever the source/load divider gives; the checks are relative to it.

- **Applies when:** The spec sets a source and/or load resistance.
- **Valid for:** Always: these are the spec's own numbers.
- **Why:** The harness inserts them; the model must design for them, not add them.
- **Source:** The spec (source_ohm, load_ohm).

### `prompt.parts.passive_only` - catalogue

> - PARTS: resistors, capacitors and inductors only (no supply rails are provided).

- **Applies when:** Op-amps are not allowed: no rails, or --passive-only.
- **Valid for:** Always: a property of the spec.
- **Why:** The model must know the allowed parts before it designs.
- **Source:** The spec (supply rails, allow_active).

### `prompt.parts.active` - catalogue

> - PARTS: resistors, capacitors, inductors AND op-amps. Neither passive nor active is preferred - choose by the requirements (see TRADE-OFFS).
>   Op-amps: the harness holds the supply rails at node 'vcc' = 12 V and node 'vee' = -12 V - connect op-amp supply pins to them, never add a supply source. An op-amp is {"id": "U1", "type": "opamp", "part": <grade>, "pins": {"in+": node, "in-": node, "vcc": "vcc", "vee": "vee", "out": node}}. Grades (each op-amp is simulated as the grade you choose; the supply rating applies whatever the technology):
>     * OPAMP_GP1: ...
>   The DC operating point is checked: every op-amp input needs a DC path (its bias current must flow somewhere), and the DC output must stay inside the part's swing, or the design fails.

- **Applies when:** Rails are given and op-amps are allowed.
- **Valid for:** Always: rails, grades and checks are the spec's and the library's.
- **Why:** Stated neutrally: with a one-sided prompt the model used an op-amp first in all nine powered live runs (2026-11-23), whatever the requirements said.
- **Source:** The spec (rails) and the op-amp library.

### `prompt.tradeoff.gain_needs_active` - fact

> a gain of 6 dB needs an op-amp - passive parts cannot amplify

- **Applies when:** TRADE-OFFS note; the spec asks for passband gain above 0 dB.
- **Valid for:** Always: a passive network's voltage gain at its passband level cannot exceed 0 dB (resonant voltage step-up inside a high-Q network aside).
- **Why:** Passive parts only dissipate or store energy.
- **Source:** Circuit theory.

### `prompt.tradeoff.gain_either` - fact

> the gain asked for is reachable with either; a passive filter can only attenuate, and loses a little to its parts' own resistance

- **Applies when:** TRADE-OFFS note; no gain above 0 dB is asked for.
- **Valid for:** Always.
- **Why:** 0 dB or less is reachable passively; real parts add series resistance.
- **Source:** Circuit theory.

### `prompt.tradeoff.load_isolation` - explanation

> 'out' drives a 1000 ohm load: a passive filter's response depends on it, an op-amp output isolates the filter from it

- **Applies when:** TRADE-OFFS note; the spec sets a load resistance.
- **Valid for:** While the op-amp's output resistance is small against the load.
- **Why:** A passive network's transfer function includes its load; an op-amp output is (nearly) a voltage source.
- **Source:** Circuit theory.

### `prompt.tradeoff.inductor_size` - derived

> at 50000 Hz an L-C filter at a 1 kohm impedance level needs about 3.18 mH

- **Applies when:** TRADE-OFFS note (every powered spec).
- **Valid for:** An order-of-magnitude estimate: L = R/(2*pi*f) at R = 1 kohm; a different impedance level scales L proportionally.
- **Why:** Lets the model see whether an inductor-based design is practical at this frequency before trying one.
- **Source:** Computed from the spec's target frequency.

### `prompt.tradeoff.q_scaling` - fact

>  (a series-RLC band-pass with Q = 10: about 31.8 mH, since L scales with Q)

- **Applies when:** Inside prompt.tradeoff.inductor_size for a bandpass spec.
- **Valid for:** Series RLC at a fixed impedance level: Q = 2*pi*f0*L / R.
- **Why:** Narrow bands need large L at a given resistance.
- **Source:** Circuit theory.

### `prompt.tradeoff.inductor_limit` - catalogue

> ; inductors are available only up to 10 mH on smd_pcb (enforced)

- **Applies when:** Inside prompt.tradeoff.inductor_size on a real technology.
- **Valid for:** Always: the technology profile's max_practical_inductance.
- **Why:** Larger parts do not exist in that construction; the check is enforced.
- **Source:** TechnologyProfile.max_practical_inductance.

### `prompt.tradeoff.inductor_any` - catalogue

> ; inductors of any size are available on ideal technology

- **Applies when:** Inside prompt.tradeoff.inductor_size on ideal technology.
- **Valid for:** Ideal technology only.
- **Why:** The ideal profile has no availability limit.
- **Source:** TechnologyProfile (ideal).

### `prompt.tradeoff.budget` - catalogue

> at most 1 mA may be drawn from each rail - op-amps consume their supply current whether or not they are needed; passive parts draw nothing

- **Applies when:** TRADE-OFFS note; the spec sets a supply-current budget.
- **Valid for:** Always: the spec's budget and the grades' catalogue currents.
- **Why:** Makes the cost of an op-amp visible before the design is made.
- **Source:** The spec (max_supply_current_a).

### `prompt.tradeoff.opamp_swing` - fact

> an op-amp output only swings within its rails

- **Applies when:** TRADE-OFFS note (op-amps allowed).
- **Valid for:** Always; how close to the rails is the grade's output headroom.
- **Why:** The output stage is powered from the rails.
- **Source:** Op-amp fundamentals.

### `prompt.tradeoff.opamp_gain` - rule of thumb

> rule of thumb: its open-loop gain (GBW / frequency) should be well above the stage's own gain - for a high-Q stage well above 2*Q^2

- **Applies when:** TRADE-OFFS note (op-amps allowed).
- **Valid for:** Single-op-amp stages; the 2*Q^2 figure is the MFB band-pass requirement. Sallen-Key stages need less, multi-amplifier topologies differ. 'Well above' means roughly 10x for few-percent accuracy.
- **Why:** A finite open-loop gain shifts f0, Q and gain; the error grows as the margin shrinks.
- **Source:** Active-filter design notes (e.g. TI SLOD006, ch. 16).

### `prompt.tradeoff.passive_no_power` - fact

> passive parts need no power and have no swing limit

- **Applies when:** TRADE-OFFS note (op-amps allowed).
- **Valid for:** Always (within the parts' voltage ratings, which are not modelled).
- **Why:** R, L and C need no supply.
- **Source:** Circuit theory.

### `prompt.technology.srf` - explanation

> An inductor behaves as a capacitor above its self-resonant frequency, so attenuation stops improving - and can get worse - beyond that point.

- **Applies when:** Construction note on a real technology.
- **Valid for:** Every real inductor; the frequency follows from its winding capacitance.
- **Why:** The winding capacitance resonates with the inductance; above that the capacitive path dominates.
- **Source:** TechnologyProfile.inductor_winding_c; any inductor datasheet's SRF.

### `prompt.technology.smaller_inductors` - rule of thumb

> rule of thumb: prefer smaller inductances (which self-resonate higher) and keep the self-resonance well above the band you must attenuate.

- **Applies when:** Construction note on a real technology.
- **Valid for:** Inductor-based designs whose stopband reaches the inductors' self-resonance; irrelevant for RC and active designs.
- **Why:** SRF = 1/(2*pi*sqrt(L*Cw)) rises as L falls (Cw roughly constant per family).
- **Source:** Inductor application notes.

### `prompt.technology.inductor_limit` - catalogue

>  Inductors above 10 mH are not available as smd_pcb parts; a design that needs one fails.

- **Applies when:** Construction note on a technology with an inductance limit.
- **Valid for:** Always: TechnologyProfile.max_practical_inductance.
- **Why:** Enforced by simulation/availability.py.
- **Source:** TechnologyProfile.

### `prompt.realistic_values` - rule of thumb

> rule of thumb: keep component values realistic (resistors in ohms to megaohms, capacitors in pF to uF, inductors in uH to H).

- **Applies when:** End of every prompt.
- **Valid for:** Discrete-part designs; the enforced limits (inductance per technology) are stricter where they exist.
- **Why:** Values far outside these ranges are unavailable or dominated by parasitics.
- **Source:** Component availability in practice.


## verdict

### `verdict.scale_factor` - derived

> to move the cutoff onto target, multiply EVERY inductor and capacitor value by 0.601 and keep the resistors - that shifts the response by 1/0.601 (exactly for ideal R, L and C; parasitics and op-amp limits do not scale with it)

- **Applies when:** A frequency (cutoff, centre, notch) is outside its tolerance.
- **Valid for:** Exact for ideal passive networks; on a real board or with op-amps the shift is close but not exact, since those effects do not scale.
- **Why:** Scaling every L and C by k maps H(f) to H(k*f): k = measured / target puts the feature on target.
- **Source:** Frequency scaling of RLC networks.

### `verdict.wrong_way` - explanation

> Response rises with frequency - this looks like a highpass, not a lowpass. Check the topology (which element is in series vs shunt to ground).

- **Applies when:** A lowpass/highpass whose window edges have the wrong gain order.
- **Valid for:** Always: measured on the response.
- **Why:** Swapped series/shunt elements turn a lowpass into a highpass and vice versa.
- **Source:** Measured.

### `verdict.no_crossing` - explanation

> No usable -3dB crossing in the judged range (100 Hz-1 MHz): gain is 0.0 dB in the passband and -2.0 dB at the far edge of that range. The cutoff may be outside it, or the circuit isn't attenuating enough to be a lowpass.

- **Applies when:** No -3 dB point, or under 10 dB of attenuation at the far edge.
- **Valid for:** Always: measured.
- **Why:** Without a crossing there is no cutoff to judge.
- **Source:** Measured.


## stopband

### `stopband.edge_shortfall` - derived

> (1) short by 20.0 dB at the stopband edge (20.0 dB at 100000 Hz): the rolloff is too shallow at the stopband edge, 1.00 decade past the cutoff

- **Applies when:** The stopband fails at its edge (not only deeper in the band).
- **Valid for:** Always: measured.
- **Why:** An edge shortfall means the response does not fall fast enough between cutoff and edge.
- **Source:** Measured.

### `stopband.effective_order` - derived

> at the edge it rolls off like a ~1.0-order filter

- **Applies when:** With stopband.edge_shortfall.
- **Valid for:** The order of the Butterworth that would give the measured attenuation at the edge - a yardstick, not the design's actual order.
- **Why:** Puts the measured steepness in the same units as the suggestion that follows.
- **Source:** Measured attenuation, inverted through the Butterworth magnitude formula.

### `stopband.butterworth_orders` - fact

> a Butterworth of order 3 gives 60.0 dB there (order 2 gives only 40.0 dB)

- **Applies when:** With stopband.edge_shortfall; n is the lowest order giving the requirement plus a 3 dB margin.
- **Valid for:** Exact for Butterworth responses; other response types of the same order give more (Chebyshev, elliptic) or less (Bessel) at the edge.
- **Why:** |H|^2 = 1/(1 + (f/fc)^(2n)).
- **Source:** Butterworth magnitude formula.

### `stopband.rolloff_per_order` - rule of thumb

> rule of thumb: each filter order adds about 20 dB per decade

- **Applies when:** With stopband.edge_shortfall.
- **Valid for:** All-pole responses well past the cutoff; not elliptic / Chebyshev-II.
- **Why:** The asymptote of an n-th order all-pole response is 20*n dB per decade.
- **Source:** Standard filter theory.

### `stopband.two_problems` - fact

> TWO separate stopband problems - both must be fixed, fixing one alone will not pass

- **Applies when:** The stopband fails both at the edge and deeper in the band.
- **Valid for:** Always: both are separate measured failures of one requirement.
- **Why:** Reporting only one made a live run chase the wrong fix (2026-11-12).
- **Source:** Measured.

### `stopband.recovers` - explanation

> (2) short by 15.0 dB, and NOT at the band edge: attenuation is 40.0 dB at 100000 Hz but degrades to 25.0 dB at 100000000 Hz, so the response recovers inside the stopband instead of staying down - a resonance or a path around the filter; a separate problem from (1), located deeper in the band

- **Applies when:** The worst stopband point is inside the band and worse than the edge.
- **Valid for:** Always: measured. A recovering response can only come from a resonance or a path that bypasses the filter.
- **Why:** Distinguishes this from an edge shortfall, which needs a different fix.
- **Source:** Measured.

### `stopband.recovers_separate` - fact

> a separate problem from (1), located deeper in the band

- **Applies when:** Tail of stopband.recovers when the edge is short as well.
- **Valid for:** Always: measured.
- **Why:** Both failures are reported independently.
- **Source:** Measured.

### `stopband.recovers_edge_ok` - fact

> the edge itself meets the requirement

- **Applies when:** Tail of stopband.recovers when the edge passes.
- **Valid for:** Always: measured.
- **Why:** Tells the model the edge needs no change.
- **Source:** Measured.


## verdict

### `verdict.peaking` - explanation

> passband peaks +12.4 dB above its level at 6.31 kHz (limit 3 dB) - a resonance that nothing damps enough; ...

- **Applies when:** Passband gain rises more than max_peaking_db above its reference.
- **Valid for:** Always: measured. The peak may come from an L-C section or from a high-Q active stage.
- **Why:** A live run passed with +12 dB of peaking before this check existed.
- **Source:** Measured.

### `verdict.peaking_terminated` - fact

> check the element values against the fixed source/load resistances - ladder tables assume the terminations they were computed for

- **Applies when:** Tip in verdict.peaking when the spec sets terminations.
- **Valid for:** Terminated designs.
- **Why:** Tabulated ladder values only give a flat response at their own terminations.
- **Source:** Filter tables.

### `verdict.peaking_unterminated` - fact

> nothing damps it from outside: the source is ideal (0 ohm) and 'out' is unloaded, so the circuit's own resonances - an L-C section, or a high-Q active stage - need their own damping resistance or a lower Q

- **Applies when:** Tip in verdict.peaking when the spec sets no terminations.
- **Valid for:** Always for an ideal source and open output.
- **Why:** With no external resistance, all damping must come from inside the circuit.
- **Source:** Circuit theory.

### `verdict.gain_active_levers` - fact

> the gain is set by the gain-setting resistors (e.g. 1 + Rf/Rg for a non-inverting stage, R3/(2*R1) at the centre of an MFB band-pass, where R3 also sets the bandwidth) or by a source/load divider

- **Applies when:** The passband-gain target fails and op-amps are allowed.
- **Valid for:** The named topologies; the verdict does not know which one is used, so it names examples rather than claiming one.
- **Why:** Gives the levers without asserting which applies.
- **Source:** Standard op-amp stage equations.

### `verdict.gain_passive_levers` - fact

> with passive parts only the gain can be at most 0 dB, and what is lost goes to resistance in the signal path (series resistors, the inductors' winding resistance) or to a divider against what follows

- **Applies when:** The passband-gain target fails and op-amps are not allowed.
- **Valid for:** Passive networks.
- **Why:** Op-amp levers would be advice the model cannot act on (seen live, 2026-11-23).
- **Source:** Circuit theory.

### `verdict.gain_real_board` - explanation

> on a real board parasitics can also erode it - see the diagnosis

- **Applies when:** Appended to the gain levers on a real technology.
- **Valid for:** Real technologies; the diagnosis then says whether they did.
- **Why:** A gain shortfall can come from stray capacitance on high-impedance nodes (seen live, 2026-11-23).
- **Source:** Measured by the diagnosis.

### `verdict.bandpass_shape` - explanation

> Response is not band-pass shaped within the judged range: the edges are only 53.0 dB (1 kHz) and 2.4 dB (10 MHz) below the peak; both sides need to roll off by at least 10 dB

- **Applies when:** A bandpass with less than 10 dB of rejection at a window edge.
- **Valid for:** Always: measured inside the judged window.
- **Why:** A peak without rolloff on both sides is not a band-pass.
- **Source:** Measured.

### `verdict.notch_symmetry` - explanation

> gain differs between the low edge (-0.4 dB at 500 Hz) and the high edge (-18.8 dB at 100 MHz) of the judged range - a notch should pass both sides equally; this looks more like a lowpass/highpass

- **Applies when:** A notch whose window edges differ by more than 3 dB.
- **Valid for:** Always: measured inside the judged window.
- **Why:** A notch passes both sides; a difference means a slope, not a notch.
- **Source:** Measured.


## parasitic

### `parasitic.coupling.why` - explanation

> signal reaches 'out' through stray capacitance from 'in', a path that goes around the filter rather than through it

- **Applies when:** Input-output stray coupling is the limiting parasitic group.
- **Valid for:** Technologies with input-output coupling (breadboard ~2 pF).
- **Why:** The coupling capacitance is a direct in-to-out path.
- **Source:** TechnologyProfile.io_coupling_c; measured by the diagnosis.

### `parasitic.coupling.remedy` - rule of thumb

> rule of thumb: lower the high-frequency impedance at 'out' - end the filter in a shunt capacitor there (a ladder ending in a series inductor leaves 'out' with nothing but the load to ground), ideally several in parallel, since N in parallel divide their lead inductance by N

- **Applies when:** After parasitic.coupling.why.
- **Valid for:** Passive ladders and other filters whose output node is high-impedance at high frequency.
- **Why:** The coupled signal divides against the output node's impedance to ground.
- **Source:** Layout practice.

### `parasitic.winding_c.why` - explanation

> an inductor becomes a capacitor above its self-resonance, and smaller inductors resonate higher

- **Applies when:** Inductor winding capacitance is the limiting parasitic group.
- **Valid for:** Real inductors.
- **Why:** SRF = 1/(2*pi*sqrt(L*Cw)).
- **Source:** TechnologyProfile.inductor_winding_c.

### `parasitic.winding_c.remedy` - rule of thumb

> rule of thumb: lower the impedance level (smaller L with proportionally larger C and smaller R keeps the same cutoff) - but not so far that the parts' own series resistance (a few ohm for a leaded inductor) becomes comparable to it - or split one inductor into several smaller ones in series

- **Applies when:** After parasitic.winding_c.why.
- **Valid for:** Inductor-based designs; the series-split helps only while the smaller parts' winding capacitances do not add up in another path.
- **Why:** A smaller L self-resonates higher; impedance scaling keeps the response.
- **Source:** Inductor application notes.

### `parasitic.losses.why` - explanation

> the parts' own series resistance is significant against the circuit's impedance level: an inductor's winding resistance grows with its inductance and adds to every resistor in series with it

- **Applies when:** Losses (winding resistance, ESR, contact resistance) are the limiting group.
- **Valid for:** Real technologies.
- **Why:** A 210 uH inductor's 4.35 ohm moved a 10 ohm design's cutoff from 10 to 6 kHz (live, 2026-11-19).
- **Source:** TechnologyProfile.inductor_dcr_per_sqrt_h.

### `parasitic.losses.remedy` - rule of thumb

> rule of thumb: raise the impedance level (larger L, smaller C, larger R for the same cutoff) so those few ohms no longer matter, or account for them in the resistor values

- **Applies when:** After parasitic.losses.why.
- **Valid for:** Until the larger L runs into its self-resonance or the technology's inductance limit - the two remedies pull in opposite directions.
- **Why:** Fixed loss resistances matter less against a higher impedance level.
- **Source:** Circuit practice.

### `parasitic.esl.why` - explanation

> a capacitor becomes an inductor above its self-resonance

- **Applies when:** Capacitor ESL is the limiting group.
- **Valid for:** Real capacitors.
- **Why:** ESL resonates with C.
- **Source:** TechnologyProfile.capacitor_esl.

### `parasitic.esl.remedy` - rule of thumb

> rule of thumb: use several smaller capacitors in parallel instead of one large one

- **Applies when:** After parasitic.esl.why.
- **Valid for:** Shunt capacitors at high frequency.
- **Why:** Parallel parts divide the ESL; smaller C self-resonates higher.
- **Source:** Decoupling practice.

### `parasitic.leads.why` - explanation

> every connection adds series inductance, which spoils shunt capacitors at high frequency

- **Applies when:** Lead / interconnect inductance is the limiting group.
- **Valid for:** Real technologies.
- **Why:** Lead inductance is in series with each part.
- **Source:** TechnologyProfile.interconnect_l_per_terminal.

### `parasitic.leads.remedy` - rule of thumb

> rule of thumb: parallel shunt paths divide it

- **Applies when:** After parasitic.leads.why.
- **Valid for:** Shunt paths; series paths add their lead inductance.
- **Why:** N equal parallel paths divide the inductance by N.
- **Source:** Layout practice.

### `parasitic.node_c.why` - explanation

> every node has stray capacitance to ground (and resistors have some across them), which loads HIGH-IMPEDANCE nodes, and a high-Q stage magnifies it

- **Applies when:** Stray node capacitance is the limiting group.
- **Valid for:** Real technologies.
- **Why:** A 530 kohm node lost 3 dB of gain to 0.3 pF at 50 kHz in a Q = 10 MFB (live, 2026-11-23).
- **Source:** TechnologyProfile.interconnect_node_c / resistor_shunt_c.

### `parasitic.node_c.remedy` - rule of thumb

> rule of thumb: nodes at hundreds of kilohms are the ones affected - Lower the impedance level, smaller resistors with proportionally larger capacitors keep the same frequencies

- **Applies when:** After parasitic.node_c.why.
- **Valid for:** Where the stray capacitance's reactance at the frequency of interest is within ~100x of the node impedance.
- **Why:** Stray C matters once 1/(2*pi*f*C) approaches the node impedance.
- **Source:** Circuit practice.


## diag

### `diag.grade.faster` - catalogue

> faster grades rated for 2.5 V / -2.5 V: OPAMP_RR10 (10 MHz)

- **Applies when:** The per-component check names an op-amp and faster grades fit the rails and budget.
- **Valid for:** Always: the library's ratings against the spec.
- **Why:** Only grades the model can actually use are named.
- **Source:** Op-amp library (gbw_hz, supply range, quiescent_a).

### `diag.grade.less_q_otherwise` - rule of thumb

> rule of thumb: otherwise less gain or Q per stage

- **Applies when:** After diag.grade.faster.
- **Valid for:** Single-stage active filters limited by open-loop gain.
- **Why:** The required open-loop gain grows with the stage's gain and Q.
- **Source:** Active-filter design notes.

### `diag.grade.none` - catalogue

> no grade faster than OPAMP_FET3 (3 MHz) is rated for 12 V / -12 V

- **Applies when:** The per-component check names an op-amp and no faster grade fits.
- **Valid for:** Always: the library's ratings against the spec.
- **Why:** 'Choose a faster grade' with none available sent a live run (2026-11-23) off to a passive design it could not finish.
- **Source:** Op-amp library.

### `diag.grade.less_q` - rule of thumb

> rule of thumb: lower the gain or Q per stage - e.g. split it into two lower-Q stages - or use a design that needs no op-amp

- **Applies when:** After diag.grade.none.
- **Valid for:** Op-amp-limited high-Q or high-gain stages.
- **Why:** Lower Q per stage lowers the open-loop gain each stage needs.
- **Source:** Active-filter design notes.

### `diag.ideal_pass_active` - derived

> With ideal parts - ideal op-amps included - this exact design would PASS, so the failure comes from the real parts: the board's parasitics or the chosen op-amp grade's own limits (gain-bandwidth, output resistance, bias current); the per-component check below says which

- **Applies when:** Diagnosis: ideal parts pass and the circuit has op-amps.
- **Valid for:** Always: from the ideal re-simulation.
- **Why:** Separates construction from design.
- **Source:** Measured (ideal re-simulation).

### `diag.ideal_pass` - derived

> With ideal parts this exact design would PASS, so the failure is caused by the construction parasitics, not by the design itself

- **Applies when:** Diagnosis: ideal parts pass, passive circuit.
- **Valid for:** Always: from the ideal re-simulation.
- **Why:** Separates construction from design.
- **Source:** Measured (ideal re-simulation).

### `diag.design_only` - derived

> The peaking failure is in the design itself - ideal parts fail it too - so fix that first (see above); parasitics are not the cause

- **Applies when:** Diagnosis: every failed check also fails with ideal parts.
- **Valid for:** Always: from the ideal re-simulation.
- **Why:** Keeps parasitics from being blamed for design problems.
- **Source:** Measured (ideal re-simulation).

### `diag.mixed` - derived

> Two kinds of failure: bandwidth fails even with ideal parts (a design problem, see above), while passband gain fails only because of the construction parasitics

- **Applies when:** Diagnosis: some failed checks are design, some construction.
- **Valid for:** Always: from the ideal re-simulation.
- **Why:** Each kind needs a different fix.
- **Source:** Measured (ideal re-simulation).

### `diag.built_only` - derived

> The passband gain failure is caused by the construction parasitics: ideal parts pass it

- **Applies when:** Diagnosis: the failed checks pass with ideal parts, though ideal parts fail some other check.
- **Valid for:** Always: from the ideal re-simulation.
- **Why:** Replaces a confusing 'ideal parts would still fail a check' opening.
- **Source:** Measured (ideal re-simulation).

### `diag.ideal_only` - derived

>  (they would miss frequency instead, which this design passes as built - mind it when changing values)

- **Applies when:** Inside diag.built_only when ideal parts fail another check.
- **Valid for:** Always: from the ideal re-simulation.
- **Why:** The parasitics happen to help that check.
- **Source:** Measured.

### `diag.per_component.fixer` - derived

> Per component (each made ideal on its own, the rest as built): L1 (100u) alone would make the design PASS - that is the part to change

- **Applies when:** Diagnosis level 'full': making one part ideal makes the design pass.
- **Valid for:** Always: from the per-component re-simulations.
- **Why:** Names the part the model can change.
- **Source:** Measured.

### `diag.per_component.partial` - derived

> Per component (each made ideal on its own, the rest as built): R3 (530k) +3.0 dB; no single part fixes it alone

- **Applies when:** Diagnosis level 'full': parts recover margin but none passes alone.
- **Valid for:** Always: measured.
- **Why:** Ranks the parts by what they cost.
- **Source:** Measured.

### `diag.per_component.none` - derived

> Per component: no single part, made ideal on its own, recovers more than 1 dB - the damage comes from several parts or the board's parasitics together

- **Applies when:** Diagnosis level 'full': no part recovers a meaningful margin alone.
- **Valid for:** Always: measured. Replaces 'the cause is the layout as a whole', an inference stated as fact.
- **Why:** Says what was measured without guessing the mechanism.
- **Source:** Measured.

### `diag.srf_below_stopband` - derived

> below the stopband: acts as a capacitor across the whole stopband

- **Applies when:** A part's computed self-resonance lies below a lowpass stopband.
- **Valid for:** Computed from the part value and the technology profile.
- **Why:** The part does not behave as its type anywhere in the stopband.
- **Source:** TechnologyProfile; the part's value.

### `diag.srf_inside` - derived

> inside the stopband: acts as a capacitor above it

- **Applies when:** A part's computed self-resonance lies inside the judged band or stopband.
- **Valid for:** Computed from the part value and the technology profile.
- **Why:** Above it the part behaves as the opposite reactance.
- **Source:** TechnologyProfile; the part's value.


## bias

### `bias.saturated` - derived

> its output sits at -10.53 V at DC, at its swing limit (-10.50 .. +10.50 V): it is SATURATED, so the AC response describes a circuit that would not work

- **Applies when:** An op-amp's DC output is within 0.1 V of its swing limit.
- **Valid for:** Always: from the operating point and the grade's (or the ideal part's) swing.
- **Why:** An AC sweep linearises around the bias point and looks plausible even then.
- **Source:** Measured (operating point).

### `bias.saturated_causes` - rule of thumb

> rule of thumb: the usual causes are an input with no DC path, no DC negative feedback (a capacitor-only feedback path leaves the full open-loop gain at DC), or positive feedback

- **Applies when:** After bias.saturated.
- **Valid for:** Linear op-amp stages; comparators and oscillators saturate by design.
- **Why:** These are the common ways a linear stage loses its DC operating point.
- **Source:** Op-amp application practice.

### `bias.no_dc_path` - explanation

> in+ (node 'n1') has no DC path - only capacitors reach it - so the input bias current (5e-08 A) has nowhere to go; add a resistor from it to ground or to the signal path

- **Applies when:** An op-amp input node has no resistive/inductive path to a held node.
- **Valid for:** Every technology: even an ideal input leaves the node's DC level undefined.
- **Why:** Found by a DC-path graph of the circuit.
- **Source:** Circuit analysis.

### `bias.supply_rating` - catalogue

> OPAMP_RR10 is rated for 2.7-5.5 V total supply, the rails give 24 V - choose another grade

- **Applies when:** The rails are outside the grade's rated total supply.
- **Valid for:** Every technology: a catalogue property of the part.
- **Why:** An ideal simulation of a part the rails would destroy is not a design.
- **Source:** Op-amp library (supply_min_v / supply_max_v).

### `bias.pins_not_on_rails` - fact

> its supply pins go to 'vcc'/'0', not to the rails 'vcc'/'vee'

- **Applies when:** An op-amp's supply pins are not wired to vcc / vee.
- **Valid for:** Always.
- **Why:** The part would be unpowered or wrongly powered.
- **Source:** The circuit as proposed.

### `bias.input_range` - derived

> in+ sits at -10.63 V, outside the input range -10.50 .. +10.50 V

- **Applies when:** An op-amp input's DC level is outside its common-mode range.
- **Valid for:** Always: from the operating point and the grade's input headroom.
- **Why:** Outside its range the input stage stops working.
- **Source:** Measured.

### `bias.no_operating_point` - rule of thumb

> rule of thumb: the DC operating point could not be solved, so no bias point exists for the active parts - the usual causes are a floating node or a loop with no DC feedback

- **Applies when:** ngspice produced no operating point for an active circuit.
- **Valid for:** Circuits with op-amps.
- **Why:** Those are the common reasons a DC solution does not exist.
- **Source:** SPICE practice.


## budget

### `budget.over` - derived

> draws 1.40 mA from the +12 V rail (limit 1 mA); op-amps alone: U1 (OPAMP_FET3) 1.4 mA

- **Applies when:** The current drawn from a rail exceeds max_supply_current_a.
- **Valid for:** Measured on real technologies; on ideal technology the grades' catalogue currents are added.
- **Why:** Makes a power budget bind on the grade choice.
- **Source:** Measured (operating point) and the op-amp library.

### `budget.levers` - fact

> use a lower-power grade, fewer op-amps, higher-value resistors between the rails, or a passive design

- **Applies when:** After budget.over.
- **Valid for:** Always: these are what draws current from the rails.
- **Why:** Each one reduces the measured rail current.
- **Source:** Circuit theory.


## availability

### `availability.too_large` - catalogue

> L1 = 22 mH exceeds the 10 mH available on smd_pcb

- **Applies when:** An inductor exceeds the technology's max_practical_inductance.
- **Valid for:** Always: the technology profile's limit.
- **Why:** A response achieved with a part that does not exist is not a design.
- **Source:** TechnologyProfile.max_practical_inductance.

### `availability.levers` - fact

> use a smaller inductor (raise the capacitance, or lower the impedance level) or a design without it

- **Applies when:** After availability.too_large.
- **Valid for:** Always: L at a given frequency falls as C or the impedance level does.
- **Why:** f0 = 1/(2*pi*sqrt(L*C)); impedance scaling moves L and R together.
- **Source:** Circuit theory.


## loop

### `loop.repeated_design` - fact

> REPEATED DESIGN: this circuit is identical to iteration 2 (only ids, order or title may differ), so it was not simulated again. That attempt failed with: Simulated bandpass: ... FAIL. Submitting it again cannot pass - change the design.

- **Applies when:** A proposal equals an earlier one by design_key.
- **Valid for:** Always: an identical circuit simulates identically.
- **Why:** A live run resubmitted the same bandpass three times (2026-11-19).
- **Source:** The run's own history.

### `loop.reject_ideal_opamp` - catalogue

> Circuit definition error: op-amp part(s) ['OPAMP_IDEAL'] are not grades you can choose; pick one of ['OPAMP_GP1', 'OPAMP_FET3'] (how ideal the op-amp behaves follows the technology, not the part name).

- **Applies when:** The model names a part outside SELECTABLE_OPAMPS.
- **Valid for:** Always.
- **Why:** Offered next to real grades, a model picks perfection (all four live runs, 2026-11-23).
- **Source:** Op-amp library (SELECTABLE_OPAMPS).

### `loop.reject_passive_only` - catalogue

> Circuit definition error: this spec allows passive parts only (resistors, capacitors, inductors) - no op-amps.

- **Applies when:** The model uses an op-amp although the spec allows passive parts only.
- **Valid for:** Always: a property of the spec.
- **Why:** --passive-only keeps rails in the spec but forbids active parts.
- **Source:** The spec (allow_active).
