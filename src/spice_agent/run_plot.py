"""
One picture of a whole agent run: every iteration's Bode curve overlaid,
with the component values each one used and what it measured.

    python -m spice_agent.run_plot out/runs/live/<run-folder>
    python -m spice_agent.run_plot out/runs/live/<run-folder> -o history.png

Everything comes from the run folder (see runlog.py) and nothing is
re-simulated: iter_NN/sweep.csv is the exact sweep the verdict was
judged on, circuit.json the proposal, result.json the verdict. So the
picture shows what the model was actually told, iteration by iteration.

Encoding: iterations are ordered, so they share one blue ramp from light
(first attempt) to dark (latest), the final one drawn heavier; each curve
carries its iteration number at the right edge and in the legend, so the
order never depends on telling shades apart. The requirement is drawn in
neutral grey - the target cutoff with its tolerance band, and the
stopband region the response must stay out of.
"""

import argparse
import json
import textwrap
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from spice_agent.simulation import DesignSpec, metrics

# Ordinal blue ramp, light -> dark; first step chosen to clear 2:1 on the
# light surface (dataviz reference palette, steps 250..700).
BLUE_RAMP = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
             "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
REQUIREMENT = "#898781"
NOT_JUDGED = "#e1e0d9"
GOOD = "#006300"            # success text ink, used with a "PASS" label

PREFIX = {"resistor": "R", "capacitor": "C", "inductor": "L", "opamp": "U"}


def _ramp(n: int) -> list[str]:
    """n colours spread across the ramp, always ending on the darkest."""
    if n == 1:
        return [BLUE_RAMP[-1]]
    return [BLUE_RAMP[round(i * (len(BLUE_RAMP) - 1) / (n - 1))] for i in range(n)]


def _fmt_hz(f) -> str:
    if f is None or (isinstance(f, float) and math.isnan(f)):
        return "-"
    for scale, unit in ((1e6, "MHz"), (1e3, "kHz")):
        if abs(f) >= scale:
            return f"{f / scale:.3g} {unit}"
    return f"{f:.3g} Hz"


def load_run(run_dir) -> dict:
    """Read a run folder into {spec, iterations: [{n, sweep, circuit, result}]}."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    spec = DesignSpec(**manifest["spec"])
    iterations = []
    for d in sorted(run_dir.glob("iter_*")):
        sweep, circuit, result = d / "sweep.csv", d / "circuit.json", d / "result.json"
        if not circuit.is_file():
            continue        # a reply that was not a circuit: nothing to show
        iterations.append({
            "n": int(d.name.split("_")[1]),
            # None for a resubmitted design: answered, not simulated again
            "sweep": pd.read_csv(sweep) if sweep.is_file() else None,
            "circuit": json.loads(circuit.read_text(encoding="utf-8")),
            "result": (json.loads(result.read_text(encoding="utf-8"))
                       if result.is_file() else {}),
        })
    return {"manifest": manifest, "spec": spec, "iterations": iterations}


def component_table(iterations: list[dict]) -> tuple[list[str], list[list[str]]]:
    """
    One row per component POSITION (type + nodes), one column per iteration.
    Identical parts in parallel collapse to '20x 30n'; differing ones are
    joined with '||'. Positions keep the order they first appear in.
    """
    rows: dict[str, list[dict]] = {}
    for col, it in enumerate(iterations):
        for c in it["circuit"]["components"]:
            if c["type"] not in PREFIX:
                continue
            if c.get("pins"):
                p = c["pins"]
                key = f"U  +{p.get('in+')} -{p.get('in-')} >{p.get('out')}"
                c = dict(c, value=c.get("part", "?"))
            else:
                key = f"{PREFIX[c['type']]}  {' - '.join(c['nodes'])}"
            rows.setdefault(key, [dict() for _ in iterations])
            cell = rows[key][col]
            cell[c["value"]] = cell.get(c["value"], 0) + 1
    labels = list(rows)
    cells = [[" || ".join(f"{k}x {v}" if k > 1 else v for v, k in cell.items()) or "-"
              for cell in rows[key]] for key in labels]
    return labels, cells


def metric_rows(spec: DesignSpec, iterations: list[dict]) -> tuple[list[str], list[list[str]]]:
    labels, cells = [], []

    def row(label, fn):
        labels.append(label)
        cells.append([fn(it["result"]) for it in iterations])

    if spec.filter_type in ("lowpass", "highpass"):
        row("cutoff", lambda r: _fmt_hz(r.get("cutoff_hz")))
    elif spec.filter_type == "bandpass":
        row("centre", lambda r: _fmt_hz(r.get("center_hz")))
        row("bandwidth", lambda r: _fmt_hz(r.get("bandwidth_hz")))
    else:
        row("notch", lambda r: _fmt_hz(r.get("notch_hz")))
        row("depth", lambda r: "-" if r.get("notch_depth_db") is None
            else f"{r['notch_depth_db']:.1f} dB")
    if spec.stopband_hz is not None:
        row(f"atten @ {_fmt_hz(spec.stopband_hz)}",
            lambda r: "-" if r.get("stopband_edge_atten_db") is None
            else f"{r['stopband_edge_atten_db']:.1f} dB")
        row("worst in stopband",
            lambda r: "-" if r.get("stopband_atten_db") is None
            else f"{r['stopband_atten_db']:.1f} dB @ {_fmt_hz(r.get('stopband_worst_hz'))}")
    if spec.filter_type != "bandpass":
        row("peaking", lambda r: "-" if r.get("passband_peaking_db") is None
            else f"{r['passband_peaking_db']:+.1f} dB")
    if any(it["result"].get("bias") for it in iterations):
        def dc(r):
            b = r.get("bias")
            if not b:
                return "-"
            outs = ", ".join(f"{p['id']} {p['v_out']:+.2f} V" for p in b["parts"])
            return (outs or "no op") + ("" if b["ok"] else " (BIAS)")
        row("op-amp DC out", dc)
    row("verdict", lambda r: "PASS" if r.get("passed") else
        ("sim failed" if r.get("simulation_failed") else
         f"repeat of {r['repeat_of']}" if r.get("repeated_design") else "fail"))
    return labels, cells


def _draw_requirement(ax, spec: DesignSpec, f_lo: float, f_hi: float,
                      passband_db: float, y_top: float, y_bottom: float) -> None:
    w_lo, w_hi = spec.judge_window(f_lo, f_hi)
    first = True
    for a, b in ((f_lo, w_lo), (w_hi, f_hi)):
        if b > a * (1 + 1e-9):
            ax.axvspan(a, b, color=NOT_JUDGED, alpha=0.55, linewidth=0, zorder=0,
                       label="not judged (outside the judged range)" if first else None)
            first = False
    if spec.max_peaking_db is not None and spec.filter_type != "bandpass":
        span = {"lowpass": (w_lo, spec.target_hz), "highpass": (spec.target_hz, w_hi)}.get(
            spec.filter_type, (w_lo, w_hi))
        ax.plot(list(span), [passband_db + spec.max_peaking_db] * 2, color=INK_2,
                linestyle="--", linewidth=1.0,
                label=f"peaking limit: +{spec.max_peaking_db:g} dB")
    tol = spec.target_hz * spec.tolerance_pct / 100
    ax.axvspan(spec.target_hz - tol, spec.target_hz + tol, color=REQUIREMENT,
               alpha=0.12, linewidth=0, label=f"target {_fmt_hz(spec.target_hz)} "
                                               f"+/-{spec.tolerance_pct:g}%")
    ax.axvline(spec.target_hz, color=REQUIREMENT, linestyle=":", linewidth=1.2)
    if spec.stopband_hz is None:
        return
    lo, hi = sorted((spec.stopband_hz, spec.stopband_end(f_lo, f_hi)))
    limit = passband_db - spec.stopband_atten_db
    ax.add_patch(Rectangle((lo, limit), hi - lo, y_top - limit, facecolor=REQUIREMENT,
                           alpha=0.14, hatch="//", edgecolor=REQUIREMENT, linewidth=0,
                           label=f"must stay below: {spec.stopband_atten_db:g} dB down, "
                                 f"{_fmt_hz(lo)} - {_fmt_hz(hi)}"))
    ax.plot([lo, hi], [limit, limit], color=INK_2, linewidth=1.2)


def plot_run_history(run_dir, save_path=None, *, quiet: bool = False) -> Path:
    """
    Draw the run in `run_dir` and save it (default: <run_dir>/history.png).
    Returns the path written. Raises ValueError for a run with no
    simulated iteration.
    """
    run_dir = Path(run_dir)
    run = load_run(run_dir)
    spec, iterations = run["spec"], run["iterations"]
    if not any(it["sweep"] is not None for it in iterations):
        raise ValueError(f"{run_dir} has no simulated iterations to plot")
    save_path = Path(save_path) if save_path else run_dir / "history.png"

    comp_labels, comp_cells = component_table(iterations)
    met_labels, met_cells = metric_rows(spec, iterations)
    n_rows = len(comp_labels) + len(met_labels) + 1
    colors = _ramp(len(iterations))

    plt.rcParams.update({"font.size": 9, "axes.edgecolor": AXIS,
                         "axes.labelcolor": INK_2, "xtick.color": MUTED,
                         "ytick.color": MUTED, "text.color": INK})
    fig = plt.figure(figsize=(11, 6.2 + 0.26 * n_rows), facecolor=SURFACE)
    gs = fig.add_gridspec(3, 1, height_ratios=[3.2, 1.3, 0.26 * n_rows + 0.3], hspace=0.12)
    ax_mag = fig.add_subplot(gs[0])
    ax_ph = fig.add_subplot(gs[1], sharex=ax_mag)
    ax_tab = fig.add_subplot(gs[2])
    for ax in (ax_mag, ax_ph):
        ax.set_facecolor(SURFACE)
        ax.grid(True, which="major", color=GRID, linewidth=0.6)
        ax.grid(True, which="minor", color=GRID, linewidth=0.3, alpha=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)

    drawn = [it for it in iterations if it["sweep"] is not None]
    f_lo = min(float(it["sweep"]["freq"].iloc[0]) for it in drawn)
    f_hi = max(float(it["sweep"]["freq"].iloc[-1]) for it in drawn)
    gains = pd.concat([it["sweep"]["gain_db"] for it in drawn])
    y_top = max(5.0, float(gains.max()) + 5)
    y_bottom = max(-150.0, float(gains.min()) - 5)
    w_lo, w_hi = spec.judge_window(f_lo, f_hi)
    last = drawn[-1]["sweep"]
    passband_db = metrics.gain_at(last, w_hi if spec.filter_type == "highpass" else w_lo)
    _draw_requirement(ax_mag, spec, f_lo, f_hi, passband_db, y_top, y_bottom)

    for i, (it, color) in enumerate(zip(iterations, colors)):
        if it["sweep"] is None:
            continue
        df, final = it["sweep"], i == len(iterations) - 1
        lw = 2.4 if final else 1.4
        verdict = "PASS" if it["result"].get("passed") else "fail"
        ax_mag.semilogx(df["freq"], df["gain_db"], color=color, linewidth=lw,
                        label=f"iteration {it['n']} ({verdict})", zorder=3 + i)
        ax_ph.semilogx(df["freq"], df["phase_deg"], color=color, linewidth=lw * 0.8,
                       zorder=3 + i)

    # Direct labels at the right edge; curves ending within a few dB of each
    # other share one label ("1,2") instead of printing on top of each other.
    ends = sorted(((float(it["sweep"]["gain_db"].iloc[-1]), it["n"]) for it in drawn),
                  reverse=True)
    groups: list[list] = []
    for y, n in ends:
        if groups and abs(groups[-1][0] - y) < 0.035 * (y_top - y_bottom):
            groups[-1][1].append(n)
        else:
            groups.append([y, [n]])
    last_n = iterations[-1]["n"]
    for y, ns in groups:
        ax_mag.annotate(",".join(str(n) for n in sorted(ns)), (f_hi, y), xytext=(5, 0),
                        textcoords="offset points", va="center", fontsize=8,
                        color=INK_2, fontweight="bold" if last_n in ns else "normal",
                        annotation_clip=False)

    ax_mag.set_xlim(f_lo, f_hi)
    ax_mag.set_ylim(y_bottom, y_top)
    ax_mag.set_ylabel("Gain (dB)")
    ax_mag.tick_params(labelbottom=False)
    status = run["manifest"].get("status", "?")
    title = ("\n".join(textwrap.wrap(f"{spec.describe()}  [{spec.technology}]", 130)) + "\n"
             f"{run['manifest'].get('backend', '?')}/{run['manifest'].get('model', '?')}"
             f" - {len(iterations)} iteration(s), {status}")
    ax_mag.set_title(title, fontsize=9.5, color=INK, loc="left")
    leg = ax_mag.legend(fontsize=7.5, loc="lower left", frameon=True, framealpha=0.9)
    leg.get_frame().set_edgecolor(GRID)

    ax_ph.set_ylim(-180, 180)
    ax_ph.set_yticks(range(-180, 181, 90))
    ax_ph.set_ylabel("Phase (deg)")
    ax_ph.set_xlabel("Frequency (Hz)")

    # ---- component / metric table --------------------------------------
    ax_tab.axis("off")
    header = [f"iter {it['n']}" for it in iterations]
    body = comp_cells + [[""] * len(iterations)] + met_cells
    row_labels = comp_labels + [""] + met_labels
    table = ax_tab.table(cellText=body, rowLabels=row_labels, colLabels=header,
                         loc="upper center", cellLoc="center", rowLoc="right")
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)
    table.scale(1, 1.25)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_facecolor(SURFACE)
        if r == 0:
            cell.set_text_props(color=INK_2, fontweight="bold")
            if c >= 0:
                cell.set_facecolor(colors[c])
                cell.set_text_props(color="#ffffff" if c >= len(colors) / 2 else INK,
                                    fontweight="bold")
        elif c == -1:
            cell.set_text_props(color=INK_2)
        elif row_labels[r - 1] == "verdict" and cell.get_text().get_text() == "PASS":
            cell.set_text_props(color=GOOD, fontweight="bold")

    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(save_path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    plt.rcParams.update(plt.rcParamsDefault)
    if not quiet:
        print(f"Saved run history plot to {save_path}")
    return save_path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Plot every iteration of an agent run.")
    p.add_argument("run_dir", help="a run folder, e.g. out/runs/live/<timestamp>_...")
    p.add_argument("-o", "--output", default=None,
                   help="where to write the PNG (default: <run_dir>/history.png)")
    a = p.parse_args(argv)
    plot_run_history(a.run_dir, a.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
