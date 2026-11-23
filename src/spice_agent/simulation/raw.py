"""
Reading ngspice's ASCII raw files.

The AC path reads `wrdata` columns (see ngspice.py); everything else - the
DC operating point today, transient runs later - comes as an ASCII raw file
(`set filetype=ascii` + `write <path> all`), which names every vector, so
nothing has to be matched up by column position:

    Variables:
        0   v(in)       voltage
        1   i(v1)       current
    Values:
     0  0.000e+00
        -5.000e-08
"""

from pathlib import Path


def read_ascii_raw(path) -> dict[str, list[float]]:
    """{vector name (lower case): [values, one per point]} for a real-valued
    ASCII raw file."""
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    names: list[str] = []
    i = 0
    while i < len(lines) and not lines[i].startswith("Variables:"):
        i += 1
    i += 1
    while i < len(lines) and not lines[i].startswith("Values:"):
        parts = lines[i].split()
        if len(parts) >= 2:
            names.append(parts[1].lower())
        i += 1
    if not names:
        raise ValueError(f"No Variables block in {path}")
    values: list[float] = []
    for line in lines[i + 1:]:
        parts = line.split()
        if not parts:
            continue
        values.append(float(parts[-1]))   # first line of a point carries its index too
    n = len(names)
    if len(values) % n:
        raise ValueError(f"{path}: {len(values)} values for {n} vectors")
    return {name: values[k::n] for k, name in enumerate(names)}


def read_operating_point(path) -> dict[str, float]:
    """The DC operating point: {'v(out)': 0.01, 'i(v_supply_pos)': -5e-4, ...}."""
    return {name: vals[0] for name, vals in read_ascii_raw(path).items()}


def node_voltage(op: dict[str, float], node: str) -> float:
    """Voltage of a top-level node; ground is 0 V by definition."""
    if node == "0":
        return 0.0
    return op[f"v({node.lower()})"]
