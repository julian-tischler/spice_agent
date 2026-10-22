"""
Shared helper for running the raw NETLIST_TEMPLATE_* strings from
constants.py through ngspice and extracting frequency-response metrics.

The templates use `wrdata {output_path} v(out) v(in)`, which for an AC
analysis writes COMPLEX vectors. ngspice's wrdata then emits, per vector,
a frequency column followed by real and imaginary parts, so the file has
6 columns:

    freq, re(v(out)), im(v(out)), freq, re(v(in)), im(v(in))

This differs from the 4-column vdb()/vp() layout that
spice_agent.simulate.run_ngspice_ac() parses — hence a separate parser
here rather than reusing that one.
"""

import os
import subprocess
import tempfile

import numpy as np
import pandas as pd

from spice_agent.paths import require_ngspice
# Re-exported so tests can import every helper from one place.
from spice_agent.simulation.metrics import (  # noqa: F401
    gain_at,
    lowpass_cutoff,
    highpass_cutoff,
    peak,
    notch,
    bandpass_bandwidth,
    rolloff_db_per_decade,
)


def run_template(template: str) -> pd.DataFrame:
    """
    Format the template with a temp output path, run ngspice in batch
    mode, and return a DataFrame with columns:
        freq, gain_db, phase_deg
    where gain/phase are of the transfer function v(out)/v(in).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        netlist_path = os.path.join(tmpdir, "circuit.cir")
        output_path = os.path.join(tmpdir, "output.txt")

        with open(netlist_path, "w") as f:
            f.write(template.format(output_path=output_path))

        result = subprocess.run(
            [require_ngspice(), "-b", netlist_path],
            capture_output=True,
            text=True,
        )

        if not os.path.exists(output_path):
            raise RuntimeError(
                "ngspice produced no output file.\n"
                f"---- stdout ----\n{result.stdout}\n"
                f"---- stderr ----\n{result.stderr}"
            )

        raw = pd.read_csv(output_path, sep=r"\s+", header=None)

    if raw.shape[1] != 6:
        raise RuntimeError(
            f"Expected 6 columns (freq,re,im per complex vector x2), "
            f"got {raw.shape[1]}."
        )

    freq = raw[0].to_numpy()
    v_out = raw[1].to_numpy() + 1j * raw[2].to_numpy()
    v_in = raw[4].to_numpy() + 1j * raw[5].to_numpy()
    h = v_out / v_in

    return pd.DataFrame({
        "freq": freq,
        "gain_db": 20 * np.log10(np.abs(h)),
        "phase_deg": np.degrees(np.angle(h)),
    })
