"""
Driving ngspice and reading what it produced.

This is the only module that knows ngspice exists as a process. It
turns a netlist string into a DataFrame of (freq, gain_db, phase_deg)
and raises SimulationError - carrying stdout/stderr - when the run
produces nothing, since that output is the most useful feedback the
agent can get about a broken circuit.
"""

import os
import subprocess

import numpy as np
import pandas as pd

from spice_agent.paths import require_ngspice


def run_ngspice_ac(netlist_str: str, netlist_path: str, output_path: str,
                   capture: dict | None = None) -> pd.DataFrame:
    """
    Write netlist to netlist_path, run ngspice in batch mode, return the
    raw AC sweep results as a DataFrame with columns:
    freq, gain_db, phase_deg

    IMPORTANT ngspice quirk: `wrdata` repeats the x-axis (frequency)
    column for EVERY vector requested, not just complex ones. So
    `wrdata path vdb(out) vp(out)` produces 4 columns on disk:
        freq, vdb(out), freq, vp(out)
    not 3. We read positionally and drop the duplicate frequency column
    rather than relying on `names=` to line up 1:1 with what's on disk -
    that mismatch previously caused columns to silently shift.
    """
    with open(netlist_path, "w") as f:
        f.write(netlist_str)

    result = subprocess.run(
        [require_ngspice(), "-b", netlist_path],
        capture_output=True,
        text=True,
    )
    if capture is not None:
        # For run logging: keep what ngspice said, success or not.
        capture.update(ngspice_stdout=result.stdout, ngspice_stderr=result.stderr,
                       ngspice_returncode=result.returncode)

    if not os.path.exists(output_path):
        # ngspice failed silently from our perspective (no output file) -
        # this is the case we most want to hand back to the LLM as
        # actionable feedback (syntax error, convergence failure, etc.)
        raise SimulationError(
            stdout=result.stdout,
            stderr=result.stderr,
            message="ngspice produced no output file - simulation failed.",
        )

    raw = pd.read_csv(output_path, sep=r"\s+", header=None)

    expected_cols = 4  # freq,vdb,freq,vp - 2 columns per requested vector
    if raw.shape[1] != expected_cols:
        raise SimulationError(
            stdout=result.stdout,
            stderr=result.stderr,
            message=(
                f"Unexpected column count in ngspice output: got "
                f"{raw.shape[1]}, expected {expected_cols}. wrdata's "
                f"column layout may differ from what this parser assumes "
                f"- inspect {output_path} manually to check."
            ),
        )

    df = pd.DataFrame({
        "freq": raw[0],
        "gain_db": raw[1],
        # ngspice's vp() is in RADIANS (unless `set units=degrees`).
        "phase_deg": np.degrees(raw[3]),
    })
    return df


class SimulationError(Exception):
    """Raised when ngspice fails to produce results. Carries stdout/stderr
    so the caller can turn it into feedback for the LLM."""
    def __init__(self, stdout: str, stderr: str, message: str):
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(message)


def compute_ac_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a linear-gain column derived from the dB gain that ngspice already
    computed for us (via vdb(node)). Assumes the AC source has unity
    magnitude (enforced by Circuit.validate()), so gain_db is directly
    the transfer function magnitude in dB - no complex division needed
    here anymore.
    """
    df = df.copy()
    df["gain_linear"] = 10 ** (df["gain_db"] / 20)
    return df
