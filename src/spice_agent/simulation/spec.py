"""
DesignSpec: what the agent is being asked to build.

A spec describes REQUIRED BEHAVIOUR only - a frequency, a tolerance, an
attenuation - never a topology or a component count. Working out how to
meet it is the designer's job.

Kept deliberately next to verify.py: every field added here needs a
matching branch in check_filter_spec(), and a field with no check is a
requirement that silently does not apply.
"""

import math
from dataclasses import asdict, dataclass
from typing import Literal

from spice_agent.circuit.parasitics import get_profile
from spice_agent.models.series import get_series


FilterType = Literal["lowpass", "highpass", "bandpass", "notch"]

# The default judged band: this many decades either side of the target.
# Out-of-band behaviour beyond it is reported, never failed - the user
# widens the window explicitly (judge_lo_hz / judge_hi_hz) when they care.
JUDGE_DECADES = 2

# Default limit on passband peaking (gain above the passband reference).
# Loose on purpose: catches undamped resonances (+12 dB was seen live)
# while still allowing deliberate Chebyshev-style ripple.
DEFAULT_MAX_PEAKING_DB = 3.0

# The passive part types a value series can be required for, and the names
# a user may give them (CLI "R=E96", or a dict key).
PASSIVE_TYPES = ("resistor", "capacitor", "inductor")
_TYPE_NAMES = {alias: t for t, letter in zip(PASSIVE_TYPES, "rcl")
               for alias in (t, t + "s", letter)}


def _part_type(name: str) -> str:
    key = str(name).strip().lower()
    if key not in _TYPE_NAMES:
        raise ValueError(f"Unknown part type {name!r} for a value series; use "
                         f"R / C / L (or resistor / capacitor / inductor)")
    return _TYPE_NAMES[key]


def parse_value_series(text: str) -> dict[str, str]:
    """
    The --series argument as a value_series dict.

        "E24"           every resistor, capacitor and inductor from E24
        "R=E96,C=E12"   resistors E96, capacitors E12, inductors unrestricted
        "E12,R=E96"     E12 for all, except resistors from E96
    """
    out: dict[str, str] = {}
    overrides: dict[str, str] = {}
    for token in (t.strip() for t in str(text).split(",")):
        if not token:
            continue
        if "=" in token:
            kind, name = token.split("=", 1)
            overrides[_part_type(kind)] = name.strip()
        else:
            out.update({t: token for t in PASSIVE_TYPES})
    out.update(overrides)
    if not out:
        raise ValueError(f"No value series in {text!r}")
    return out


@dataclass
class DesignSpec:
    """
    What the agent is asked to design. One target frequency covers all
    four filter types: it is the -3 dB cutoff for lowpass/highpass and
    the centre frequency for bandpass/notch.
    """
    filter_type: FilterType = "lowpass"
    target_hz: float = 1590.0
    tolerance_pct: float = 10.0
    # bandpass only: required -3dB bandwidth (checked with tolerance_pct).
    # None = don't care.
    bandwidth_hz: float | None = None
    # notch only: minimum attenuation at the notch relative to passband.
    min_notch_depth_db: float = 20.0
    # lowpass/highpass only: require at least stopband_atten_db of
    # attenuation BELOW THE PASSBAND across the whole stopband, not merely
    # at one frequency. This is what forces a higher-order topology: a
    # 1st-order filter only rolls off 20 dB per decade, so a steep
    # requirement cannot be met by an RC pair whatever its values.
    #
    # stopband_hz is the band edge nearest the passband (above it for a
    # lowpass, below it for a highpass). stopband_end_hz is the far edge,
    # away from the passband; left as None it runs to the edge of the judged
    # window (see judge_window), not to the end of the sweep. Requiring the whole band matters because a response can dip
    # at one frequency and recover - an inductor's self-resonance does
    # precisely that - so a single-point check can be satisfied by a
    # filter whose stopband is bad everywhere else.
    stopband_hz: float | None = None
    stopband_atten_db: float | None = None
    stopband_end_hz: float | None = None
    # How the circuit is assumed to be physically built. "ideal" (the
    # default) keeps every component perfect, so existing behaviour is
    # untouched; any other profile name from parasitics.PROFILES makes the
    # simulation include that technology's parasitics.
    technology: str = "ideal"
    # The frequency range that is judged. Left as None it is JUDGE_DECADES
    # decades either side of the target (extended to cover an explicit
    # stopband), clipped to the sweep. Everything the verdict looks at -
    # passband reference, shape checks, the default stopband end - lies
    # inside it; behaviour outside is reported as information only.
    # Setting either edge explicitly is how a user asks for MORE, and the
    # sweep is extended to cover it.
    judge_lo_hz: float | None = None
    judge_hi_hz: float | None = None
    # Maximum gain above the passband reference anywhere in the passband
    # (lowpass, highpass, notch). None switches the check off.
    max_peaking_db: float | None = DEFAULT_MAX_PEAKING_DB
    # The world around the filter, fixed by the harness and never part of
    # the model's circuit. 0 ohm source and no load (None) is an ideal
    # voltage source into an open output - exactly what is simulated when
    # nothing is specified; nothing is ever added that the user did not ask
    # for. When set, the harness inserts R_SOURCE between V1 and 'in' and
    # R_LOAD from 'out' to ground, both ideal, both outside the parasitics.
    source_ohm: float = 0.0
    load_ohm: float | None = None
    # Supply rails for active parts, held at the nodes 'vcc' / 'vee' by the
    # harness (the model may connect to them, never drive them). None = no
    # supplies: passive parts only. Both or neither; single-supply designs
    # give supply_neg_v=0.
    supply_pos_v: float | None = None
    supply_neg_v: float | None = None
    # Optional absolute passband gain target (dB, at the passband reference;
    # the peak for a bandpass). None = only relative checks, as for passive
    # filters. Active filters can have gain, so this is how a user asks for
    # unity (0) or, say, +20 dB.
    passband_gain_db: float | None = None
    gain_tolerance_db: float = 1.0
    # Maximum current drawn from EACH supply rail, amps (as a datasheet states
    # supply current). None = no budget. Judged with the op-amp grades'
    # catalogue quiescent current on every technology, so a power budget
    # binds even when the op-amps are simulated as ideal.
    max_supply_current_a: float | None = None
    # False = passive parts only even though rails exist ("the board has
    # +/-12 V, but I want a passive filter").
    allow_active: bool = True
    # Standard values the passive parts must come from: {part type: series},
    # e.g. {"resistor": "E96", "capacitor": "E12"} (see models/series.py).
    # A type left out is unrestricted; None (the default) restricts nothing.
    # A catalogue property of the parts, so it binds on every technology,
    # ideal included. Normalised in __post_init__ to full type names and
    # upper-case series names.
    value_series: dict[str, str] | None = None

    def __post_init__(self):
        if self.filter_type not in ("lowpass", "highpass", "bandpass", "notch"):
            raise ValueError(f"Unknown filter_type {self.filter_type!r}")
        if self.target_hz <= 0:
            raise ValueError("target_hz must be positive")

        get_profile(self.technology)   # raises on an unknown profile name

        if (self.stopband_hz is None) != (self.stopband_atten_db is None):
            raise ValueError(
                "stopband_hz and stopband_atten_db must be given together "
                "(or both left as None)."
            )
        if self.stopband_hz is not None:
            if self.filter_type not in ("lowpass", "highpass"):
                raise ValueError(
                    f"A stopband requirement is only defined for lowpass and "
                    f"highpass, not {self.filter_type!r}."
                )
            if self.stopband_atten_db <= 3.0:
                raise ValueError(
                    "stopband_atten_db must exceed 3 dB - the -3 dB point is the cutoff itself, not a stopband."
                )
            if self.filter_type == "lowpass" and self.stopband_hz <= self.target_hz:
                raise ValueError(
                    f"For a lowpass the stopband ({self.stopband_hz:g} Hz) must lie "
                    f"above the cutoff ({self.target_hz:g} Hz)."
                )
            if self.filter_type == "highpass" and self.stopband_hz >= self.target_hz:
                raise ValueError(
                    f"For a highpass the stopband ({self.stopband_hz:g} Hz) must lie "
                    f"below the cutoff ({self.target_hz:g} Hz)."
                )
        elif self.stopband_end_hz is not None:
            raise ValueError(
                "stopband_end_hz has no meaning without stopband_hz and "
                "stopband_atten_db."
            )

        if self.stopband_end_hz is not None:
            # The far edge must lie further from the passband than the near
            # edge, otherwise the band is empty or inverted.
            if self.filter_type == "lowpass" and self.stopband_end_hz <= self.stopband_hz:
                raise ValueError(
                    f"For a lowpass, stopband_end_hz ({self.stopband_end_hz:g} Hz) "
                    f"must lie above stopband_hz ({self.stopband_hz:g} Hz)."
                )
            if self.filter_type == "highpass" and self.stopband_end_hz >= self.stopband_hz:
                raise ValueError(
                    f"For a highpass, stopband_end_hz ({self.stopband_end_hz:g} Hz) "
                    f"must lie below stopband_hz ({self.stopband_hz:g} Hz)."
                )

        for name in ("judge_lo_hz", "judge_hi_hz"):
            v = getattr(self, name)
            if v is not None and v <= 0:
                raise ValueError(f"{name} must be positive")
        if self.judge_lo_hz is not None and self.judge_lo_hz >= self.target_hz:
            raise ValueError(f"judge_lo_hz ({self.judge_lo_hz:g} Hz) must lie below the "
                             f"target ({self.target_hz:g} Hz)")
        if self.judge_hi_hz is not None and self.judge_hi_hz <= self.target_hz:
            raise ValueError(f"judge_hi_hz ({self.judge_hi_hz:g} Hz) must lie above the "
                             f"target ({self.target_hz:g} Hz)")
        if self.stopband_hz is not None:
            lo, hi = self.judge_window()
            edges = [self.stopband_hz] + ([self.stopband_end_hz]
                                          if self.stopband_end_hz is not None else [])
            for e in edges:
                if not (lo * (1 - 1e-9) <= e <= hi * (1 + 1e-9)):
                    raise ValueError(
                        f"Stopband edge {e:g} Hz lies outside the judged window "
                        f"({lo:g}-{hi:g} Hz); widen judge_lo_hz / judge_hi_hz.")
        if self.max_peaking_db is not None and self.max_peaking_db < 0:
            raise ValueError("max_peaking_db must be >= 0 (or None to switch it off)")
        if self.source_ohm < 0:
            raise ValueError("source_ohm must be >= 0")
        if self.load_ohm is not None and self.load_ohm <= 0:
            raise ValueError("load_ohm must be positive, or None for no load")
        if (self.supply_pos_v is None) != (self.supply_neg_v is None):
            raise ValueError("supply_pos_v and supply_neg_v must be given together "
                             "(single supply: supply_neg_v=0)")
        if self.supply_pos_v is not None:
            if self.supply_pos_v <= 0 or self.supply_neg_v >= self.supply_pos_v:
                raise ValueError(f"supply rails must satisfy supply_neg_v < supply_pos_v "
                                 f"and supply_pos_v > 0, got {self.supply_pos_v:g} / "
                                 f"{self.supply_neg_v:g} V")
        if self.gain_tolerance_db <= 0:
            raise ValueError("gain_tolerance_db must be positive")
        if self.max_supply_current_a is not None:
            if self.max_supply_current_a <= 0:
                raise ValueError("max_supply_current_a must be positive")
            if not self.powered:
                raise ValueError("max_supply_current_a needs supply rails "
                                 "(supply_pos_v / supply_neg_v)")
        if self.value_series is not None:
            normalised = {}
            for kind, name in dict(self.value_series).items():
                get_series(name)                        # raises on an unknown series
                normalised[_part_type(kind)] = str(name).strip().upper()
            self.value_series = {t: normalised[t] for t in PASSIVE_TYPES
                                 if t in normalised} or None

    def series_for(self, component_type: str) -> str | None:
        """The value series a part type must use, or None if unrestricted."""
        return (self.value_series or {}).get(component_type)

    @property
    def powered(self) -> bool:
        return self.supply_pos_v is not None

    @property
    def active_allowed(self) -> bool:
        """Op-amps may be used: rails exist and the user did not forbid it."""
        return self.powered and self.allow_active

    def stopband_end(self, f_lo: float | None = None, f_hi: float | None = None) -> float:
        """The stopband's far edge: explicit, else the judged-window edge."""
        if self.stopband_end_hz is not None:
            return self.stopband_end_hz
        lo, hi = self.judge_window(f_lo, f_hi)
        return hi if self.filter_type == "lowpass" else lo

    @property
    def terminated(self) -> bool:
        return self.source_ohm > 0 or self.load_ohm is not None

    def judge_window(self, f_lo: float | None = None,
                     f_hi: float | None = None) -> tuple[float, float]:
        """
        The judged frequency range, (lo, hi).

        Explicit judge_lo_hz / judge_hi_hz are used as given. A default edge
        is JUDGE_DECADES decades from the target, widened when needed so an
        explicit stopband lies inside (a decade past its edge when it has no
        explicit end), and clipped to the sweep [f_lo, f_hi] when given.
        """
        span = 10.0 ** JUDGE_DECADES
        lo = self.judge_lo_hz if self.judge_lo_hz is not None else self.target_hz / span
        hi = self.judge_hi_hz if self.judge_hi_hz is not None else self.target_hz * span
        if self.stopband_hz is not None:
            if self.filter_type == "lowpass" and self.judge_hi_hz is None:
                need = (self.stopband_end_hz if self.stopband_end_hz is not None
                        else self.stopband_hz * 10)
                hi = max(hi, need)
            if self.filter_type == "highpass" and self.judge_lo_hz is None:
                need = (self.stopband_end_hz if self.stopband_end_hz is not None
                        else self.stopband_hz / 10)
                lo = min(lo, need)
        if f_lo is not None:
            lo = max(lo, f_lo)
        if f_hi is not None:
            hi = min(hi, f_hi)
        return lo, hi

    @property
    def min_order(self) -> int | None:
        """
        Lowest filter order that could possibly meet the stopband
        requirement, from the ideal -20*n dB/decade asymptote. Advisory
        only - a real filter needs a margin on this, and the spec is
        always checked by simulation, never by this estimate.
        """
        if self.stopband_hz is None:
            return None
        decades = abs(math.log10(self.stopband_hz / self.target_hz))
        if decades == 0:
            return None
        return max(1, math.ceil((self.stopband_atten_db - 3.0) / (20.0 * decades)))

    @property
    def frequency_label(self) -> str:
        return "cutoff frequency (-3dB point)" if self.filter_type in ("lowpass", "highpass") \
            else "centre frequency"

    def describe(self) -> str:
        """Human/LLM-readable one-liner of the requirement."""
        parts = [
            f"{self.filter_type} filter, {self.frequency_label} "
            f"{self.target_hz:g} Hz (+/-{self.tolerance_pct:g}%)"
        ]
        if self.filter_type == "bandpass" and self.bandwidth_hz is not None:
            parts.append(f"-3dB bandwidth {self.bandwidth_hz:g} Hz (+/-{self.tolerance_pct:g}%)")
        if self.filter_type == "notch":
            parts.append(f"notch depth at least {self.min_notch_depth_db:g} dB below passband")
        if self.stopband_hz is not None:
            # State the whole band, not "above X": the requirement holds
            # across all of it, and with no explicit end it stops at the
            # judged-window edge - the model must be told exactly that.
            lo, hi = sorted((self.stopband_hz, self.stopband_end()))
            band = f"everywhere from {lo:g} Hz to {hi:g} Hz"
            parts.append(
                f"at least {self.stopband_atten_db:g} dB below passband {band}"
            )
        if self.max_peaking_db is not None and self.filter_type != "bandpass":
            parts.append(f"passband may not peak more than {self.max_peaking_db:g} dB "
                         f"above its level")
        if self.judge_lo_hz is not None or self.judge_hi_hz is not None:
            lo, hi = self.judge_window()
            parts.append(f"judged from {lo:g} Hz to {hi:g} Hz")
        if self.terminated:
            src = f"{self.source_ohm:g} ohm source" if self.source_ohm > 0 else "ideal source"
            load = f"{self.load_ohm:g} ohm load" if self.load_ohm is not None else "no load"
            parts.append(f"driven from a {src} into a {load}")
        if self.passband_gain_db is not None:
            where = "peak gain" if self.filter_type == "bandpass" else "passband gain"
            parts.append(f"{where} {self.passband_gain_db:g} dB "
                         f"(+/-{self.gain_tolerance_db:g} dB)")
        if self.powered:
            parts.append(f"supply rails {self.supply_pos_v:g} V / {self.supply_neg_v:g} V")
            if self.max_supply_current_a is not None:
                parts.append(f"at most {self.max_supply_current_a * 1e3:g} mA from each "
                             f"supply rail")
            if not self.allow_active:
                parts.append("passive parts only")
        if self.value_series:
            parts.append("standard values only: " + ", ".join(
                f"{t}s {s}" for t, s in self.value_series.items()))
        return "; ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)
