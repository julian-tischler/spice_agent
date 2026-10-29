"""
Command line entry point.

    python -m spice_agent.agent --type bandpass --target 10000

Also holds the post-run export: whichever of the schematic, netlist and
Bode plot were asked for, written only when the design actually passed.
"""

import argparse

from spice_agent.agent.analysis import analysis_for
from spice_agent.agent.loop import (
    DEFAULT_MAX_ITERATIONS,
    MODEL_NAME,
    AgentRunResult,
    run_agent_loop,
)
from spice_agent.agent.rate_limit import RetryPolicy
from spice_agent.circuit import PROFILES
from spice_agent.simulation import DesignSpec


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
    p.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    p.add_argument("--model", default=MODEL_NAME)
    ex = p.add_argument_group("outputs (optional, written only when the spec is met)")
    ex.add_argument("--asc", metavar="PATH", default=None,
                    help="write the final design as an LTspice schematic (.asc)")
    ex.add_argument("--cir", metavar="PATH", default=None,
                    help="write the final design as an LTspice-runnable netlist (.cir)")
    ex.add_argument("--plot", metavar="PATH", default=None,
                    help="write a Bode plot of the final design (.png)")
    rl = p.add_argument_group("rate limiting")
    rl.add_argument("--min-interval", type=float, default=RetryPolicy.min_interval_s,
                    help="minimum seconds between Gemini requests")
    rl.add_argument("--max-retries", type=int, default=RetryPolicy.max_retries,
                    help="retries on 429/5xx before giving up")
    rl.add_argument("--initial-backoff", type=float, default=RetryPolicy.initial_backoff_s,
                    help="first retry delay in seconds (doubles each retry)")
    return p.parse_args(argv)


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
        technology=a.technology,
    )
    policy = RetryPolicy(
        min_interval_s=a.min_interval,
        max_retries=a.max_retries,
        initial_backoff_s=a.initial_backoff,
    )
    run = run_agent_loop(spec, model_name=a.model, max_iterations=a.max_iterations,
                         retry_policy=policy)
    if run.passed and (a.asc or a.cir or a.plot):
        export_design(run, asc_path=a.asc, cir_path=a.cir, plot_path=a.plot)
    return 0 if run.passed else 1


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
