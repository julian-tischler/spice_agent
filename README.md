# spice-agent

An LLM designs a passive filter. A real SPICE simulator decides whether it
worked. The measured result goes back to the model so it can revise.

The point is that nothing is taken on the model's word: every verdict comes
from ngspice running an actual netlist, and the feedback the model receives is
measured rather than asserted. Optionally the simulation also includes the
parasitics of a chosen construction technology, so the question becomes "would
this work when *built*" rather than "is the arithmetic right".

```bash
python -m spice_agent.agent --type lowpass --target 10000 \
    --stopband-hz 100000 --stopband-atten 40 \
    --asc out/designs/lp10k.asc --plot out/plots/lp10k.png
```

> Iteration 1 — 2nd-order L-C proposed. Cutoff 9991 Hz (ok), attenuation at
> 100 kHz is 40.0 dB, need ≥ 40.0 — short by 0.0 dB at the stopband edge; no
> choice of component values fixes this, the filter ORDER must increase.
>
> Iteration 2 — 3rd-order L-C-L. Cutoff 9993 Hz, 60.0 dB at 100 kHz. **PASS**

---

## How it works

```
         DesignSpec                      what must be true of the design
             │
             ▼
   agent/  prompts.py ──► Gemini         brief in, circuit JSON out
             │               │           (paced and retried: rate_limit.py)
             │               ▼
             └──► loop.py ◄──┘
                     │  proposed components as JSON
                     ▼
   circuit/  model.py            JSON → validated Circuit
                     │
                     ├─ expansion.py     optional: ideal part → parasitic
                     │                   equivalent, using parasitics.py
                     ▼
   simulation/  ngspice.py       run it, parse the sweep
                     │
                     ├─ metrics.py       measure cutoff / peak / notch / band
                     ▼
                 verify.py       judge against the spec, say what to fix
                     │
                     ▼
              result dict  ──┬──► back to the model as the tool response
                             ├──► export/   .asc schematic, .cir netlist
                             └──► plotting  annotated Bode plot
```

Two rules shape the whole design:

**The LLM never writes SPICE.** It fills in a JSON schema describing components
— id, type, two nodes, a value — and a deterministic Python layer compiles that
to a netlist. Syntax errors are therefore impossible, and every failure the
model sees is about circuit behaviour.

**The model is told what to achieve, never how.** A spec is a frequency, a
tolerance, an attenuation. Choosing the topology and the filter order is the
model's job to derive, which is also what makes the task non-trivial.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the module layout and the reasoning
behind it.

---

## Install

Needs Python ≥ 3.10 and **ngspice** on your PATH (or set `NGSPICE_PATH`).
LTspice is optional — only the schematic export and its cross-checks use it.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
.venv/Scripts/python.exe -m pip install pandas numpy matplotlib google-genai pytest
```

> `pyproject.toml` does not yet declare runtime dependencies, so the second
> line is currently required. Worth fixing.

Check that both simulators were found:

```bash
.venv/Scripts/python.exe -m spice_agent.paths
```

For the agent itself, set `GEMINI_API_KEY`. Everything else — simulation,
verification, export, plotting — works without any API key.

---

## The DesignSpec

A `DesignSpec` is the whole requirement. It is turned into prose for the model
and used verbatim to judge the result, so the brief and the verdict can never
disagree.

| Field | Default | Meaning |
|---|---|---|
| `filter_type` | `"lowpass"` | `lowpass`, `highpass`, `bandpass` or `notch` |
| `target_hz` | `1590.0` | −3 dB cutoff (LP/HP) or centre frequency (BP/notch) |
| `tolerance_pct` | `10.0` | how far `target_hz` may miss |
| `bandwidth_hz` | `None` | bandpass only: required −3 dB bandwidth |
| `min_notch_depth_db` | `20.0` | notch only: minimum depth below passband |
| `stopband_hz` | `None` | LP/HP: edge of the stopband nearest the passband |
| `stopband_atten_db` | `None` | LP/HP: minimum attenuation across that stopband |
| `stopband_end_hz` | `None` | LP/HP: far edge; defaults to the end of the sweep |
| `technology` | `"ideal"` | `ideal`, `smd_pcb` or `breadboard` |

`describe()` renders it as the line the model actually reads:

```python
>>> DesignSpec("lowpass", 10_000, stopband_hz=100_000, stopband_atten_db=40).describe()
'lowpass filter, cutoff frequency (-3dB point) 10000 Hz (+/-10%);
 at least 40 dB below passband above 100000 Hz'
```

### Why the stopband term matters

Without it, a spec is just a frequency and **any** order satisfies it — a
single resistor and capacitor will do, and every frontier model produces that
in one shot. The stopband is what makes the problem real, because order sets
rolloff: an n-th order filter falls off roughly 20·n dB per decade, so the
requirement implicitly dictates a topology.

Measured on real circuits:

| | attenuation one decade past cutoff |
|---|---|
| 1st-order RC | 20.0 dB |
| 2nd-order RLC | 39.1 dB |
| 3rd-order LC ladder | 60.0 dB |

So "40 dB at ten times the cutoff" is just out of a 2nd-order's reach and
genuinely forces 3rd order. `spec.min_order` estimates the minimum from the
asymptote — and the answer is often counterintuitive: 40 dB at **10×** the
cutoff needs 2nd order, but the same 40 dB at **2×** needs 7th.

### The stopband is a band, not a point

`stopband_atten_db` is required across the **whole** stopband, judged by its
worst point. A single-frequency check can be satisfied by a response that dips
there and recovers — which is exactly what an inductor's self-resonance does —
so a filter could pass while being useless either side of the sample.

### What is *not* checked

`technology` and the spec fields above are enforced. The prompt also asks for
unity passband gain, and **nothing verifies that** — a design sitting at −6 dB
would satisfy every check. Phase is simulated and plotted but never judged.

---

## Usage

### Design a filter

```bash
python -m spice_agent.agent --type bandpass --target 10000 --bandwidth 3000
```

```bash
python -m spice_agent.agent --type notch --target 2000 --tolerance 5 --notch-depth 30
```

Add `--asc`, `--cir` and `--plot` to write outputs — only produced when the
design actually passes:

```bash
python -m spice_agent.agent --type lowpass --target 10000 \
    --stopband-hz 100000 --stopband-atten 40 \
    --asc out/designs/d.asc --cir out/designs/d.cir --plot out/plots/d.png
```

`--max-iterations` caps the budget (default 6). `--min-interval`,
`--max-retries` and `--initial-backoff` control API pacing; a daily-quota 429
is detected from the server's own `RetryInfo` and fails fast rather than
burning the retry budget on waits that cannot succeed.

### Design for a real board

```bash
python -m spice_agent.agent --type lowpass --target 500000 \
    --stopband-hz 5000000 --stopband-atten 40 --technology breadboard
```

With a non-ideal technology, each ideal component is expanded into its
parasitic equivalent before simulation, and the sweep extends to 100 MHz
because parasitics are invisible below roughly 1 MHz. The model is told which
technology it is designing for and the self-resonance guidance that implies.

The difference is not subtle. A textbook-correct 3rd-order Butterworth for
500 kHz, simulated three ways:

| | passband | cutoff | worst stopband attenuation | verdict |
|---|---|---|---|---|
| `ideal` | +0.00 dB | 542 kHz | 66.0 dB at 5 MHz | **pass** |
| `smd_pcb` | −0.09 dB | 540 kHz | 43.8 dB at 57.5 MHz | pass |
| `breadboard` | −0.09 dB | 528 kHz | **19.0 dB** at 24 MHz | **fail** |

The design is textbook-right and unbuildable on a breadboard: its 318 µH
inductors self-resonate at 2.30 MHz, below the 5 MHz where the requirement
even begins. No choice of component values fixes that.

### Verify a circuit without an LLM

Everything below the agent works standalone and needs no API key.

```python
from spice_agent.simulation import DesignSpec, simulate_and_verify

circuit = {
    "title": "RC lowpass",
    "components": [
        {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
        {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": "1k"},
        {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": "100n"},
    ],
    "analysis": {"type": "ac", "sweep": "dec", "points": 100,
                 "fstart": "1", "fstop": "1meg", "output_node": "out"},
}

result = simulate_and_verify(circuit, DesignSpec("lowpass", 1590))
print(result["message"])
# Simulated lowpass: cutoff 1587.8 Hz vs target 1590.0 Hz (-0.1%). PASS
```

`simulate_get_full_sweep(circuit, technology="breadboard")` returns the whole
sweep as a DataFrame for plotting or your own analysis.

### Explore from the console

No API key needed for any of these:

```bash
python -m spice_agent.paths        # which simulators were found
python -m spice_agent.circuit     # parasitic model + ideal vs as-built netlists
python -m spice_agent.simulation  # one verification, end to end
python -m spice_agent.plotting    # writes a Bode plot
python tests/circuit/test_templates.py    # all 9 reference circuits, measured
```

### Export to LTspice

```python
from spice_agent.export import export

export(circuit, asc_path="out/designs/d.asc", cir_path="out/designs/d.cir")
export(circuit, cir_path="out/designs/built.cir", technology="breadboard")
```

The `.asc` is a drawn schematic — ladder topologies get real wires left to
right, anything else falls back to net labels. The `.cir` runs in **both**
simulators: LTspice needs only the `.ac` directive, while ngspice's batch mode
requires a `.print` line, so both are emitted.

Passing a non-ideal `technology` exports the **as-built** circuit, so what you
open is what was simulated — parasitics and all. On the breadboard profile a
4-component filter becomes 29 elements.

---

## Testing

```bash
python -m pytest -m "not live"   # 221 tests, needs only ngspice  (~55 s)
python -m pytest -m ltspice      #  15 tests, drives LTspice.exe
python -m pytest -m live         #   5 tests, calls Gemini; needs an API key
```

226 total. The LTspice and live tests skip themselves when the binary or the
key is missing, so `python -m pytest` works on a bare checkout.

Two conventions worth keeping if you extend the suite. Expected values come
from circuits that were **measured first**, with the figure recorded in a
comment — guessed thresholds caused several false failures early on. And live
runs append every iteration's proposal to `out/runs/live_runs.jsonl`, because
a live run costs quota and produces a real design; keeping only pass/fail
discards the most interesting output in the suite.

---

## Limitations

**AC analysis only.** `AnalysisSpec` raises on anything else. No step response,
overshoot, ringing or settling time — which are the things a customer actually
complains about. Adding them means a second analysis path and a second parser.

**Two-terminal passives only.** R, L, C and independent sources. No op-amps or
transistors: that needs `.model`/`.subckt` support and relaxing `Component`'s
two-node assumption.

**Passband gain and phase are measured but never checked.** The prompt asks for
unity gain and nothing enforces it.

**Parasitic values are representative, not measured.** This is the biggest
caveat. Inductor winding capacitance (3 pF SMD / 15 pF breadboard) and Q at
self-resonance (20 / 10) are plausible mid-range figures, not measurements, and
they carry every conclusion about buildability. `profile.inductor_srf_hz(L)`
and `profile.inductor_rp(L)` exist precisely so the two can be checked against
a real datasheet or an impedance sweep — the SRF pins the capacitance, the peak
height pins the Q.

**Interconnect parasitics are a lumped estimate, not an extraction.** There is
no layout, so trace lengths genuinely do not exist here. The profiles assume
typical geometry. A tight layout beats them; a sloppy one is worse.

**Ground returns are treated as perfect.** Terminals connected to ground get no
lead inductance, since modelling them would mean per-component local grounds
and so ground bounce — finer-grained than the rest of the model justifies.

**Availability limits are advisory.** `max_practical_inductance` is recorded but
unenforced: nothing stops the agent proposing a 23.87 mH inductor in an 0603
package.

**The feedback does not yet attribute a parasitic failure to a component.** It
correctly says "something is resonating; increasing the order will not help",
but does not name which inductor, give its self-resonant frequency, or point
out that the design would have passed ideally. That is the next thing worth
building.

---

## A note on what this does and does not demonstrate

Simulation-in-the-loop only earns its keep when the model cannot predict the
outcome. For textbook passive filters it can: `fc = 1/(2πRC)` is memorised, and
in testing a cheap fast model one-shot every basic spec to within 0.2 %. The
loop fired only once the spec demanded something with no closed form — a
stopband steep enough to dictate the order, or a design judged as-built with
parasitics.

Two further ideas were tested and **did not** survive. Component-tolerance
yield turned out to be predictable analytically: first-order sensitivity
reproduced a 500-sample Monte Carlo to two decimal places, including the
correlation between constraints. And a correlated-constraint argument for joint
yield came within 2.8 percentage points of simply multiplying the marginals.
The pattern is that a linear circuit with smooth metrics has closed-form
sensitivities by construction, so no amount of extra constraints escapes it.
What does escape it: singular metrics (a matching-limited notch), and
non-linear circuits, where a common-emitter amplifier's optimum sits next to a
cliff that small-signal analysis mispredicts by 431×.
