"""
The passive catalogue in the spec: which standard value series the parts
must come from (DesignSpec.value_series, --series).

The series tables themselves are tested in tests/models/test_series.py;
the technology's value ranges in test_constraints.py.
"""

import json

import pytest

from spice_agent.agent.analysis import analysis_for
from spice_agent.simulation import DesignSpec, simulate_and_verify
from spice_agent.simulation.spec import parse_value_series

# ---------------------------------------------------------------------
# The spec field
# ---------------------------------------------------------------------


def test_default_restricts_nothing():
    s = DesignSpec("lowpass", 1e4)
    assert s.value_series is None
    assert s.series_for("resistor") is None
    assert "standard values" not in s.describe()


def test_names_are_normalised():
    s = DesignSpec("lowpass", 1e4, value_series={"R": "e96", "capacitors": " E12 "})
    assert s.value_series == {"resistor": "E96", "capacitor": "E12"}
    assert s.series_for("capacitor") == "E12" and s.series_for("inductor") is None


def test_an_empty_mapping_is_no_restriction():
    assert DesignSpec("lowpass", 1e4, value_series={}).value_series is None


@pytest.mark.parametrize("bad, match", [
    ({"resistor": "E13"}, "Unknown value series 'E13'"),
    ({"diode": "E12"}, "Unknown part type 'diode'"),
])
def test_bad_series_are_rejected(bad, match):
    with pytest.raises(ValueError, match=match):
        DesignSpec("lowpass", 1e4, value_series=bad)


def test_describe_states_the_series_in_type_order():
    s = DesignSpec("lowpass", 1e4, value_series={"L": "E6", "R": "E96"})
    assert s.describe().endswith("standard values only: resistors E96, inductors E6")


def test_the_series_survives_the_run_log_round_trip():
    """spec.json is written with to_dict() and read back by run_plot."""
    s = DesignSpec("lowpass", 1e4, value_series={"R": "E24"})
    assert DesignSpec(**json.loads(json.dumps(s.to_dict()))) == s


# ---------------------------------------------------------------------
# --series
# ---------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("E24", {"resistor": "E24", "capacitor": "E24", "inductor": "E24"}),
    ("R=E96,C=E12", {"resistor": "E96", "capacitor": "E12"}),
    ("E12,R=E96", {"resistor": "E96", "capacitor": "E12", "inductor": "E12"}),
    ("R=E96, E12", {"resistor": "E96", "capacitor": "E12", "inductor": "E12"}),
])
def test_parse_value_series(text, expected):
    assert parse_value_series(text) == expected


def test_parse_rejects_an_empty_argument():
    with pytest.raises(ValueError, match="No value series"):
        parse_value_series(" , ")


def test_cli_series_reaches_the_spec(monkeypatch):
    import spice_agent.agent.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "run_agent_loop", lambda spec, **kw: seen.setdefault("s", spec)
                        and cli.AgentRunResult(spec=spec, passed=False, iterations=0))
    cli.main(["--no-log", "--target", "1e4", "--series", "E12,R=E96"])
    assert seen.pop("s").value_series == {"resistor": "E96", "capacitor": "E12",
                                          "inductor": "E12"}
    cli.main(["--no-log", "--target", "1e4"])
    assert seen["s"].value_series is None


def test_cli_rejects_an_unknown_series(capsys):
    from spice_agent.agent import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--series", "R=E13"])
    assert "Unknown value series 'E13'" in capsys.readouterr().err


# ---------------------------------------------------------------------
# The check (simulation/availability.py)
# ---------------------------------------------------------------------
#
# Measured first (ngspice, 2026-11-23): RC low-pass 1.6k / 100n = 995 Hz;
# 1.59k / 100n = 1001 Hz. Both pass a 1 kHz +/-10 % spec on response alone.

V1 = {"id": "V1", "type": "voltage_source", "nodes": ["in", "0"], "value": "AC 1"}
E24_RC = {"resistor": "E24", "capacitor": "E12"}


def rc(r, cap, spec):
    circuit = {"title": "t", "components": [
        V1, {"id": "R1", "type": "resistor", "nodes": ["in", "out"], "value": r},
        {"id": "C1", "type": "capacitor", "nodes": ["out", "0"], "value": cap}],
        "analysis": analysis_for(spec)}
    return simulate_and_verify(circuit, spec)


def test_standard_values_pass():
    r = rc("1.6k", "100n", DesignSpec("lowpass", 1000, value_series=E24_RC))
    assert r["passed"] is True and r["availability"]["ok"] is True
    assert r["availability"]["series"] == E24_RC


def test_off_series_value_fails_but_is_still_simulated():
    r = rc("1.59k", "100n", DesignSpec("lowpass", 1000, value_series=E24_RC))
    assert r["passed"] is False
    assert r["cutoff_hz"] == pytest.approx(1001, rel=0.01)            # still measured
    assert r["message"].startswith("NOT BUILDABLE - R1 = 1.59k is not an E24 value; "
                                   "nearest 1.5 kohm (-5.7%) and 1.6 kohm (+0.6%)")
    (v,) = r["availability"]["violations"]
    assert (v["kind"], v["series"]) == ("series", "E24")
    assert v["below_si"] == pytest.approx(1.5e3) and v["above_si"] == pytest.approx(1.6e3)


def test_the_series_binds_on_ideal_technology_too():
    """A catalogue property: how ideal the parts are does not change which
    values exist."""
    r = rc("1.59k", "100n", DesignSpec("lowpass", 1000, value_series=E24_RC))
    assert DesignSpec("lowpass", 1000).technology == "ideal" and r["passed"] is False


def test_unlisted_part_types_are_unrestricted():
    r = rc("1.59k", "100n", DesignSpec("lowpass", 1000, value_series={"capacitor": "E6"}))
    assert r["passed"] is True


def test_spelling_of_a_standard_value_does_not_matter():
    for r_text in ("1600", "1.60k", "1.6e3"):
        assert rc(r_text, "0.1u", DesignSpec("lowpass", 1000, value_series=E24_RC))["passed"]


def test_range_and_series_are_reported_together():
    r = rc("1.59k", "470n", DesignSpec("lowpass", 212, value_series=E24_RC,
                                       technology="smd_pcb"))
    kinds = [(v["id"], v["kind"]) for v in r["availability"]["violations"]]
    assert sorted(kinds) == [("C1", "range"), ("R1", "series")]
    msg = r["message"]                                  # ranges first, then series
    assert msg.index("C1 = 470 nF exceeds") < msg.index("R1 = 1.59k is not an E24")


def test_an_off_series_value_is_a_design_failure_for_the_diagnosis():
    r = rc("1.59k", "100n", DesignSpec("lowpass", 1000, value_series=E24_RC,
                                       technology="breadboard"))
    assert r["parasitics"]["design_failures"] == ["availability"]
    assert r["parasitics"]["parasitic_failures"] == []


# ---------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------

def test_prompt_lists_every_value_of_each_required_series():
    from spice_agent.agent.prompts import build_system_prompt

    p = build_system_prompt(DesignSpec("lowpass", 1e4, value_series={"R": "E96", "C": "E12"}))
    assert "STANDARD VALUES (required, checked): resistors from E96: 1, 1.02, 1.05" in p
    assert "9.53, 9.76 (times any power of ten)" in p
    assert "capacitors from E12: 1, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2" in p
    assert "types not listed may take any value" in p


def test_types_sharing_a_series_get_one_table():
    from spice_agent.agent.prompts import build_series_note

    note = build_series_note(DesignSpec("lowpass", 1e4, value_series={"R": "E24", "C": "E24",
                                                                       "L": "E6"}))
    assert note.count("9.1") == 1
    assert "resistors and capacitors from E24" in note and "inductors from E6" in note


def test_no_series_note_by_default():
    from spice_agent.agent.prompts import build_system_prompt

    assert "STANDARD VALUES" not in build_system_prompt(DesignSpec("lowpass", 1e4))
