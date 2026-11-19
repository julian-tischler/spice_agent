"""
Command line entry point.

    python -m spice_agent.agent --type bandpass --target 10000

Also holds the post-run export: whichever of the schematic, netlist and
Bode plot were asked for, written only when the design actually passed.
"""

import argparse
import json

from spice_agent.agent.analysis import analysis_for
from spice_agent.agent.claude_loop import (
    CLAUDE_EFFORT,
    CLAUDE_MODEL,
    DEFAULT_CLAUDE_ITERATIONS,
    run_claude_loop,
)
from spice_agent.agent.loop import (
    DEFAULT_MAX_ITERATIONS,
    MODEL_NAME,
    AgentRunResult,
    run_agent_loop,
)
from spice_agent.agent.rate_limit import RetryPolicy
from spice_agent.circuit import PROFILES
from spice_agent.runlog import RunRecorder
from spice_agent.simulation import DesignSpec
from spice_agent.simulation.spec import DEFAULT_MAX_PEAKING_DB


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the SPICE design agent for one filter spec.")
    p.add_argument("--type", dest="filter_type", default="lowpass",
                   choices=["lowpass", "highpass", "bandpass", "notch"])
    p.add_argument("--target", type=float, default=1590.0,
                   help="cutoff (lowpass/highpass) or centre frequency (bandpass/notch), Hz")
    p.add_argument("--tolerance", type=float, default=10.0, help="percent")
    p.add_argument("--bandwidth", type=float, default=None,
                   help="bandpass only: required -3dB bandwidth in Hz")
    p.add_argument("--notch-depth", type=float, default=20.0,
                   help="notch only: minimum depth below passband, dB")
    p.add_argument("--technology", default="ideal",
                   choices=sorted(PROFILES),
                   help="how the circuit is assumed to be built; non-ideal "
                        "profiles include that technology's parasitics")
    p.add_argument("--stopband-hz", type=float, default=None,
                   help="lowpass/highpass: frequency at which to require attenuation")
    p.add_argument("--stopband-atten", type=float, default=None,
                   help="lowpass/highpass: dB of attenuation below passband required "
                        "at --stopband-hz (forces a higher-order topology)")
    p.add_argument("--stopband-end", type=float, default=None, metavar="HZ",
                   help="lowpass/highpass: far edge of the stopband. Default: the edge "
                        "of the judged range (see --judge-lo/--judge-hi)")
    jd = p.add_argument_group(
        "what is judged (defaults: 2 decades either side of --target, 3 dB peaking, "
        "ideal source, no load)")
    jd.add_argument("--judge-lo", type=float, default=None, metavar="HZ",
                    help="lowest frequency that is judged; below it the response is "
                         "reported but cannot fail the design (default target/100)")
    jd.add_argument("--judge-hi", type=float, default=None, metavar="HZ",
                    help="highest frequency that is judged (default target*100); the "
                         "sweep is extended to cover it")
    jd.add_argument("--max-peaking", default=str(DEFAULT_MAX_PEAKING_DB), metavar="DB",
                    help="max passband gain above its level, dB, or 'off' "
                         f"(default {DEFAULT_MAX_PEAKING_DB:g})")
    jd.add_argument("--source-ohm", type=float, default=0.0, metavar="OHM",
                    help="source resistance the harness puts between V1 and 'in' "
                         "(default 0 = ideal source); the model cannot change it")
    jd.add_argument("--load-ohm", type=float, default=None, metavar="OHM",
                    help="load resistance the harness puts from 'out' to ground "
                         "(default: none, 'out' unloaded); the model cannot change it")
    p.add_argument("--diagnosis", choices=["full", "basic", "off"], default="full",
                   help="parasitics diagnosis of failed non-ideal designs: full (default; "
                        "includes the per-component check, a few seconds per iteration), "
                        "basic (quicker, no per-component check) or off")
    p.add_argument("--backend", choices=["gemini", "claude"], default="gemini",
                   help="gemini: Gemini API (needs GEMINI_API_KEY). claude: Claude Code "
                        "headless mode (`claude -p`) on your Claude subscription")
    p.add_argument("--max-iterations", type=int, default=None,
                   help=f"default {DEFAULT_MAX_ITERATIONS} (gemini) / "
                        f"{DEFAULT_CLAUDE_ITERATIONS} (claude)")
    p.add_argument("--model", default=None,
                   help=f"default {MODEL_NAME} (gemini) / {CLAUDE_MODEL} (claude)")
    p.add_argument("--effort", default=CLAUDE_EFFORT,
                   choices=["low", "medium", "high", "xhigh", "max"],
                   help="claude backend only: reasoning effort (higher uses more quota)")
    ex = p.add_argument_group("outputs (optional, written only when the spec is met)")
    ex.add_argument("--asc", metavar="PATH", default=None,
                    help="write the final design as an LTspice schematic (.asc)")
    ex.add_argument("--cir", metavar="PATH", default=None,
                    help="write the final design as an LTspice-runnable netlist (.cir)")
    ex.add_argument("--plot", metavar="PATH", default=None,
                    help="write a Bode plot of the final design (.png)")
    lg = p.add_argument_group("run logging")
    lg.add_argument("--no-log", action="store_true",
                    help="do not write a run folder (default: every run is logged "
                         "to out/runs/live/<timestamp>_.../)")
    lg.add_argument("--runs-dir", metavar="DIR", default=None,
                    help="root for run folders, which go in its live/ or dry/ "
                         "subfolder (default: ./out/runs, or $SPICE_AGENT_RUNS_DIR)")
    rl = p.add_argument_group("rate limiting (gemini backend only)")
    rl.add_argument("--min-interval", type=float, default=RetryPolicy.min_interval_s,
                    help="minimum seconds between Gemini requests")
    rl.add_argument("--max-retries", type=int, default=RetryPolicy.max_retries,
                    help="retries on 429/5xx before giving up")
    rl.add_argument("--initial-backoff", type=float, default=RetryPolicy.initial_backoff_s,
                    help="first retry delay in seconds (doubles each retry)")
    return p.parse_args(argv)


def _peaking_arg(text: str) -> float | None:
    if str(text).strip().lower() in ("off", "none"):
        return None
    try:
        return float(text)
    except ValueError:
        raise SystemExit(f"--max-peaking must be a number of dB or 'off', not {text!r}")


def main(argv=None) -> int:
    a = parse_args(argv)
    spec = DesignSpec(
        filter_type=a.filter_type,
        target_hz=a.target,
        tolerance_pct=a.tolerance,
        bandwidth_hz=a.bandwidth,
        min_notch_depth_db=a.notch_depth,
        stopband_hz=a.stopband_hz,
        stopband_atten_db=a.stopband_atten,
        stopband_end_hz=a.stopband_end,
        technology=a.technology,
        judge_lo_hz=a.judge_lo,
        judge_hi_hz=a.judge_hi,
        max_peaking_db=_peaking_arg(a.max_peaking),
        source_ohm=a.source_ohm,
        load_ohm=a.load_ohm,
    )
    argv_used = list(argv) if argv is not None else None
    if a.backend == "claude":
        model = a.model or CLAUDE_MODEL
        iterations = a.max_iterations or DEFAULT_CLAUDE_ITERATIONS
        settings = {"effort": a.effort, "max_iterations": iterations,
                    "diagnosis": a.diagnosis}
    else:
        model = a.model or MODEL_NAME
        iterations = a.max_iterations or DEFAULT_MAX_ITERATIONS
        policy = RetryPolicy(
            min_interval_s=a.min_interval,
            max_retries=a.max_retries,
            initial_backoff_s=a.initial_backoff,
        )
        settings = {"max_iterations": iterations, "retry_policy": policy,
                    "diagnosis": a.diagnosis}

    recorder = (RunRecorder.disabled() if a.no_log else
                RunRecorder.start(spec=spec, backend=a.backend, model=model,
                                  argv=argv_used, settings=settings, runs_dir=a.runs_dir))
    if recorder.enabled:
        print(f"Logging run to {recorder.root}")

    with recorder:
        if a.backend == "claude":
            run = run_claude_loop(spec, model_name=model, effort=a.effort,
                                  max_iterations=iterations, recorder=recorder,
                                  diagnose=a.diagnosis)
        else:
            run = run_agent_loop(spec, model_name=model, max_iterations=iterations,
                                 retry_policy=policy, recorder=recorder,
                                 diagnose=a.diagnosis)
        recorder.finish(run)
        if recorder.enabled:
            _plot_history(recorder)

        if run.passed:
            if recorder.enabled:
                _export_final(run, recorder)
            if a.asc or a.cir or a.plot:
                export_design(run, asc_path=a.asc, cir_path=a.cir, plot_path=a.plot)
                for path in (a.asc, a.cir, a.plot):
                    if path:
                        recorder.note_external_output(path)
    return 0 if run.passed else 1


def _plot_history(recorder: RunRecorder) -> None:
    """history.png in the run folder: every iteration's Bode curve and the
    component values it used. Pass or fail - the failures are the
    interesting part. Never fatal; matplotlib is imported lazily."""
    try:
        from spice_agent.run_plot import plot_run_history

        path = plot_run_history(recorder.root, quiet=True)
        print(f"Wrote run history plot: {path}")
    except ValueError:
        pass                    # no simulated iteration: nothing to draw
    except Exception as e:  # noqa: BLE001
        recorder.note("history_plot_error", f"{type(e).__name__}: {e}")
        print(f"Warning: could not plot the run history: {e}")


def _export_final(run: AgentRunResult, recorder: RunRecorder) -> None:
    """Write the passing design (JSON, netlist, schematic, Bode plot) into
    the run folder's final/. A failing export is noted, never fatal: the
    run itself already succeeded."""
    final = recorder.final_dir()
    (final / "circuit.json").write_text(
        json.dumps(run_circuit_json(run), indent=2), encoding="utf-8")
    try:
        export_design(run, asc_path=final / "circuit.asc", cir_path=final / "circuit.cir",
                      plot_path=final / "bode.png")
    except Exception as e:  # noqa: BLE001
        recorder.note("final_export_error", f"{type(e).__name__}: {e}")
        print(f"Warning: could not write all final outputs: {e}")


def run_circuit_json(run: AgentRunResult) -> dict:
    """The run's final circuit as a complete circuit JSON dict."""
    if run.final_circuit is None:
        raise ValueError("Run has no circuit.")
    return {
        "title": run.final_circuit.get("title", "LLM-generated circuit"),
        "components": run.final_circuit["components"],
        "analysis": analysis_for(run.spec),
    }


def export_design(run: AgentRunResult, asc_path=None, cir_path=None, plot_path=None) -> dict:
    """
    Write the run's final circuit as LTspice files and/or a Bode plot.
    Both modules are imported lazily so the core agent depends on neither
    the optional export package nor matplotlib.
    """
    circuit_json = run_circuit_json(run)
    info: dict = {}

    if asc_path or cir_path:
        from spice_agent.export import export

        # Non-ideal technology exports the as-built circuit, so the
        # files describe what was simulated, not the ideal design.
        info.update(export(circuit_json, asc_path=asc_path, cir_path=cir_path,
                           source_ohm=run.spec.source_ohm, load_ohm=run.spec.load_ohm,
                           technology=run.spec.technology))
        for key in ("asc", "cir"):
            if key in info:
                print(f"Wrote LTspice {key}: {info[key]}"
                      + (f"  (layout: {info['layout']})" if key == "asc" else ""))

    if plot_path:
        from spice_agent.plotting import plot_bode

        plot_bode(circuit_json, save_path=plot_path, spec=run.spec, quiet=True)
        info["plot"] = plot_path
        print(f"Wrote Bode plot: {plot_path}")

    return info
