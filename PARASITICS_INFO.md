# How the parasitic values are chosen

All numbers live in [`src/spice_agent/circuit/parasitics.py`](src/spice_agent/circuit/parasitics.py).
This document explains where they came from and how much to trust them.

## The short version

They are **representative figures for a class of construction**, taken from
standard engineering rules of thumb and typical datasheet values. They are not
measurements of your parts, and they are not extracted from a layout.

A prediction from this model means *"a filter built roughly this way behaves
roughly like this"* — never *"your board will measure this"*.

## Parametric, not a catalogue

A real designer picks a **part number** and reads its parasitics off the
datasheet. We instead compute parasitics from the component value plus a
technology profile, so the agent can propose any value it likes.

That makes the model effectively a *synthetic parts family*: an averaged,
idealised version of what you would get buying one class of component. It
composes with everything else and needs no BOM database, at the cost of never
being right about a specific part.

## What scales, and what doesn't

Only three values are computed; the rest are constants per profile.

| Quantity | Law | Why |
|---|---|---|
| Inductor DCR | `300 · √L` | Inductance grows with turns² on a fixed core, so turns ∝ √L; wire length — and so resistance — grows with turns. |
| Inductor `R_p` | `Q · √(L/C_p)` | `√(L/C_p)` is the tank's characteristic impedance (4.6 kΩ at 318 µH, 25.8 kΩ at 10 mH), so a fixed resistance would be wrong across the range. Q is the part that stays roughly constant for a family. |
| Inductor SRF | `1 / (2π√(L·C_p))` | Not a stored value — a consequence of `C_p`. |

`C_p` itself is held **constant per family**, which is the main approximation in
the inductor model. Real winding capacitance varies with winding style more than
with inductance.

## The values, with confidence

| Field | `smd_pcb` | `breadboard` | Confidence |
|---|---|---|---|
| `inductor_winding_c` | 3 pF | 15 pF | **LOW — anchor this** |
| `inductor_q_at_srf` | 20 | 10 | **LOW** |
| `inductor_dcr_per_sqrt_h` | 300 Ω/√H | 300 Ω/√H | MEDIUM |
| `capacitor_esl` | 0.8 nH | 4 nH | HIGH |
| `capacitor_esr` | 20 mΩ | 50 mΩ | MEDIUM |
| `resistor_series_l` | 0.4 nH | 8 nH | MEDIUM |
| `resistor_shunt_c` | 0.2 pF | 0.5 pF | LOW |
| `interconnect_l_per_terminal` | 4 nH | 15 nH | MEDIUM |
| `interconnect_node_c` | 0.3 pF | 2 pF | LOW |
| `contact_resistance` | 0 (soldered) | 30 mΩ | LOW |
| `io_coupling_c` | 0.02 pF | 2 pF | LOW |
| `resistor_range` | 1 Ω – 10 MΩ | 1 Ω – 10 MΩ | HIGH (enforced: `NOT BUILDABLE`) |
| `capacitor_range` | 1 pF – 100 nF (C0G) | 1 pF – 10 µF (C0G or film) | MEDIUM (enforced) |
| `inductor_range` | 1 nH – 10 mH | 1 µH – 100 mH | MEDIUM (enforced) |

**Where the high-confidence ones come from.** 0.5–0.9 nH of ESL for an 0603
MLCC is a standard figure in vendor application notes. Roughly 1 nH per mm for
a narrow trace or a component lead is a long-standing rule of thumb, which is
where the interconnect inductances come from (≈4 mm of trace on a board, lead
plus strip plus jumper on a breadboard).

**Where the low-confidence ones come from.** 5–30 pF is the range usually
quoted for mH-class leaded chokes, so 15 pF is a middle. SMD parts wind fewer
turns, hence 3 pF. Filter inductors run Q = 20–80 at resonance; 20 and 10 are
conservative, with breadboard lower on the grounds that cheap leaded chokes
have worse cores. A few pF between adjacent breadboard strips, and tens of
milliohms per spring contact, are both widely repeated and rarely measured.

## The two numbers that matter most

`inductor_winding_c` and `inductor_q_at_srf` carry essentially every conclusion
this project draws about buildability, because together they decide where an
inductor stops being an inductor and how violently it resonates on the way.
Everything else is a correction by comparison — DCR costs a fraction of a dB in
the passband, while `C_p` decides whether the stopband works at all.

Two accessors exist so they can be checked:

```python
profile.inductor_srf_hz(L)   # 1/(2*pi*sqrt(L*Cp))  -> validates C_p
profile.inductor_rp(L)       # Q*sqrt(L/Cp)         -> validates Q
```

**One impedance sweep of one real inductor pins both.** The frequency of the
impedance peak gives `C_p`; the height of that peak *is* `R_p`, which gives Q.
Verified in simulation — set `R_p` to 500 kΩ and the peak measures 497 kΩ:

| profile | L | `R_p` set | peak \|Z\| | peak at | SRF predicted |
|---|---|---|---|---|---|
| `smd_pcb` | 318.3 µH | 206 kΩ | 203 kΩ | 5.13 MHz | 5.15 MHz |
| `smd_pcb` | 10 mH | 1155 kΩ | 1140 kΩ | 0.92 MHz | 0.92 MHz |
| `breadboard` | 318.3 µH | 46 kΩ | 46 kΩ | 2.29 MHz | 2.30 MHz |
| `breadboard` | 10 mH | 258 kΩ | 258 kΩ | 0.41 MHz | 0.41 MHz |

A single datasheet SRF figure is enough to tell whether `C_p` is right to within
a factor of two, which is the accuracy that matters here.

## Interconnect: an estimate, not an extraction

The real flow is schematic → layout → **extraction**, where a tool reads actual
trace geometry. We have no layout: the agent emits a netlist and stops, so trace
lengths genuinely do not exist in this project. The profiles assume typical
geometry instead. A tight layout beats these figures; a sloppy one with long
loops is worse.

The breadboard is the more defensible of the two, since its geometry is
quantised and known — 2.54 mm pitch, fixed strip lengths, standard jumper
lengths. "Some PCB someone might lay out" is the vague case.

## Why breadboard is not just SMD with bigger numbers

Most differences are magnitude (3.75× the interconnect inductance, 6.7× the node
capacitance). Two are **structural**:

- **`io_coupling_c`** — capacitance directly between input and output. With no
  ground plane, adjacent strips couple, which gives the signal a path *around*
  the filter. That puts a hard floor on stopband attenuation which no amount of
  extra filter order can lower. The SMD value is 100× smaller because a ground
  plane sits between the nodes.
- **`contact_resistance`** — a soldered joint has none worth modelling; a
  solderless contact does, and it varies between insertions.

## Stated simplifications

These are deliberate choices, not oversights:

- **Ground returns are perfect.** Terminals connected to node 0 get no lead
  inductance or contact resistance. Modelling them would give every component
  its own local ground and so model ground bounce — finer-grained than the rest
  of this model justifies.
- **Sources stay ideal.** A source's own parasitics are not part of the circuit
  being designed.
- **Availability is advisory.** `max_practical_inductance` is recorded but
  unenforced: nothing stops the agent proposing 23.87 mH in an 0603 package.

## Changing them

Edit the profile constants directly — they are plain dataclass fields and
nothing caches them:

```python
SMD_PCB = TechnologyProfile(
    name="smd_pcb",
    inductor_winding_c=3e-12,      # <- measured yours? put it here
    inductor_q_at_srf=20.0,
    ...
)
```

A new profile is one more `TechnologyProfile(...)` added to the `PROFILES` dict;
it then appears automatically in `--technology` on the CLI and in
`DesignSpec.technology`. All values are SI (farads, henries, ohms), and a zero
means "not modelled", which is exactly how the `IDEAL` profile switches
everything off.

To see the effect of a change:

```bash
python -m spice_agent.circuit     # SRF/DCR/Rp tables, prompt text, netlists
python -m pytest tests/circuit/test_parasitics.py
```

Note that several tests assert measured figures recorded in their comments, so
changing a value will correctly fail them — update the comment and the number
together, and measure rather than guess the replacement.
