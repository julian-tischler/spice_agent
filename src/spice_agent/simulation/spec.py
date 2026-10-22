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


FilterType = Literal["lowpass", "highpass", "bandpass", "notch"]

# How far below the passband the response must be at the far end of the
# sweep (or on both sides of a band-pass peak) to count as "actually
# behaving like that filter type". Stops a flat divider or a mis-wired


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
    # away from the passband; left as None it runs to the end of the swept
    # range. Requiring the whole band matters because a response can dip
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
            # Say "above"/"below" rather than "at": the requirement holds
            # across the whole stopband, not at a single frequency.
            direction = "above" if self.filter_type == "lowpass" else "below"
            band = f"{direction} {self.stopband_hz:g} Hz"
            if self.stopband_end_hz is not None:
                lo, hi = sorted((self.stopband_hz, self.stopband_end_hz))
                band = f"everywhere from {lo:g} Hz to {hi:g} Hz"
            parts.append(
                f"at least {self.stopband_atten_db:g} dB below passband {band}"
            )
        return "; ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)
