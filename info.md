# SPICE Agent — Project Status

**Project:** LLM-driven agent that autonomously designs electronic circuits and verifies them via SPICE simulation.
**Location:** `c:\Work\CDLab\spice_agent\`
**Status as of this report:** Core pipeline complete and verified end-to-end on real hardware (LLM → circuit design → real ngspice simulation → pass/fail → visualization).

---

## 1. Goal

Build a fully closed-loop system where an LLM:
1. Proposes a circuit design (topology + component values)
2. Gets that design simulated by a real SPICE engine (ngspice) — not just LLM-estimated behavior
3. Receives actual simulated results as structured feedback
4. Revises the design if it doesn't meet spec, and repeats until it does (or an iteration budget runs out)

Scope for this phase: passive RC/RLC low-pass filters. Narrow on purpose — the goal was to validate the *pipeline*, not circuit-design sophistication, before expanding to more complex topologies (op-amp circuits, transistor-level designs).

---

## 2. Architecture

```
LLM (Gemini)
   │  proposes circuit as structured JSON
   ▼
netlist_generator.py   (JSON → SPICE netlist, with validation)
   ▼
simulate_and_verify.py (runs ngspice, parses output, checks spec)
   ▼
result dict (passed/fail + real simulated metrics)
   │
   └──► fed back to LLM for next iteration (gemini_agent_loop.py)
   └──► or visualized as a Bode plot (plot_bode.py)
```

**Design principle followed throughout:** the LLM never writes raw SPICE syntax. It only produces a structured JSON representation; a deterministic, independently-testable Python layer compiles that into valid SPICE. This was an intentional early decision to avoid burning simulation/iteration budget on syntax errors, and it held up well in practice.

---

## 3. Components built

### `netlist_generator.py`
Deterministic compiler: JSON circuit description → valid ngspice netlist text.

- `Component` — validates that component IDs match their SPICE type prefix (e.g. an id starting with `R` must be type `resistor`), and that each component has exactly 2 nodes.
- `AnalysisSpec` — currently supports AC sweep analysis only (`dec`/`oct`/`lin` sweep types). Probes the output node using ngspice's `vdb()`/`vp()` pseudo-vectors (see §5 for why).
- `Circuit` — top-level container. `validate()` checks for: missing ground node, duplicate component IDs, no independent source, and (for AC analysis) that the source has unity AC magnitude (`AC 1`) — required for the dB readout to be directly usable as gain.
- Pure function of its input — no ngspice or LLM dependency, fully unit-testable in isolation.

### `simulate_and_verify.py`
The actual verification engine — the "tool" the LLM agent calls.

- `run_ngspice_ac()` — writes the netlist, invokes ngspice in batch mode via `subprocess`, parses the output file.
- `compute_ac_metrics()` — derives linear gain from the dB values ngspice already computed.
- `find_cutoff_frequency()` — locates the -3dB point via linear interpolation between the two bracketing swept frequency points (more accurate than snapping to the nearest sample).
- `check_lowpass_spec()` — compares simulated cutoff against a target frequency ± tolerance, returns a structured, JSON-serializable result (plain Python types, not numpy scalars — important since this feeds an LLM prompt later).
- `simulate_and_verify()` — the single entry point tying it all together: JSON circuit + target spec → pass/fail result.
- `simulate_get_full_sweep()` — returns the complete AC sweep (not just the summary), used for plotting.

### `gemini_agent_loop.py`
The actual LLM agent loop, using Google's Gemini API (`google-genai` SDK).

- Uses **manual** function calling (automatic function calling explicitly disabled) so the loop, iteration cap, and logging stay under explicit control rather than the SDK's black-box auto-loop.
- `simulate_circuit` tool exposes the circuit JSON schema to the model; the design target (cutoff frequency, tolerance) is fixed by the harness and not exposed as a model-controlled parameter.
- On each iteration: model proposes a circuit → real ngspice simulation runs → result (including the actual numeric cutoff and error %) is fed back as the tool response → model revises if needed.
- Iteration budget capped (`MAX_ITERATIONS = 6`) with a clear "budget exhausted" exit path.
- Malformed circuit JSON or validation errors are caught and turned into feedback text rather than crashing the loop.
- Currently configured for `gemini-2.5-flash` as a first, inexpensive model to validate the loop mechanics before trying a stronger model.

### `plot_bode.py`
Visualization: runs the full AC sweep for a given circuit and produces a two-panel Bode plot (magnitude in dB, phase in degrees, both vs. log-scale frequency), with the simulated and target cutoff frequencies marked as vertical reference lines.

---

## 4. Verified results

Running the full loop with Gemini (`gemini-2.5-flash`) on the target spec **"RC low-pass filter, cutoff 1590 Hz ± 10%"**:

- The model proposed a standard RC filter (R1 = 1kΩ, C1 = 100nF) on the **first iteration**.
- Real ngspice simulation returned a cutoff of **1587.8 Hz** (theoretical value for this R/C combo: ≈1591.5 Hz) — **0.14% error**.
- Spec check: **PASSED**, no revision needed.
- Result visualized via `plot_bode.py`: correct flat passband, -20dB/decade rolloff past cutoff, and -45° phase point landing exactly at the cutoff frequency — matches textbook RC filter behavior.

This confirms the full loop — LLM reasoning, structured circuit representation, real SPICE execution, and spec verification — is working correctly together, not just in isolated pieces.

---

## 5. Key technical issues found and fixed

These are worth recording since they weren't obvious upfront and cost real debugging time:

1. **Windows has no plain `ngspice` binary.** The console binary is `ngspice_con.exe`, and PATH-based lookup was unreliable across machines (especially one without full admin rights). **Fix:** hardcoded static path to the binary (`NGSPICE_PATH` constant), bypassing PATH entirely.

2. **`wrdata` duplicates the frequency (x-axis) column for every requested vector — including real-valued ones.** Initial assumption was that only complex vectors (`v(node)`) triggered this; testing showed it also happens for real-valued pseudo-vectors (`vdb()`, `vp()`). Requesting `wrdata output.txt vdb(out) vp(out)` produces **4 columns** (`freq, vdb, freq, vp`), not 3. Silent column misalignment from this bug caused an early result to look like a simulation failure even though ngspice had actually run correctly (visible in retrospect via a suspicious `passband_gain_db == 1.0`, which was actually the `fstart` value leaking into the wrong column). **Fix:** parse ngspice output positionally (by column index) rather than assuming a name-to-column mapping, and explicitly validate the column count with a clear error if it doesn't match expectations.

3. **AC gain readout simplification.** Switched from manually computing `V(out)/V(in)` via complex division to just reading ngspice's `vdb()`/`vp()` pseudo-vectors directly. This only works correctly if the AC source has unity magnitude, so `Circuit.validate()` now enforces `AC 1` on the source — a constraint worth keeping in mind if the circuit domain expands to multi-source circuits later.

4. **LTspice does not auto-generate schematics from arbitrary hand-written netlists.** Initial assumption was incorrect — LTspice opens `.cir` files as a text/netlist editor, not as a schematic view, unless the netlist originated from LTspice's own schematic export. Dropped as an approach.

5. **Schematic visualization pivoted to `schemdraw`** (Python) as a side exploration — maps the same JSON circuit representation directly to a rendered schematic image, avoiding netlist-import fragility entirely. Confirmed working for simple series-ladder topologies (which covers RC/RLC filters). Explicitly does **not** handle bridge topologies, parallel branches, multiple sources, or feedback loops — noted as a scope limit in the code, not a silent gap.

---

## 6. Current limitations / explicitly out of scope (for now)

- **Topology scope:** series-ladder RC/RLC only. No bridges, no parallel branches between the same two nodes, no op-amps/transistors, no feedback.
- **Analysis type:** AC sweep only. `AnalysisSpec` has stubs for `dc`/`tran` but they raise `NotImplementedError`.
- **Filter type:** only low-pass spec-checking (`check_lowpass_spec`) exists. High-pass/band-pass checking not yet implemented.
- **Source constraint:** AC source magnitude must be exactly 1 (unity) for the current gain-readout method to be valid.
- **Component realism:** no snap-to-E12/E24 standard value step yet — the LLM could in principle propose a component value like 3.784kΩ that isn't a real, purchasable part.
- **Convergence handling:** `SimulationError` surfaces ngspice failures as feedback, but there's no retry-with-alternate-`.options` logic yet for genuine convergence failures (as opposed to syntax/definition errors).
- **Single LLM provider tested so far:** Gemini (`gemini-2.5-flash`) only. Not yet tried against a stronger model.

---

## 7. Suggested next steps

Roughly in order of natural progression:

1. Extend spec-checking to high-pass and band-pass filters (mirrors of the existing low-pass logic).
2. Add component-value realism (snap to E12/E24 series) as a post-processing step.
3. Add convergence-failure retry logic (`.options gmin` stepping, etc.) before treating a failed simulation as a design flaw.
4. Try the loop against a stronger model and compare iteration counts / success rates against Gemini Flash.
5. Expand topology support beyond series ladders once the above is solid — likely the point where the JSON schema and netlist generator need their first real extension (parallel branches, multiple sources).
6. Consider logging/persisting each agent run (circuit proposed, result, iteration count) for later comparison across models/specs.

---

## 8. Files in this project

| File | Purpose |
|---|---|
| `netlist_generator.py` | JSON circuit description → SPICE netlist (deterministic, no LLM/ngspice dependency) |
| `simulate_and_verify.py` | Runs ngspice, parses results, checks against spec — the core verification tool |
| `gemini_agent_loop.py` | LLM agent loop using Gemini API with manual function calling |
| `plot_bode.py` | Generates magnitude/phase Bode plots from real simulation data |
| `schematic_generator.py` | (side exploration) JSON circuit → visual schematic via schemdraw |