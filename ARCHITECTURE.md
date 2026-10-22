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

## The pipeline

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

## Packages

| Path | Holds |
|---|---|
| `circuit/model.py` | `Component`, `AnalysisSpec`, `Circuit`. Validation and netlist compilation. No simulator or LLM knowledge. |
| `circuit/parasitics.py` | `TechnologyProfile` and the built-in profiles. **Edit the parasitic numbers here.** |
| `circuit/expansion.py` | Ideal component → parasitic equivalent circuit. |
| `simulation/ngspice.py` | The only module that knows ngspice is a process. |
| `simulation/metrics.py` | Pure measurement of a sweep. No notion of pass or fail. |
| `simulation/spec.py` | `DesignSpec` — the requirement model. |
| `simulation/verify.py` | `check_filter_spec` — the verdict, and the feedback text. |
| `simulation/pipeline.py` | `simulate_and_verify` — the whole path in one call. |
| `agent/rate_limit.py` | Pacing and retry. Knows nothing about circuits. |
| `agent/prompts.py` | The design brief. Pure string building, so it can be asserted. |
| `agent/tool_schema.py` | The JSON schema the model fills in. |
| `agent/loop.py` | `run_agent_loop` — propose, simulate, feed back, repeat. |
| `agent/cli.py` | Argument parsing, and writing outputs when a design passes. |
| `export/netlist.py` | `.cir` text, plus the as-built (parasitic-expanded) circuit. |
| `export/schematic.py` | Symbol geometry, layout, `.asc` writer. |
| `export/ltspice_cli.py` | Driving `LTspice.exe` for cross-checks. |
| `paths.py` | Finding ngspice and LTspice. Override here or by env var. |
| `units.py` | SPICE value parsing (`"23.87m"` → `0.02387`). |
| `templates.py` | Nine hand-written reference netlists with known behaviour. |

Import from a package, not its modules — `from spice_agent.simulation import
DesignSpec` — so the internal split stays an implementation detail.

## Design decisions worth knowing

**`spec.py` and `verify.py` are deliberately adjacent.** Every field added to
`DesignSpec` needs a matching branch in `check_filter_spec`, and a field with no
check is a requirement that silently does not apply. They are separate files
only because `verify.py` would otherwise dominate the module.

**Failure messages are written to be acted on, not just read.** The same 20 dB
shortfall means different things in different places: at a stopband edge the
filter order is too low, deeper in the band something is resonating. Those need
opposite fixes, so `verify.py` distinguishes them explicitly and says so.

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

**The Gemini client is injectable.** That is what allows the entire loop to be
tested against a scripted fake with no network — see `tests/_support/fake_gemini.py`.

## Tests

`tests/` mirrors `src/spice_agent/`. Shared helpers live in `tests/_support/`,
importable from any subdirectory because `pyproject.toml` sets
`pythonpath = ["tests"]`.

Three markers divide them by what they need:

```bash
python -m pytest -m "not live"     # default: needs only ngspice (~55 s)
python -m pytest -m ltspice        # also drives LTspice.exe
python -m pytest -m live           # also calls Gemini; needs GEMINI_API_KEY
```

The LTspice and live tests skip themselves automatically when the binary or
the API key is absent.

Two conventions are worth keeping. Expected values come from circuits that were
**measured first**, with the figure recorded in a comment — guessed thresholds
caused several false failures early on. And live runs append to
`out/runs/live_runs.jsonl` with every iteration's proposal, because a live run
costs quota and produces a real design; keeping only pass/fail throws away the
most interesting output in the suite.

## Generated output

Everything under `out/` is produced by a run and is git-ignored:
`out/designs/` for `.asc` and `.cir`, `out/plots/` for Bode plots,
`out/runs/` for the live-run log.

## Current limits

- **AC analysis only.** `AnalysisSpec` raises on anything else, and
  `run_ngspice_ac` assumes the `vdb`/`vp` column layout. Transient support
  means a second analysis path and a second parser.
- **Two-terminal passives only.** No op-amps or transistors: that needs
  `.model`/`.subckt` support and relaxing `Component`'s two-node assumption.
- **Phase is simulated and plotted but never checked.** Likewise passband gain
  is measured and reported, but no spec field requires it — the prompt asks for
  unity gain and nothing enforces it.
- **Parasitic values are representative, not measured.** Inductor winding
  capacitance is the load-bearing figure and the least certain one, with Q at
  self-resonance a close second; `profile.inductor_srf_hz(L)` exists to check
  the first against a real datasheet, and `inductor_rp(L)` is directly the
  height of the resonant impedance peak.
