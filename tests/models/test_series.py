"""
The E-series tables and lookups (spice_agent.models.series).

The tables are checked against their own structure (each coarser series is
every other value of the next finer one) and against the IEC 60063 values
the formula gets wrong, so a typo in a table cannot pass unnoticed.
"""

import pytest

from spice_agent.models.series import (
    E6,
    E12,
    E24,
    E48,
    E96,
    SERIES,
    TOLERANCE_PCT,
    deviation_pct,
    get_series,
    is_member,
    neighbours,
)
from spice_agent.units import parse_spice_value

# ---------------------------------------------------------------------
# The tables
# ---------------------------------------------------------------------


@pytest.mark.parametrize("name, n", [("E6", 6), ("E12", 12), ("E24", 24), ("E48", 48), ("E96", 96)])
def test_each_series_has_n_increasing_values_in_one_decade(name, n):
    table = SERIES[name]
    assert len(table) == n and len(set(table)) == n
    assert table[0] == 1.0 and table[-1] < 10.0
    assert list(table) == sorted(table)


@pytest.mark.parametrize("coarse, fine", [(E6, E12), (E12, E24), (E48, E96)])
def test_coarser_series_is_every_other_value_of_the_finer(coarse, fine):
    assert coarse == fine[::2]


def test_e24_keeps_the_values_the_formula_gets_wrong():
    """IEC 60063 predates round(10 ** (i / 24)); the formula gives 2.6, 2.9,
    3.2, 3.5, 3.8, 4.2, 4.6 and 8.3 instead - not values anyone sells."""
    formula = [round(10 ** (i / 24), 1) for i in range(24)]
    differ = {t for t, f in zip(E24, formula) if t != f}
    assert differ == {2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 8.2}


def test_e96_is_the_formula_to_two_decimals():
    assert E96 == tuple(round(10 ** (i / 96), 2) for i in range(96))


def test_every_series_has_a_tolerance():
    assert set(TOLERANCE_PCT) == set(SERIES)


def test_series_names_are_case_insensitive_and_checked():
    assert get_series("e24") is E24 and get_series(" E12 ") is E12
    with pytest.raises(ValueError, match="Unknown value series 'E13'"):
        get_series("E13")


# ---------------------------------------------------------------------
# Membership: on the parsed value, in every decade
# ---------------------------------------------------------------------


@pytest.mark.parametrize("text", ["1.6k", "1600", "1.60k", "1.6e3", "16", "160meg", "1.6"])
def test_spelling_and_decade_do_not_matter(text):
    assert is_member(parse_spice_value(text), "E24")


@pytest.mark.parametrize("text, series, expected", [
    ("4.7n", "E6", True),
    ("4.7n", "E96", False),         # E96 has 4.64 and 4.75, not 4.7
    ("2.2u", "E12", True),
    ("2.25n", "E24", False),
    ("10k", "E6", True),            # 1.0 of the next decade
    ("100", "E96", True),
    ("1.58k", "E96", True),
    ("1.58k", "E24", False),
    ("3.3p", "E12", True),
    ("5.1", "E12", False),          # E24 only
])
def test_membership(text, series, expected):
    assert is_member(parse_spice_value(text), series) is expected


def test_float_noise_and_trailing_zeros_still_match():
    assert is_member(4.70e-9, "E12") and is_member(4.7e-9 * (1 + 2e-4), "E12")
    assert is_member(9.99999999e3, "E6")            # lands on 10k, next decade
    assert not is_member(4.7e-9 * 1.01, "E12")      # 1 % off is not 4.7n


# ---------------------------------------------------------------------
# Neighbours: the values either side, for feedback
# ---------------------------------------------------------------------


def test_neighbours_bracket_the_value():
    below, above = neighbours(2.25e-9, "E24")
    assert below == pytest.approx(2.2e-9) and above == pytest.approx(2.4e-9)


def test_a_member_is_its_own_neighbour_as_the_table_value():
    below, above = neighbours(4.70001e-9, "E12")
    assert below == above == pytest.approx(4.7e-9, rel=1e-12)


@pytest.mark.parametrize("value, series, below, above", [
    (9.5e3, "E24", 9.1e3, 10e3),       # wraps up into the next decade
    (1.05, "E24", 1.0, 1.1),
    (0.99, "E12", 0.82, 1.0),          # just below a decade boundary
    (1.59e3, "E24", 1.5e3, 1.6e3),
    (9.8e-6, "E96", 9.76e-6, 10e-6),
])
def test_neighbours_across_decade_boundaries(value, series, below, above):
    b, a = neighbours(value, series)
    assert b == pytest.approx(below) and a == pytest.approx(above)
    assert b < value < a


@pytest.mark.parametrize("bad", [0.0, -1e3, float("inf"), float("nan")])
def test_non_positive_values_are_rejected(bad):
    with pytest.raises(ValueError, match="positive and finite"):
        neighbours(bad, "E12")


def test_deviation_is_signed_percent_of_the_value():
    assert deviation_pct(1.59e3, 1.5e3) == pytest.approx(-5.66, abs=0.01)
    assert deviation_pct(1.59e3, 1.6e3) == pytest.approx(+0.63, abs=0.01)
