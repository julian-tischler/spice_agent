"""
Tests for simulator binary discovery (spice_agent.paths).

The search order is what matters: explicit override, then environment
variable, then PATH, then known install locations. These tests drive the
generic `_find` helper with fakes so they pass on any machine, plus a
couple of checks that the real finders locate something here.
"""

import os

import pytest

from spice_agent import paths


@pytest.fixture
def fake_exe(tmp_path):
    def make(name):
        p = tmp_path / name
        p.write_text("")
        return str(p)
    return make


def test_override_wins(monkeypatch, fake_exe):
    override, other = fake_exe("override.exe"), fake_exe("env.exe")
    monkeypatch.setenv("FAKE_PATH", other)
    assert paths._find(override, "FAKE_PATH", ("nothing-here",), []) == override


def test_env_var_used_when_no_override(monkeypatch, fake_exe):
    env = fake_exe("env.exe")
    monkeypatch.setenv("FAKE_PATH", env)
    assert paths._find(None, "FAKE_PATH", ("nothing-here",), []) == env


def test_missing_override_falls_through(monkeypatch, fake_exe):
    env = fake_exe("env.exe")
    monkeypatch.setenv("FAKE_PATH", env)
    assert paths._find(r"C:\does\not\exist.exe", "FAKE_PATH", ("nothing-here",), []) == env


def test_path_lookup(monkeypatch):
    monkeypatch.delenv("FAKE_PATH", raising=False)
    monkeypatch.setattr(paths.shutil, "which",
                        lambda n: r"C:\on\path.exe" if n == "wanted" else None)
    assert paths._find(None, "FAKE_PATH", ("missing", "wanted"), []) == r"C:\on\path.exe"


def test_known_locations_are_globbed(monkeypatch, tmp_path, fake_exe):
    monkeypatch.delenv("FAKE_PATH", raising=False)
    monkeypatch.setattr(paths.shutil, "which", lambda n: None)
    (tmp_path / "ngspice-44" / "bin").mkdir(parents=True)
    (tmp_path / "ngspice-47" / "bin").mkdir(parents=True)
    for v in ("44", "47"):
        (tmp_path / f"ngspice-{v}" / "bin" / "ngspice.exe").write_text("")
    found = paths._find(None, "FAKE_PATH", ("nothing",),
                        [str(tmp_path / "ngspice-*" / "bin" / "ngspice.exe")])
    assert found.endswith(os.path.join("ngspice-47", "bin", "ngspice.exe")), \
        "newest versioned install should win"


def test_returns_none_when_nothing_found(monkeypatch):
    monkeypatch.delenv("FAKE_PATH", raising=False)
    monkeypatch.setattr(paths.shutil, "which", lambda n: None)
    assert paths._find(None, "FAKE_PATH", ("nothing",), [r"C:\nope\*.exe"]) is None


def test_directories_are_not_matched(monkeypatch, tmp_path):
    monkeypatch.delenv("FAKE_PATH", raising=False)
    monkeypatch.setattr(paths.shutil, "which", lambda n: None)
    (tmp_path / "ngspice.exe").mkdir()          # a directory, not a file
    assert paths._find(None, "FAKE_PATH", ("nothing",), [str(tmp_path / "*.exe")]) is None


def test_require_error_message_is_actionable(monkeypatch):
    monkeypatch.setattr(paths, "find_ngspice", lambda: None)
    with pytest.raises(FileNotFoundError) as e:
        paths.require_ngspice()
    msg = str(e.value)
    assert "NGSPICE_PATH" in msg          # says which env var to set
    assert "constants.py" in msg          # and where the override lives
    assert "PATH" in msg


# ---- the real finders on this machine -------------------------------

def test_ngspice_is_found_without_hardcoded_constant():
    """ngspice must be discoverable by search alone - the override is an
    escape hatch, not the mechanism. A fresh clone has to work unedited."""
    assert getattr(paths, "NGSPICE_PATH", None) is None, \
        "paths.py should not ship a machine-specific override"
    exe = paths.find_ngspice()
    assert exe is not None and os.path.isfile(exe)


def test_require_ngspice_returns_the_same_path():
    assert paths.require_ngspice() == paths.find_ngspice()
