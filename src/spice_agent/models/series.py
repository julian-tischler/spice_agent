"""
The standard preferred-value series for passive parts (IEC 60063).

Resistors, capacitors and inductors are sold in E-series values: E12 means
twelve values per decade (1.0, 1.2, 1.5, ... 8.2, then 10, 12, ...). A spec
can require the model to use them (DesignSpec.value_series); this module
answers the two questions that check needs:

    is_member(value, "E24")     is 1.6k an E24 value?          -> True
    neighbours(value, "E24")    the E24 values either side      -> (1.5k, 1.6k)

The tables are the published ones, NOT the formula round(10 ** (i / n)).
E6 - E24 predate the formula and differ from it in eight places (2.7, 3.0,
3.3, 3.6, 3.9, 4.3, 4.7, 8.2, where the formula gives 2.6, 2.9, 3.2, ...);
a computed table would reject the most common parts there are. E48 and E96
are the formula's (to two decimals) and are written out anyway, so the
tables can be checked against a datasheet by eye.

Matching is on the parsed value, so "1.6k", "1600" and "1.60k" all match,
within REL_TOL. Adjacent E96 values are ~2.4 % apart, so 0.1 % cannot
confuse two of them, while it absorbs a model writing 4.7n as 4.70e-9 or
float noise in the decade arithmetic.

Nothing here chooses a value: neighbours() is a table lookup, so feedback
can state the nearest standard values without the harness ever searching
for a design (see ARCHITECTURE.md, "The one invariant").
"""

import math

E6 = (1.0, 1.5, 2.2, 3.3, 4.7, 6.8)

E12 = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)

E24 = (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
       3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1)

E48 = (1.00, 1.05, 1.10, 1.15, 1.21, 1.27, 1.33, 1.40, 1.47, 1.54, 1.62, 1.69,
       1.78, 1.87, 1.96, 2.05, 2.15, 2.26, 2.37, 2.49, 2.61, 2.74, 2.87, 3.01,
       3.16, 3.32, 3.48, 3.65, 3.83, 4.02, 4.22, 4.42, 4.64, 4.87, 5.11, 5.36,
       5.62, 5.90, 6.19, 6.49, 6.81, 7.15, 7.50, 7.87, 8.25, 8.66, 9.09, 9.53)

E96 = (1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30,
       1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69, 1.74,
       1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32,
       2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09,
       3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
       4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
       5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32,
       7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76)

SERIES: dict[str, tuple[float, ...]] = {
    "E6": E6, "E12": E12, "E24": E24, "E48": E48, "E96": E96,
}

# The tolerance each series was designed for: adjacent values are about
# 2 x tolerance apart, so every value is covered. Informational - a part's
# tolerance is not simulated.
TOLERANCE_PCT: dict[str, float] = {"E6": 20.0, "E12": 10.0, "E24": 5.0,
                                    "E48": 2.0, "E96": 1.0}

REL_TOL = 1e-3


def get_series(name: str) -> tuple[float, ...]:
    """The mantissas of a series by name ('E24', case-insensitive)."""
    key = str(name).strip().upper()
    if key not in SERIES:
        raise ValueError(f"Unknown value series {name!r}; known: {', '.join(SERIES)}")
    return SERIES[key]


def _candidates(value: float, table: tuple[float, ...]) -> list[float]:
    """The series values in the decade of `value` and the two around it."""
    if not (value > 0 and math.isfinite(value)):
        raise ValueError(f"A series value must be positive and finite, not {value!r}")
    decade = math.floor(math.log10(value))
    return [m * 10.0 ** e for e in (decade - 1, decade, decade + 1) for m in table]


def neighbours(value: float, series: str) -> tuple[float, float]:
    """
    (below, above): the nearest series values with below <= value <= above.
    A value that is in the series (within REL_TOL) returns itself twice -
    as the table's exact value, so 4.70e-9 comes back as 4.7e-9.
    """
    table = get_series(series)
    cands = _candidates(value, table)
    for c in cands:
        if abs(value - c) <= REL_TOL * c:
            return c, c
    below = max(c for c in cands if c < value)
    above = min(c for c in cands if c > value)
    return below, above


def is_member(value: float, series: str) -> bool:
    below, above = neighbours(value, series)
    return below == above


def deviation_pct(value: float, standard: float) -> float:
    """How far `standard` is from `value`, in percent of `value` (signed:
    negative when the standard value is smaller)."""
    return (standard - value) / value * 100.0
