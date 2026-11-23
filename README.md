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
   agent/  prompts.py ──► Gemini  or  Claude      brief in, circuit JSON out
             │          loop.py    claude_loop.py
             │       (rate_limit.py)  (`claude -p`)
             │               │
             └──► run loop ◄─┘ ──► runlog.py   every iteration on disk
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
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

> `pyproject.toml` does not yet declare runtime dependencies, so the
> `requirements.txt` line is currently required. Worth fixing.

Check that both simulators were found:

```bash
.venv/Scripts/python.exe -m spice_agent.paths
```

The agent has two interchangeable model backends:

- **Gemini** (default): set `GEMINI_API_KEY`.
- **Claude** (`--backend claude`): drives Claude Code's headless mode
  (`claude -p`) on a Claude Pro/Max subscription. Needs the `claude` CLI
  installed and logged in; it is looked up via `CLAUDE_BIN`, then `PATH`, then
  the binary bundled with the VS Code extension.

Everything else — simulation, verification, export, plotting — works without
any API key or CLI.

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
| `stopband_end_hz` | `None` | LP/HP: far edge (`--stopband-end`); defaults to the judged-window edge |
| `technology` | `"ideal"` | `ideal`, `smd_pcb` or `breadboard` |
| `judge_lo_hz` / `judge_hi_hz` | `None` | the judged range (`--judge-lo/--judge-hi`); default two decades either side of the target |
| `max_peaking_db` | `3.0` | max passband gain above its level (`--max-peaking`, `off` = `None`); not for bandpass |
| `source_ohm` | `0.0` | source resistance the harness inserts before `in` (`--source-ohm`) |
| `load_ohm` | `None` | load resistance the harness puts on `out` (`--load-ohm`); `None` = unloaded |
| `supply_pos_v` / `supply_neg_v` | `None` | supply rails at nodes `vcc` / `vee` (`--supply`, or `--supply-pos/--supply-neg`); `None` = passive only |
| `passband_gain_db` | `None` | required passband gain (peak gain for bandpass), `--gain`; `None` = not checked |
| `gain_tolerance_db` | `1.0` | tolerance on that gain, `--gain-tol` |
| `max_supply_current_a` | `None` | max current from each rail (`--max-supply-current 1m`); `None` = no budget |
| `allow_active` | `True` | `False` (`--passive-only`) forbids op-amps even when rails exist |

`describe()` renders it as the line the model actually reads:

```python
>>> DesignSpec("lowpass", 10_000, stopband_hz=100_000, stopband_atten_db=40).describe()
'lowpass filter, cutoff frequency (-3dB point) 10000 Hz (+/-10%);
 at least 40 dB below passband everywhere from 100000 Hz to 1e+06 Hz;
 passband may not peak more than 3 dB above its level'
```

### What is judged: the window

Every check is made inside a **judged window**, by default two decades
either side of the target (10 kHz → 100 Hz – 1 MHz), and the sweep always
covers it. That includes the passband reference, the bandpass and notch shape
checks, the peaking check and, unless `--stopband-end` says otherwise, the
stopband. What the response does *outside* the window is reported to the
model and the user ("not judged: above 5 MHz the response sags to −18 dB
at 100 MHz"), but it cannot fail a design.

The reason is that the user asked for a filter at the target. Three decades
away, a breadboard's parasitics dominate whatever was designed: an inductor
there is a 15 pF capacitor. Judging that by default rejected good designs.
Live, a −50 dB notch exactly on 50 kHz was rejected because its gain at
100 MHz was −19 dB. Someone who does care about 100 MHz says so, with
`--judge-hi 1e8` (or an explicit `--stopband-end`, which widens the window
to include it), and the sweep is extended to match.

### The world around the filter: source and load

By default the filter is driven by an **ideal voltage source (0 Ω)** and
`out` is **unloaded**. The harness adds nothing that was not asked for.
Real filters sit between a source with some output resistance and a load, and
those resistances do the damping. Textbook ladder tables (the `g_k` values in
the lowpass hint) assume them: a Butterworth ladder computed for a 1 kΩ source
peaks +3.9 dB and misses its cutoff by 8.6% when driven from an ideal one.

`--source-ohm` and `--load-ohm` (`source_ohm` / `load_ohm`) make the
surroundings part of the requirement. The harness inserts `R_SOURCE` between
`V1` and `in` and `R_LOAD` from `out` to ground. Both are ideal, never get
parasitics, and are written into the exported `.cir`/`.asc`. They are
reserved, so the model cannot add, remove or change them, and the prompt
tells it they are there. All checks are relative to the resulting passband
level, so a 50 Ω / 50 Ω ladder's −6.02 dB passband is correct, not a failure.

### Active parts: op-amps

Give the spec supply rails and the model **may** use op-amps; it is not
told to:

```bash
python -m spice_agent.agent --type bandpass --target 10000 --bandwidth 2000 \
    --supply 12 --gain 0 --max-supply-current 1m
```

- **Rails mean "allowed", never "preferred".** The spec describes what the
  filter must do and the world it lives in, never how to build it. With rails,
  the prompt offers passives and op-amps as equal options, with a passive and
  an active hint each. It adds a TRADE-OFFS note filled with this spec's own
  numbers: gain above 0 dB needs an op-amp; a load favours an op-amp output;
  "a series-RLC band-pass with Q = 10 at 50 kHz needs about 31.8 mH, but
  inductors are available only up to 10 mH on smd_pcb"; the current budget;
  op-amp swing and gain-bandwidth limits; passives need no power. With the
  earlier, one-sided prompt the model used an op-amp first in all nine powered
  live runs, whatever the requirements said. `--passive-only` keeps rails in
  the spec but forbids op-amps.
- **What exists is enforced.** Inductors larger than the technology offers
  (10 mH on `smd_pcb`, 100 mH on `breadboard`, unlimited on `ideal`) fail the
  design as `NOT BUILDABLE`. The design is still simulated, so the model sees
  its response, and the diagnosis files it as a design problem.
- **A supply-current budget** (`--max-supply-current`, per rail) is measured
  from the operating point. On ideal technology, where op-amps are simulated
  ideal and draw nothing, each grade's catalogue current is charged instead.
  Exceeding it fails the design as `OVER BUDGET`, naming each op-amp's share.
  A passive design draws nothing.
- **Catalogue properties bind on every technology.** A grade's supply rating
  and supply current are properties of the part, so they apply even when the
  op-amp is simulated as ideal. Electrical behaviour (gain-bandwidth, swing,
  bias current) follows the technology.

- **The model chooses a grade; the technology decides the physics.** This is
  the same split as for passives. A passive is a type plus a value, and
  `--technology` decides whether it is simulated ideal or with its
  parasitics. An op-amp is a type plus a **grade**, a real design trade-off
  in bandwidth, slew rate, supply range and bias current:

  | Grade | Class | GBW | Slew | Swing / input range | Supply |
  |---|---|---|---|---|---|
  | `OPAMP_GP1` | bipolar general purpose (LM358/741 class) | 1 MHz | 0.5 V/µs | 1.5 V from each rail | 3–32 V |
  | `OPAMP_FET3` | JFET input (TL07x class) | 3 MHz | 13 V/µs | 1.5 V / 3 V from each rail | 10–36 V |
  | `OPAMP_RR10` | CMOS rail-to-rail | 10 MHz | 6 V/µs | rail-to-rail | 2.7–5.5 V |

  - With `--technology ideal`, every op-amp is simulated as the ideal one,
    just as every inductor is then perfect.
  - With a real technology, the grade's macromodel is used, plus a lead on
    every pin.

  `OPAMP_IDEAL` (1 GHz, rail-to-rail) is therefore not on the menu. It is
  the ideal technology's expansion and the diagnosis's "what if this op-amp
  were ideal" part. When it *was* offered, all four live runs picked it.
  Each grade is a generic macromodel written for this project from typical
  datasheet figures, so it can be committed. Vendor models are not bundled:
  their licences generally forbid redistribution and some are encrypted.
  The model wires the pins (`{"type": "opamp", "part": "OPAMP_FET3", "pins":
  {"in+", "in-", "vcc", "vee", "out"}}`), and the harness inlines the
  `.subckt`.
- **The rails belong to the harness.** `V_SUPPLY_POS` / `V_SUPPLY_NEG` hold
  `vcc` / `vee`, ideal and outside the parasitics. The design may connect to
  the rails but never drive them. With no rails in the spec, the prompt says
  "passive only", and an op-amp is answered with that.
- **The DC operating point is checked.** An AC sweep linearises around the
  bias point, so it draws a believable curve even for an op-amp pinned at a
  rail. Every active run therefore solves `op` first, and a design fails with
  `BIAS PROBLEM` when:
  - an output sits at its swing limit (saturated);
  - an input leaves its common-mode range;
  - an input has no DC path, the capacitor-coupled-input mistake: the bias
    current charges the node until the output rails (−10.53 V on `OPAMP_GP1`
    in the tests). This fails even with ideal technology, because the node's
    DC level is then undefined;
  - the part is on a supply it is not rated for (on every technology: the
    rating belongs to the part).

  The message names the cause.
- **Grade limits are diagnosed.** On a real technology, the per-component
  check also makes each op-amp ideal on its own. On `smd_pcb`, a 10 kHz MFB
  band-pass passes with the `OPAMP_FET3` grade, but its bandwidth comes out
  10.6% low with the 1 MHz `OPAMP_GP1`. The diagnosis says "U1 (OPAMP_GP1)
  alone would make the design PASS … a faster grade, or less gain or Q per
  stage".
- `--gain` sets an absolute passband-gain target, since active filters can
  have gain; without it only the relative checks apply.

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

### Passband peaking

The passband may not rise more than `max_peaking_db` (default **3 dB**) above
its own level, which catches undamped resonances. This used to be unchecked,
and a live run passed with **+12 dB** of peaking: the undamped L-C in
`out/runs/live/2026-11-12T15-31-54_lowpass-10000Hz_claude-haiku/` (see its
`history.png`). Re-judged today, it fails. The limit is loose on purpose
(Chebyshev-style ripple still passes); tighten it with `--max-peaking 0.5` or
switch it off with `--max-peaking off`. Bandpass has no peaking check, because
its passband *is* the peak.

### What is *not* checked

The absolute passband level is not checked: the prompt asks for unity gain
(when unterminated), but a design sitting at −6 dB still satisfies every check,
because everything is judged relative to the passband. Phase is simulated and
plotted but never judged. A notch has no width requirement, so a notch so
wide that the window edges sit on its skirts is judged against a passband that
is already attenuated.

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

What is judged, and around what:

| Flag | Default | |
|---|---|---|
| `--judge-lo HZ` / `--judge-hi HZ` | target ÷ 100 / × 100 | the judged range |
| `--stopband-end HZ` | window edge | far edge of a lowpass/highpass stopband |
| `--max-peaking DB` | `3` | passband peaking limit, or `off` |
| `--source-ohm OHM` | `0` (ideal source) | fixed source resistance |
| `--load-ohm OHM` | none (unloaded) | fixed load resistance |
| `--supply V` | none (passive only) | ±V rails at `vcc` / `vee`; or `--supply-pos` / `--supply-neg` |
| `--gain DB` / `--gain-tol DB` | not checked / `1` | absolute passband-gain target |
| `--max-supply-current A` | no budget | max current per rail, e.g. `1m` |
| `--passive-only` | off | no op-amps, even with rails |
| `--diagnosis full\|basic\|off` | `full` | parasitics diagnosis of failed non-ideal designs |

`--max-iterations` caps the budget (default 6 for Gemini, 5 for Claude) and
`--model` overrides the model (default `gemini-3.5-flash` / `haiku`).
`--min-interval`, `--max-retries` and `--initial-backoff` control Gemini API
pacing; a daily-quota 429 is detected from the server's own `RetryInfo` and
fails fast rather than burning the retry budget on waits that cannot succeed.

### Use a Claude subscription instead of the Gemini API

```bash
python -m spice_agent.agent --backend claude --type lowpass --target 10000 \
    --stopband-hz 100000 --stopband-atten 40
```

`claude -p` has no native function calling here, so the model is forced to
reply with JSON matching the same circuit schema (`--json-schema`) and the
harness runs the simulation itself. One session is resumed across iterations,
so each turn only sends the new simulation result.

Because a subscription quota is shared with interactive use, the defaults are
deliberately frugal: `haiku` at `--effort low`, every built-in tool, MCP
server, skill, hook and settings file switched off, and the call run from an
empty temp dir so no `CLAUDE.md` is loaded. Token use is tallied and printed at
the end, and a usage-limit or auth error stops the run immediately instead of
retrying.

### Run logs

Every agent run writes a self-contained folder (disable with `--no-log`,
relocate the root with `--runs-dir` or `SPICE_AGENT_RUNS_DIR`). Folders are
filed by whether a real model was called:

```
out/runs/
    live/        gemini and claude runs — real data, keep it
    dry/         everything else (analytic baselines, scripted fakes) —
                 safe to delete whenever you like
```

`live` is decided by the backend; code calling `RunRecorder.start` directly
can force either with `live=True/False`. The test suite never writes here:
`tests/conftest.py` points the run log at a temp directory.

```
out/runs/live/2026-11-10T14-32-07_lowpass-10000Hz_claude-haiku/
    run.json            manifest: request, spec, backend/model, environment,
                        per-iteration timing/usage/verdict, status, outputs
    request.txt         the verbatim command line
    spec.json           the DesignSpec built from it
    system_prompt.txt   the exact brief sent to the model
    iter_01/            prompt, command, raw model response, proposed circuit,
                        netlist(s), ngspice.log, sweep.csv, result.json
    history.png         every iteration's Bode curve + its component values
    final/              circuit.json/.asc/.cir and bode.png — only on a pass
```

`history.png` is drawn after every run, passed or failed, straight from the
saved `sweep.csv`, `circuit.json` and `result.json` files; nothing is
re-simulated. Curves go from light (first attempt) to dark (latest). The
grey zones are the target cutoff band and the stopband region the response
must stay out of. The table underneath lists each component position per
iteration, with identical parallel parts collapsed (`20x 30n`), and what each
iteration measured. The region outside the judged window is greyed out and
the peaking limit drawn as a dashed line. To redraw it for any existing run:

```bash
python -m spice_agent.run_plot out/runs/live/<run-folder>
```

Each of `live/` and `dry/` has its own `index.jsonl`, one summary line per
run. The manifest is written
before the first model call and finalised on exit — including errors and
Ctrl+C, which end with status `error` / `aborted` — so a crashed run still
leaves a readable record. Environment variables whose names contain `KEY`,
`TOKEN`, `SECRET` or `PASSWORD` are redacted from everything written.

### Design for a real board

```bash
python -m spice_agent.agent --type lowpass --target 500000 \
    --stopband-hz 5000000 --stopband-atten 40 --technology breadboard \
    --source-ohm 1000
```

With a non-ideal technology, each ideal component is expanded into its
parasitic equivalent before simulation, and the sweep extends to 100 MHz
because parasitics are invisible below roughly 1 MHz. The model is told which
technology it is designing for and the self-resonance guidance that implies.

The stopband is still judged only inside the window (here up to 50 MHz,
two decades above 500 kHz). Add `--judge-hi 1e8` to hold the design to
100 MHz.

The difference is not subtle. A textbook 3rd-order Butterworth L-C-L for
500 kHz (318.3 µH, 636.6 pF, 318.3 µH, 1 kΩ load), simulated three ways with
the 1 kΩ source it was designed for:

| | passband | cutoff | worst stopband attenuation | verdict |
|---|---|---|---|---|
| `ideal` | −6.02 dB | 500 kHz | 60.0 dB at 5 MHz | **pass** |
| `smd_pcb` | −6.07 dB | 495 kHz | 40.9 dB at 39.8 MHz | pass |
| `breadboard` | −6.07 dB | 482 kHz | **18.1 dB** at 8.3 MHz | **fail** |

Without the source resistance the same ladder peaks +3.9 dB and lands at
543 kHz (+8.6%). It fails the default peaking limit even with ideal parts,
because it was designed for a termination it does not get.

The design is textbook-right and unbuildable on a breadboard: its 318 µH
inductors self-resonate at 2.30 MHz, below the 5 MHz where the requirement
even begins. No choice of component values fixes that.

### What the model gets back

Each failed iteration returns a verdict message written to be acted on, plus
the measured numbers (rounded to 4 significant figures for the model; the run
log keeps full precision). All advice and explanation text, in the prompt and
in the feedback, comes from one documented catalogue, `src/spice_agent/hints.py`.
It is readable as [HINTS.md](HINTS.md) and states, for every hint, when it
applies, where it stops being valid, why it holds and its source. Hints convey
hard facts only, and rules of thumb are always labelled as such (see
ARCHITECTURE.md). Depending on what failed, the message carries:

- **Frequency miss:** the exact correction. Multiplying every L and C by
  k = measured / target moves the whole response onto target without changing
  its shape (`frequency_scale_factor`).
- **Stopband too shallow at the edge:** the order is too low. The message says
  how steep the design currently is (`effective_order`) and which order a
  Butterworth needs for the requirement plus 3 dB margin (`suggested_order`).
- **Stopband recovers inside the band:** something resonates or leaks around
  the filter, so more order alone will not help.
- **Both at once** are reported as two separate problems. Previously only one
  was named, which told the model "more order will not help" even when the
  edge was short as well.
- **The shape of the band:** attenuation at the edge, at each decade, at the
  far end and at the worst point.
- **Peaking:** how far the passband rises, where, and why. With an ideal
  source and no load, nothing damps an L-C section.
- **Everything, always.** A failed shape check no longer hides the rest: centre,
  bandwidth, depth, peaking and stopband are all reported in every result.
- **Outside the window:** notable behaviour beyond the judged range, marked as
  not judged.
- **Repeated design:** a circuit identical to an earlier one (ignoring ids,
  order and title) is not simulated again. The model is told so, along with
  that attempt's verdict. A second repeat in a row ends the run with status
  `stalled`; live, a model once resubmitted the same bandpass three times.
- **Parasitics diagnosis** (non-ideal technology only, `--diagnosis`): extra
  local simulations, no model tokens.
  - **Design or construction?** Failed checks that ideal parts would *also*
    fail are named as design problems and never blamed on parasitics.
  - **Per parasitic group:** for a construction failure, which group costs the
    most, measured both on its own and by removing it from the full model,
    and what helps against it. For the textbook 10 kHz ladder judged to
    100 MHz on a breadboard, the 2 pF input–output coupling alone takes the
    stopband from 67.5 to 1.5 dB. Losses (inductor winding resistance, ESR,
    contact resistance) are a group too, measured against a lossless run. In
    a live 10 Ω lowpass, a 210 µH inductor's 4.35 Ω winding resistance pulled
    the cutoff from 10 kHz to 6 kHz, and the message quotes each inductor's
    resistance.
  - **Per component** (`full`, the default): each part, or each group of
    identical parallel parts, is made ideal on its own while the rest stays as
    built. The message then names the part to change: on the live 100 kHz
    bandpass, "L1 (100u) alone would make the design PASS".
  - **Self-resonances** of the parts inside the judged band.

  `basic` skips the per-component check for quicker runs, and `off` skips
  the diagnosis. Its cost is logged as `duration_s`: about 1.5 s per failed
  iteration on a 3-part breadboard filter. It describes what is wrong and
  where, and never proposes values; that stays the model's job.

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
sweep as a DataFrame for plotting or your own analysis. Phase is in degrees.

For a non-ideal technology, the Bode plot overlays the ideal-component response
(grey) on the as-built one, so the parasitic damage is visible at a glance.

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
python -m pytest -m "not live"   # 733 tests, needs only ngspice  (~100 s)
python -m pytest -m ltspice      #  15 tests, drives LTspice.exe
python -m pytest -m live         #   5 tests, calls Gemini; needs an API key
```

738 total (the 15 LTspice tests are part of the "not live" set). The Claude
backend is tested against a scripted runner, so no test calls `claude`. The LTspice and live tests skip themselves when the binary or the
key is missing, so `python -m pytest` works on a bare checkout.

Two conventions worth keeping if you extend the suite. Expected values come
from circuits that were **measured first**, with the figure recorded in a
comment — guessed thresholds caused several false failures early on. And live
runs append every iteration's proposal to `out/runs/live/live_runs.jsonl`, because
a live run costs quota and produces a real design; keeping only pass/fail
discards the most interesting output in the suite.

---

## Limitations

**AC analysis only.** `AnalysisSpec` raises on anything else. No step response,
overshoot, ringing or settling time — which are the things a customer actually
complains about. Adding them means a second analysis path and a second parser.

**Op-amps, but no transistors yet, and AC plus operating point only.**
Op-amps come from the generic library. Transistors (stage 3) and transient
behaviour (stage 2: slew-rate distortion, clipping at real signal levels,
oscillation) are not simulated yet. The slew rate is in the models but nothing
measures it.

**The op-amp models are generic, not vendor-accurate.** They have one pole, no
offset voltage, no noise, no output current limit and no supply rejection. A
pass means "a part of this class would work", not "this exact part will".

**No LTspice schematic for op-amp circuits.** The `.asc` export is skipped
with a recorded reason; the `.cir` carries the whole circuit, models inline.

**Passband level and phase are measured but never checked.** Peaking above the
passband is checked (3 dB by default); the absolute level is not, and neither is
phase.

**Peaking of an undamped resonance is only as accurate as the sweep.** A truly
undamped L-C's peak is sharper than 100 points per decade can resolve, so the
reported figure (+35 to +71 dB for the same circuit, depending on where the
samples land) is a lower bound. That does not change the verdict, which is
far over 3 dB either way.

A notch had the same problem, and there it did change things, so a notch is
refined. On the 100-per-decade grid an ideal series-RLC zero falls between
two samples. Live, it read 31.5 dB instead of infinitely deep, so the
diagnosis wrongly said "ideal parts fail it too" for a notch limited only by
the inductor's winding resistance. For a notch spec, every sweep (the
verdict's, each diagnosis variant's and the plot's) gets one extra dense
linear sweep between the samples either side of its minimum. An ideal zero
now reads about 100 dB, and a lossy notch reads its true depth.

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

**Only inductor size is checked for availability.** Inductors are limited
per technology. Capacitor and resistor ranges are not, and neither are
standard value series (E12/E24), which is the next step (a light passive
catalogue).

**Parasitic attribution is per group, not per pair.** Each group is measured
on its own and by removal, and ranked by the larger effect. That catches both
a parasitic that does all the damage alone and one that only hurts in
combination, but it does not say *which* pair interacts. The advice attached
to each group is a rule of thumb, not a computed fix.

**A spec can be infeasible and nothing says so.** On a breadboard, the
input–output coupling puts a floor on attenuation at high frequencies, which
no passive design gets below. With the default window this rarely matters;
with `--judge-hi 1e8` it can make a spec unreachable, and the loop then spends
its whole budget (or stalls on repeats). The diagnosis names the coupling, but
nothing proves infeasibility.

**The judged window is a default, not a physical truth.** Two decades either
side is a convention. A user whose system has a 50 MHz interferer must say so
with `--judge-hi`; nothing warns that one was needed.

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
