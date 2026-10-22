"""
Driving LTspice.exe and reading its output.

Used for cross-checks: have LTspice netlist an .asc to confirm the
schematic really describes the intended circuit, or batch-simulate it to
compare against ngspice on the same design.

Two LTspice 24 quirks are handled here: batch-running an .asc needs
`-Run -b` (plain `-b` only works on a .net/.cir), and the ASCII .raw it
writes is UTF-16.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Re-exported: callers reach for these from the export package.
from spice_agent.paths import find_ltspice, require_ltspice  # noqa: F401


def _read_text_any(path: Path) -> str:
    data = path.read_bytes()
    return data.decode("utf-16") if b"\x00" in data[:64] else data.decode("utf-8", errors="replace")


def ltspice_netlist(asc_path: str | os.PathLike, exe: str | None = None, timeout: float = 60) -> str:
    """Have LTspice netlist an .asc; returns the .net text."""
    exe = exe or require_ltspice()
    asc = Path(asc_path)
    subprocess.run([exe, "-netlist", str(asc)], check=True, timeout=timeout,
                   capture_output=True)
    net = asc.with_suffix(".net")
    if not net.exists():
        raise RuntimeError(f"LTspice produced no netlist for {asc}")
    return _read_text_any(net)


def ltspice_run(asc_path: str | os.PathLike, exe: str | None = None, timeout: float = 120) -> Path:
    """Batch-simulate an .asc with LTspice, ASCII raw output. Returns the
    .raw path."""
    exe = exe or require_ltspice()
    asc = Path(asc_path)
    # -Run is needed for LTspice 24 to batch-run an .asc (plain -b only
    # works on a .net/.cir); -ascii makes the .raw parseable text.
    subprocess.run([exe, "-Run", "-b", "-ascii", str(asc)], check=True, timeout=timeout,
                   capture_output=True)
    raw = asc.with_suffix(".raw")
    if not raw.exists():
        log = asc.with_suffix(".log")
        raise RuntimeError(
            f"LTspice produced no .raw for {asc}\n"
            + (_read_text_any(log) if log.exists() else "(no log)")
        )
    return raw


def parse_ascii_raw(raw_path: str | os.PathLike, vector: str = "V(out)"):
    """
    Minimal parser for an LTspice ASCII .raw from an AC analysis.
    Returns a DataFrame with freq, gain_db, phase_deg for `vector`.
    """
    import math
    import pandas as pd

    data = Path(raw_path).read_bytes()
    # LTspice writes the ASCII raw as UTF-16 on Windows; older builds used UTF-8
    text = data.decode("utf-16") if b"\x00" in data[:64] else data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    names: list[str] = []
    i = 0
    while i < len(lines) and not lines[i].startswith("Values:"):
        if lines[i].startswith("Variables:"):
            i += 1
            while i < len(lines) and not lines[i].startswith("Values:"):
                parts = lines[i].split()
                names.append(parts[1])
                i += 1
            break
        i += 1
    if not names:
        raise ValueError("No Variables block in raw file")
    try:
        col = [n.lower() for n in names].index(vector.lower())
    except ValueError:
        raise ValueError(f"{vector} not in raw file; have {names}")

    freqs, vals = [], []
    i += 1  # skip "Values:"
    row = []
    for line in lines[i:]:
        parts = line.split()
        if not parts:
            continue
        if len(parts) == 2 and row == []:
            # "<index> <value>" starts a point
            row = [parts[1]]
        else:
            row.append(parts[-1])
        if len(row) == len(names):
            def cplx(s):
                re_, im_ = s.split(",")
                return complex(float(re_), float(im_))
            freqs.append(cplx(row[0]).real)
            vals.append(cplx(row[col]))
            row = []

    gain_db = [20 * math.log10(abs(v)) if abs(v) > 0 else float("-inf") for v in vals]
    phase = [math.degrees(math.atan2(v.imag, v.real)) for v in vals]
    return pd.DataFrame({"freq": freqs, "gain_db": gain_db, "phase_deg": phase})
