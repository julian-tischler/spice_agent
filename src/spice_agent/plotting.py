"""
Bode plot: magnitude (dB) and phase vs frequency, from a real ngspice
AC sweep.

What gets annotated depends on the DesignSpec, because the interesting
feature differs per filter type: the -3dB corner for a lowpass/highpass,
the peak and -3dB bandwidth for a bandpass, the null for a notch. Passing
no spec falls back to the lowpass annotation, which is what the original
version always did.
"""

import os

import matplotlib
matplotlib.use("Agg")  # no GUI backend needed, just save to file
import matplotlib.pyplot as plt

from spice_agent.simulation import metrics
from spice_agent.simulation import DesignSpec, simulate_get_full_sweep

MEASURED = "tab:red"
TARGET = "tab:green"


def _annotate(ax, df, spec: DesignSpec):
    """Draw the measured feature(s) for this filter type. Returns the
    measured frequency, or None if the feature isn't in the swept range."""
    if spec.filter_type in ("lowpass", "highpass"):
        passband_db = df["gain_db"].iloc[0 if spec.filter_type == "lowpass" else -1]
        cutoff = (metrics.lowpass_cutoff(df) if spec.filter_type == "lowpass"
                  else metrics.highpass_cutoff(df))
        ax.axhline(passband_db - 3.0, color="gray", linestyle="--", linewidth=1,
                   label=f"-3dB ({passband_db - 3:.1f} dB)")
        if cutoff is not None:
            ax.axvline(cutoff, color=MEASURED, linestyle="--", linewidth=1,
                       label=f"measured cutoff: {cutoff:,.1f} Hz")
        if spec.stopband_hz is not None:
            # Mark the requirement (where the curve must get to) and what
            # the circuit actually achieves there.
            required_db = passband_db - spec.stopband_atten_db
            achieved_db = metrics.gain_at(df, spec.stopband_hz)
            met = achieved_db <= required_db
            ax.plot([spec.stopband_hz], [required_db], marker="_", markersize=18,
                    markeredgewidth=2.5, color=TARGET,
                    label=f"stopband limit: {spec.stopband_atten_db:g} dB down "
                          f"at {spec.stopband_hz:,.0f} Hz")
            ax.plot([spec.stopband_hz], [achieved_db], marker="o", markersize=7,
                    markerfacecolor="none", markeredgewidth=2,
                    color="tab:green" if met else MEASURED,
                    label=f"achieved: {passband_db - achieved_db:.1f} dB down "
                          f"({'meets' if met else 'MISSES'} spec)")
        return cutoff

    if spec.filter_type == "bandpass":
        f_peak, g_peak = metrics.peak(df)
        bw = metrics.bandpass_bandwidth(df)
        ax.axhline(g_peak - 3.0, color="gray", linestyle="--", linewidth=1, label="-3dB")
        label = f"measured centre: {f_peak:,.1f} Hz"
        if bw is not None:
            label += f"\nbandwidth: {bw:,.1f} Hz"
        ax.axvline(f_peak, color=MEASURED, linestyle="--", linewidth=1, label=label)
        return f_peak

    f_notch, g_notch = metrics.notch(df)
    passband_db = max(df["gain_db"].iloc[0], df["gain_db"].iloc[-1])
    ax.axvline(f_notch, color=MEASURED, linestyle="--", linewidth=1,
               label=f"measured notch: {f_notch:,.1f} Hz\ndepth: {passband_db - g_notch:.1f} dB")
    return f_notch


def plot_bode(circuit_json: dict, save_path: str = "bode_plot.png",
              spec: DesignSpec | None = None, *, target_cutoff_hz: float | None = None,
              df=None, quiet: bool = False):
    """
    Simulate `circuit_json` and save a two-panel Bode plot.

    spec              annotate for this filter type and mark its target
    target_cutoff_hz  legacy shorthand for DesignSpec("lowpass", value)
    df                reuse an existing sweep instead of simulating again
    """
    if spec is None:
        spec = DesignSpec("lowpass", target_cutoff_hz) if target_cutoff_hz else DesignSpec()
        show_target = target_cutoff_hz is not None
    else:
        show_target = True

    # Simulate the circuit as built (with the spec's technology parasitics),
    # and for a non-ideal technology also the ideal one, to overlay.
    if df is None:
        df = simulate_get_full_sweep(circuit_json, technology=spec.technology,
                                    source_ohm=spec.source_ohm, load_ohm=spec.load_ohm)
    df_ideal = None
    if spec.technology != "ideal":
        df_ideal = simulate_get_full_sweep(circuit_json, technology="ideal",
                                          source_ohm=spec.source_ohm, load_ohm=spec.load_ohm)

    fig, (ax_mag, ax_phase) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)

    tech_label = f"as built ({spec.technology})" if df_ideal is not None else None
    if df_ideal is not None:
        ax_mag.semilogx(df_ideal["freq"], df_ideal["gain_db"], color="gray",
                        linestyle="-", linewidth=1.2, alpha=0.7, label="ideal components")
        ax_phase.semilogx(df_ideal["freq"], df_ideal["phase_deg"], color="gray",
                          linewidth=1.2, alpha=0.7)
    ax_mag.semilogx(df["freq"], df["gain_db"], color="tab:blue", label=tech_label)
    measured = _annotate(ax_mag, df, spec)
    if show_target:
        ax_mag.axvline(spec.target_hz, color=TARGET, linestyle=":", linewidth=1.5,
                       label=f"target: {spec.target_hz:,.1f} Hz")
    ax_mag.set_ylabel("Gain (dB)")
    ax_mag.set_title(f"{circuit_json.get('title', 'Circuit AC Response')}\n{spec.describe()}",
                     fontsize=10)
    ax_mag.grid(True, which="both", alpha=0.3)
    ax_mag.legend(fontsize=8, loc="best")

    ax_phase.semilogx(df["freq"], df["phase_deg"], color="tab:orange")
    if measured is not None:
        ax_phase.axvline(measured, color=MEASURED, linestyle="--", linewidth=1)
    if show_target:
        ax_phase.axvline(spec.target_hz, color=TARGET, linestyle=":", linewidth=1.5)
    ax_phase.set_ylim(-180, 180)
    ax_phase.set_yticks(range(-180, 181, 90))
    ax_phase.set_ylabel("Phase (deg)")
    ax_phase.set_xlabel("Frequency (Hz)")
    ax_phase.grid(True, which="both", alpha=0.3)

    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    if not quiet:
        print(f"Saved Bode plot to {save_path}")
    return df


if __name__ == "__main__":
    example = {
        "title": "RLC Band-Pass Filter",
        "components": [
            {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"},
            {"id": "L1", "type": "inductor", "nodes": ["in", "n1"], "value": "10m"},
            {"id": "C1", "type": "capacitor", "nodes": ["n1", "out"], "value": "100n"},
            {"id": "R1", "type": "resistor", "nodes": ["out", "0"], "value": "100"},
        ],
        "analysis": {
            "type": "ac", "sweep": "dec", "points": 100,
            "fstart": "1", "fstop": "1meg", "output_node": "out",
        },
    }
    plot_bode(example, save_path="bode_plot.png",
              spec=DesignSpec("bandpass", 5030, bandwidth_hz=1590))
