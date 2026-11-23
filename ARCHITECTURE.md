# Architecture

An LLM designs a passive filter; a real SPICE simulator decides whether it
worked; the measured result goes back to the model so it can revise. This
document describes where each part of that lives and why.

## The one invariant

**The LLM never writes SPICE.** It fills in a JSON schema describing
components; a deterministic, independently testable Python layer compiles
that into a netlist. This was an early decision to avoid spending iterations
on syntax errors, and it has held up — every failure the model sees is about
circuit behaviour, never about text format.

A second rule follows from it: **the model is told what to achieve, never how.**
A spec is a frequency, a tolerance, an attenuation. Topology and filter order
are the model's job to derive.

A third governs everything the harness says back: **feedback and hints convey
hard facts only.** A fact is one of two kinds:

- **measured on this design**, by simulating the circuit the model proposed:
  "bandwidth 9.6% too wide", "L1's winding resistance costs 1 dB", "with
  an ideal U1 it would pass", or an exact consequence of one, such as the L/C
  scale factor that moves a measured frequency onto target;
- **read from the catalogue or the spec**, checkable against the library and
  the user's numbers: "no grade faster than OPAMP_FET3 is rated for ±12 V",
  "inductors above 10 mH are not available on smd_pcb".

Feedback never makes claims about designs that were not simulated, such as
"this topology is at its limit" or "a passive design cannot reach this".
Reliably defining when a topology class is exhausted would mean reasoning
over every value combination and variant of it, which the harness cannot
verify. It would also be wrong whenever the harness simply did not think of
something: in the passive-only 50 kHz run, a different passive arrangement
might well reach the gain the series RLC could not. That reasoning is the
design work, and it stays with the model, for the same reason the harness
never searches for component values. Any new hint or diagnosis text should be
checked against this rule: if it cannot be traced to a simulation of this
circuit, or to the catalogue or spec, it does not belong in the feedback.

The rule is enforced by keeping all such text in one place,
`src/spice_agent/hints.py`, the **hint catalogue**. Each hint carries:
- its **kind** (fact, derived, catalogue, explanation, rule of thumb);
- **when it applies** and **where it stops being valid**;
- **why** it holds, and its **source**.

The code that measures still decides *when* a hint fires (`verify.py`,
`diagnosis.py`, `bias.py`, ...), but takes the *words* from the catalogue
(`render("stopband.rolloff_per_order")`), which prefixes every rule of thumb
with its label. `HINTS.md` is generated from it (`python -m
spice_agent.hints`). `tests/test_hints.py` checks four things:
- every hint is documented and used;
- no rule of thumb renders unlabelled, and no model-facing string elsewhere
  says "rule of thumb";
- no hint uses phrasing that claims things about untested designs;
- `HINTS.md` is current.

## The pipeline

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

## Packages

| Path | Holds |
|---|---|
| `circuit/model.py` | `Component`, `AnalysisSpec`, `Circuit`. Validation and netlist compilation, including the harness's source/load terminations (`Circuit.terminated`). No simulator or LLM knowledge. |
| `circuit/parasitics.py` | `TechnologyProfile` and the built-in profiles. **Edit the parasitic numbers here.** |
| `circuit/expansion.py` | Ideal component → parasitic equivalent circuit. |
| `simulation/ngspice.py` | The only module that knows ngspice is a process. |
| `simulation/metrics.py` | Pure measurement of a sweep. No notion of pass or fail. |
| `simulation/spec.py` | `DesignSpec` — the requirement model, including the judged window, the peaking limit and the terminations. |
| `simulation/verify.py` | `check_filter_spec` — the verdict, and the feedback text. |
| `simulation/diagnosis.py` | Why a non-ideal design failed: design vs construction, per-parasitic-group and per-component attribution, part self-resonances. Levels `full` / `basic` / `off`. |
| `simulation/pipeline.py` | `simulate_and_verify` — the whole path in one call, including the diagnosis. For a notch spec every sweep is refined around its minimum first (`refine_notch`), so the depth is not limited by the grid. |
| `agent/analysis.py` | `analysis_for(spec)` — which AC sweep a spec is simulated with. A leaf module so prompts, loop and CLI need not import each other. |
| `agent/rate_limit.py` | Pacing and retry for the Gemini API. Knows nothing about circuits. |
| `agent/prompts.py` | The design brief. Pure string building, so it can be asserted. |
| `agent/tool_schema.py` | `CIRCUIT_JSON_SCHEMA`, the JSON the model fills in, and the Gemini tool declaration wrapping it. |
| `agent/loop.py` | `run_agent_loop` — propose, simulate, feed back, repeat (Gemini function calling). Also `call_simulate_circuit_tool`, shared by both backends. |
| `agent/claude_loop.py` | `run_claude_loop` — the same loop over `claude -p` with forced JSON output. |
| `agent/cli.py` | Argument parsing, backend choice, and writing outputs when a design passes. |
| `export/netlist.py` | `.cir` text, plus the as-built (parasitic-expanded) circuit. |
| `export/schematic.py` | Symbol geometry, layout, `.asc` writer. |
| `export/ltspice_cli.py` | Driving `LTspice.exe` for cross-checks. |
| `runlog.py` | `RunRecorder` — one folder per agent run with every prompt, reply, netlist, sweep and verdict. |
| `plotting.py` | Annotated Bode plot; overlays the ideal response for non-ideal technologies. |
| `run_plot.py` | `history.png` for a run folder: all iterations overlaid, plus a component/metric table. Reads only the saved files. |
| `models/opamps.py` | The op-amp library: `OpampModel` parameters, the `.subckt` generated from them, the prompt summary. **Add parts here.** |
| `simulation/raw.py` | ASCII raw files from ngspice (`write`): the operating point today, transient later. |
| `simulation/bias.py` | `check_bias`: saturation, input range, DC paths and supply rating of every op-amp, from the operating point. |
| `hints.py` | The hint catalogue: every piece of advice and explanation the model is given, documented (kind, when it applies, validity, why, source). Generates `HINTS.md`. **Edit advice text here.** |
| `paths.py` | Finding ngspice and LTspice. Override here or by env var. |
| `units.py` | SPICE value parsing (`"23.87m"` → `0.02387`). |
| `templates.py` | Nine hand-written reference netlists with known behaviour. |

Import from a package, not its modules — `from spice_agent.simulation import
DesignSpec` — so the internal split stays an implementation detail.

## Design decisions worth knowing

**Multi-pin parts are named pins over the same `nodes` list.** An op-amp is
`{"type": "opamp", "part": ..., "pins": {...}}`. `Component` derives `nodes`
from the pins in the subcircuit's order, so everything that walks nodes (the
expansion, the DC-path graph, the netlist) works unchanged. Only code that
assumed exactly two nodes had to learn about `MULTI_PIN`. Pin order matters
(in+ vs in-), so repeat detection and diagnosis grouping keep it.

**Constraints decide passive vs active, never the prompt.** Rails make
op-amps *allowed*. The prompt then offers both options with equal weight
and a TRADE-OFFS note built from the spec's own numbers (`prompts.
build_tradeoff_note`). The checks that actually separate the two are
enforced: `simulation/availability.py` applies the technology's
`max_practical_inductance` (`NOT BUILDABLE`), and
`bias.check_supply_budget` applies `max_supply_current_a` per rail
(`OVER BUDGET`). Both still let the design be simulated and reported, and
both are design-level failures for the diagnosis. Catalogue properties of
a grade (supply rating, quiescent current) bind on every technology;
electrical behaviour follows the technology. When the diagnosis names an
op-amp, `diagnosis.grade_advice` lists only faster grades rated for these
rails and within the budget, or says there are none and points to lower Q
per stage. Advice the model could not act on once sent a live run off to a
passive design it could not finish.

**The model never writes a model, and never chooses physics.** It picks an
op-amp *grade* (`SELECTABLE_OPAMPS`), which plays the role a value plays for a
passive. The technology then decides the simulation, exactly as for passives:
`ideal` swaps every op-amp for `IDEAL_OPAMP` in `Circuit.to_netlist` (and the
bias check judges against the ideal part's limits), while a real technology
inlines the grade's `.subckt` and adds pin leads. `IDEAL_OPAMP` is refused at
the tool boundary (`call_simulate_circuit_tool`): offered next to real
grades, the model picked it every time. The library parameterises generic
classes of part from typical datasheet figures, so it is ours to commit;
vendor models are not bundled.

**Active circuits are judged at their bias point first.** An AC sweep is a
linearisation, so a saturated op-amp still yields a smooth curve. When a
circuit has active parts, the control block solves `op` and writes it as an
ASCII raw file before the AC sweep. `bias.check_bias` reads it against each
part's library limits, and a bias failure fails the design whatever the AC
result says. Passive circuits run exactly as before (no `op`).

**`spec.py` and `verify.py` are deliberately adjacent.** Every field added to
`DesignSpec` needs a matching branch in `check_filter_spec`, and a field with no
check is a requirement that silently does not apply. They are separate files
only because `verify.py` would otherwise dominate the module.

**Only what the user asked for can fail a design; everything else is
information.** Every check runs inside `DesignSpec.judge_window()`, by
default two decades either side of the target, and the sweep always covers
it. The response outside is reported (`outside_window`) and never judged.
This rule came from live breadboard runs: judging the bandpass and notch
shape at the 100 MHz sweep end rejected designs that met every stated
requirement (a −50 dB notch exactly on 50 kHz, failed for −19 dB at
100 MHz), and pushed the model to make them worse. A user widens the window
explicitly (`judge_lo_hz` / `judge_hi_hz`, or an explicit stopband end).
`describe()` and the prompt state the judged range, so the brief and the
verdict still cannot disagree.

**The harness adds nothing the user did not ask for.** Source and load
resistances are spec fields (`source_ohm=0`, `load_ohm=None`: ideal source,
open output). When set, `Circuit.terminated()` inserts `R_SOURCE` / `R_LOAD`
at netlist time. They are ideal (outside the parasitic expansion and its
stray node capacitance), their ids and the `src_ext` node are reserved so a
designed circuit cannot touch them, and the export writes them out. Checks are
relative to the passband level, so the −6 dB of a matched pair is not a
failure.

**No check ends the verdict early.** A failed shape check (bandpass rolloff,
notch symmetry) is one failure among the others, so centre, bandwidth, depth,
peaking and stopband are always measured and reported. The only early returns
left are a response that is the wrong filter type or has no measurable
cutoff.

**Failure messages are written to be acted on, not just read.** The same 20 dB
shortfall means different things in different places: at a stopband edge the
filter order is too low, deeper in the band something is resonating or leaking.
Those need different fixes, so `verify.py` diagnoses each one independently and
reports both when both apply (`stopband_causes`). Naming only one used to tell
the model "more order will not help" while the edge was also short. Frequency
misses come with the exact L/C scale factor, and an order shortfall comes with
a suggested Butterworth order.

**The loop refuses to re-simulate a repeated design.** `loop.design_key`
identifies a design by its components (type, unordered nodes, numeric value),
ignoring ids, order and title. A repeat is answered with the earlier verdict
and not simulated again, and a second repeat in a row ends the run as
`stalled`. Both backends share `RepeatGuard`.

**The verdict and the diagnosis are separate layers.** `verify.py` judges one
sweep and knows nothing about the circuit. `diagnosis.py` needs the circuit and
the technology, because it re-simulates: once with ideal parts and once with
each parasitic group on its own and once with each group removed. Both are
needed because parasitics interact. On one ladder, removing the stray coupling
made the stopband *worse*, while on its own it is the whole problem. On
another, every group alone was harmless, while removing the winding
capacitance won back 21 dB. Groups are ranked by the larger effect; the
module docstring has the numbers. It only runs for a failed non-ideal design, never
changes the verdict. It diagnoses only failures that ideal parts would pass;
a check that ideal parts also fail is a design problem and is named as one,
never blamed on parasitics. At level `full` (the default) it also re-simulates
with each component (or group of identical parallel parts) made ideal on its
own (`ideal_ids` in the expansion), which names the part to change. Losses
(winding resistance, ESR, contact resistance) form their own group, but the
isolation baseline keeps them on: real parts are never lossless, and a
lossless baseline made other groups' damage vanish into interactions with
them. Losses are charged against a separate lossless run instead. `basic`
skips that, and `off` (or `diagnose=False`) skips the diagnosis. It never
proposes values: a harness that searched for them would be measuring itself.

**The model sees a trimmed copy of the result.** `agent.loop.model_feedback`
rounds floats to 4 significant figures and drops fields the message already
spells out. Gemini receives it as the function response; the Claude backend
gets `feedback_text`, which puts the message first and the numbers after. Run
logs and `run.history` keep the full-precision result.

**The stopband is checked across a band, not at one frequency.** A single-point
check can be satisfied by a response that dips there and recovers — which is
exactly what an inductor's self-resonance does. `metrics.worst_attenuation`
takes the minimum over the whole band.

**Parasitic expansion happens at the last possible moment**, inside
`Circuit.to_netlist()`. Everything upstream — validation, the tool schema, the
exporter's layout logic — works on the ideal circuit the designer chose. The
default profile is `ideal`, so the parasitic path is entirely opt-in.

**Parasitics push the sweep to 100 MHz.** Below about 1 MHz they are invisible,
so a short sweep would make the feature a no-op. Ideal mode keeps 1 Hz–1 MHz.

**`Circuit.to_netlist` imports expansion lazily.** `expansion.py` needs `GROUND`
from `model.py`, so a module-level import would be circular. The function-level
import is the cycle break, and it is commented as such at the call site.

**The model call is injectable in both backends.** `run_agent_loop` takes a
Gemini `client`, `run_claude_loop` a `runner(cmd, prompt)`. That is what allows
both loops to be tested against scripted fakes with no network or CLI — see
`tests/_support/fake_gemini.py` and `tests/agent/test_claude_loop.py`.

**Both backends share one tool implementation.** Gemini calls
`simulate_circuit` through native function calling; the Claude backend has no
tools and is instead forced (`--json-schema`) to reply with the same
`CIRCUIT_JSON_SCHEMA`, which the harness then passes to the same
`call_simulate_circuit_tool`. Feedback is therefore identical whichever model
is driving. Both return an `AgentRunResult`.

**The Claude backend fails fast on quota.** A subscription's limits are shared
with interactive use, so a usage-limit or auth error raises immediately rather
than entering a retry loop, and every call runs with tools, MCP, skills, hooks
and settings disabled from an empty temp directory to keep requests small.

**Run logging is threaded through as a `capture` dict and a recorder.**
`simulate_and_verify` and `run_ngspice_ac` optionally fill a `capture` dict
(netlist, ideal netlist, ngspice output, sweep); the loops hand it to a
`RunRecorder`. `RunRecorder.disabled()` accepts every call and does nothing, so
call sites never branch on whether logging is on. The recorder is a context
manager in `cli.py`, which guarantees a final `run.json` even on an exception.

**Netlist titles are forced to ASCII.** Models write things like "fc ≈ 1 kHz";
the netlist is written in the platform encoding, so non-ASCII crashed the write
on Windows and surfaced as a bogus circuit error.

**ngspice's `vp()` is in radians.** `run_ngspice_ac` converts to degrees, so
every `phase_deg` column really is degrees.

## Tests

`tests/` mirrors `src/spice_agent/`. Shared helpers live in `tests/_support/`,
importable from any subdirectory because `pyproject.toml` sets
`pythonpath = ["tests"]`.

Three markers divide them by what they need:

```bash
python -m pytest -m "not live"     # default: needs only ngspice (~55 s)
python -m pytest -m ltspice        # also drives LTspice.exe
python -m pytest -m live           # also calls Gemini; needs GEMINI_API_KEY

No test calls the `claude` CLI; the Claude backend runs against a scripted
runner.
```

The LTspice and live tests skip themselves automatically when the binary or
the API key is absent.

Two conventions are worth keeping. Expected values come from circuits that were
**measured first**, with the figure recorded in a comment — guessed thresholds
caused several false failures early on. And live runs append to
`out/runs/live/live_runs.jsonl` with every iteration's proposal, because a live run
costs quota and produces a real design; keeping only pass/fail throws away the
most interesting output in the suite.

## Generated output

Everything under `out/` is produced by a run and is git-ignored:
`out/designs/` for `.asc` and `.cir`, `out/plots/` for Bode plots, and
`out/runs/` for run logs. Runs are split by mode: `out/runs/live/` holds runs
that called a real model (the `gemini` and `claude` backends) plus the test
suite's `live_runs.jsonl`, and is the data to keep; `out/runs/dry/` holds
everything else (analytic baselines, scripted fakes) and can be deleted at any
time. Each holds one `<timestamp>_<type>-<target>Hz_<backend>-<model>/` folder
per run (layout documented at the top of `runlog.py`) and its own
`index.jsonl`. `--runs-dir` or `SPICE_AGENT_RUNS_DIR` moves the root.

Tests never write there: `tests/conftest.py` redirects `SPICE_AGENT_RUNS_DIR`
to a temp directory for every test, since CLI tests drive scripted fakes that
would otherwise be filed as live runs.

## Current limits

- **AC analysis only.** `AnalysisSpec` raises on anything else, and
  `run_ngspice_ac` assumes the `vdb`/`vp` column layout. Transient support
  means a second analysis path and a second parser.
- **Op-amps only (stage 1), AC plus operating point.** No transistors and
  no transient analysis yet. The op-amp models are generic single-pole
  macromodels with no offset or noise. Op-amp circuits get no `.asc`
  schematic.
- **Phase and absolute passband level are measured but never checked.**
  Peaking above the passband is (`max_peaking_db`, default 3 dB). That limit
  exists because a live breadboard run was accepted with +12 dB of resonant
  peaking near cutoff. A notch has no width requirement.
- **Parasitic values are representative, not measured.** Inductor winding
  capacitance is the load-bearing figure and the least certain one, with Q at
  self-resonance a close second; `profile.inductor_srf_hz(L)` exists to check
  the first against a real datasheet, and `inductor_rp(L)` is directly the
  height of the resonant impedance peak.
