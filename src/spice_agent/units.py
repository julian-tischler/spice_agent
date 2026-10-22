"""
Parsing SPICE value strings.

Separate from everything else because both the parasitic model and the
netlist layer need it, and it depends on nothing.
"""

import re


# Order matters: "meg" must be tried before "m", or "10meg" parses as
# 10 milli. Note SPICE's classic trap -- suffixes are case-insensitive,
# so "1M" means 1 milli, NOT 1 mega. "1Meg" is mega.
_SUFFIXES: tuple[tuple[str, float], ...] = (
    ("mil", 25.4e-6),
    ("meg", 1e6),
    ("t", 1e12),
    ("g", 1e9),
    ("k", 1e3),
    ("m", 1e-3),
    ("u", 1e-6),
    ("n", 1e-9),
    ("p", 1e-12),
    ("f", 1e-15),
)

_NUMBER = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)\s*$")


def parse_spice_value(text: str | float | int) -> float:
    """
    '23.87m' -> 0.02387, '100n' -> 1e-07, '1Meg' -> 1000000.0, '1k5' is
    NOT supported (SPICE does not use it either). Trailing unit letters
    after a known suffix are ignored, so '10uF' and '1kOhm' both work.
    """
    if isinstance(text, (int, float)):
        return float(text)
    m = _NUMBER.match(str(text))
    if not m:
        raise ValueError(f"Cannot parse SPICE value {text!r}")
    mantissa, suffix = float(m.group(1)), m.group(2).lower()
    if not suffix:
        return mantissa
    for name, scale in _SUFFIXES:
        if suffix.startswith(name):
            return mantissa * scale
    # An unknown suffix is a bare unit like 'ohm' or 'hz'
    return mantissa
