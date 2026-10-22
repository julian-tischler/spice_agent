"""
Locating the external simulator binaries.

Nothing here is specific to this machine: each finder tries, in order,
an explicit override in constants.py, an environment variable, the
PATH, and finally the usual install locations for the platform. That
way a fresh clone works without editing source, and a non-standard
install is still a one-line override.

    from spice_agent.paths import find_ngspice, require_ngspice
    exe = require_ngspice()        # raises a helpful error if missing
    exe = find_ngspice()           # returns None if missing
"""

from __future__ import annotations

import os
import shutil
import sys
from glob import glob
from pathlib import Path

# ---------------------------------------------------------------------
# Optional overrides. Leave both as None: the finders below locate the
# executables on PATH or in the usual install locations, so a fresh clone
# works unedited. Set one only for a non-standard install -- and prefer
# the NGSPICE_PATH / LTSPICE_PATH environment variables, which do the same
# job without touching source.
# ---------------------------------------------------------------------

NGSPICE_PATH: str | None = None
LTSPICE_PATH: str | None = None


_WINDOWS = sys.platform == "win32"
_HOME = Path.home()

# Glob patterns, expanded in order. ~ and %VARS% are expanded first.
_NGSPICE_LOCATIONS = [
    r"C:\Program Files\ngspice*\bin\ngspice_con.exe",
    r"C:\Program Files (x86)\ngspice*\bin\ngspice_con.exe",
    r"C:\Spice64\bin\ngspice_con.exe",
    "~/ngspice*/Spice64/bin/ngspice_con.exe",
    "~/ngspice*/bin/ngspice_con.exe",
    "/usr/bin/ngspice",
    "/usr/local/bin/ngspice",
    "/opt/homebrew/bin/ngspice",
] if _WINDOWS else [
    "/usr/bin/ngspice",
    "/usr/local/bin/ngspice",
    "/opt/homebrew/bin/ngspice",
    "~/ngspice*/bin/ngspice",
]

_LTSPICE_LOCATIONS = [
    r"%LOCALAPPDATA%\Programs\ADI\LTspice\LTspice.exe",
    r"C:\Program Files\ADI\LTspice\LTspice.exe",
    r"C:\Program Files\LTC\LTspiceXVII\XVIIx64.exe",
    r"C:\Program Files (x86)\LTC\LTspiceIV\scad3.exe",
    "~/Applications/LTspice.app/Contents/MacOS/LTspice",
    "/Applications/LTspice.app/Contents/MacOS/LTspice",
]

# ngspice_con is the batch-friendly console build on Windows; ngspice is
# the usual name everywhere else. Order matters — prefer the console one.
_NGSPICE_EXE_NAMES = ("ngspice_con", "ngspice") if _WINDOWS else ("ngspice",)
_LTSPICE_EXE_NAMES = ("LTspice", "XVIIx64", "ltspice")


def _first_existing(patterns: list[str]) -> str | None:
    for pattern in patterns:
        expanded = os.path.expandvars(os.path.expanduser(pattern))
        # sorted() so ngspice-47 beats ngspice-44 when several are installed
        for hit in sorted(glob(expanded), reverse=True):
            if os.path.isfile(hit):
                return hit
    return None


def _find(override: str | None, env_var: str, exe_names: tuple[str, ...],
          locations: list[str]) -> str | None:
    if override and os.path.isfile(override):
        return override
    from_env = os.environ.get(env_var)
    if from_env and os.path.isfile(from_env):
        return from_env
    for name in exe_names:
        on_path = shutil.which(name)
        if on_path:
            return on_path
    return _first_existing(locations)


def find_ngspice() -> str | None:
    """Path to the ngspice binary, or None if it can't be found."""
    return _find(NGSPICE_PATH, "NGSPICE_PATH", _NGSPICE_EXE_NAMES, _NGSPICE_LOCATIONS)


def find_ltspice() -> str | None:
    """Path to the LTspice executable, or None if it can't be found."""
    return _find(LTSPICE_PATH, "LTSPICE_PATH", _LTSPICE_EXE_NAMES, _LTSPICE_LOCATIONS)


def _not_found_message(what: str, env_var: str, const_name: str,
                       exe_names: tuple[str, ...], locations: list[str]) -> str:
    return (
        f"{what} not found. Looked for {' / '.join(exe_names)} on PATH, in the "
        f"{env_var} environment variable, in {const_name} in "
        f"spice_agent/constants.py, and in:\n  "
        + "\n  ".join(os.path.expandvars(os.path.expanduser(p)) for p in locations)
        + f"\nInstall it, or set {env_var} to the full path of the executable."
    )


def require_ngspice() -> str:
    """Path to ngspice, or FileNotFoundError explaining where we looked."""
    exe = find_ngspice()
    if exe is None:
        raise FileNotFoundError(_not_found_message(
            "ngspice", "NGSPICE_PATH", "NGSPICE_PATH", _NGSPICE_EXE_NAMES, _NGSPICE_LOCATIONS))
    return exe


def require_ltspice() -> str:
    """Path to LTspice, or FileNotFoundError explaining where we looked."""
    exe = find_ltspice()
    if exe is None:
        raise FileNotFoundError(_not_found_message(
            "LTspice", "LTSPICE_PATH", "LTSPICE_PATH", _LTSPICE_EXE_NAMES, _LTSPICE_LOCATIONS))
    return exe


if __name__ == "__main__":
    for name, finder in (("ngspice", find_ngspice), ("LTspice", find_ltspice)):
        print(f"{name:8s}: {finder() or '(not found)'}")
