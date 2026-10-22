"""
Frequency-response metrics extracted from an AC sweep DataFrame.

Every function takes a DataFrame with at least the columns
    freq      (Hz, ascending)
    gain_db   (transfer-function magnitude in dB)
as produced by simulate.run_ngspice_ac() or tests/template_runner.py.

Pure numpy/pandas — no ngspice or LLM dependency, so these are fully
unit-testable and shared between the spec checker and the test suite.
"""

import numpy as np
import pandas as pd


def _interp_crossing(freq, gain, target, i_before, i_after) -> float:
    """Linear interpolation of the frequency where gain crosses target
    between two adjacent sample indices."""
    f1, f2 = freq[i_before], freq[i_after]
    g1, g2 = gain[i_before], gain[i_after]
    if g2 == g1:
        return float(f2)
    frac = (target - g1) / (g2 - g1)
    return float(f1 + frac * (f2 - f1))


def gain_at(df: pd.DataFrame, freq_hz: float) -> float:
    """Gain in dB at an arbitrary frequency, interpolated in log-f."""
    return float(np.interp(np.log10(freq_hz), np.log10(df["freq"]), df["gain_db"]))


def lowpass_cutoff(df: pd.DataFrame, reference_db: float | None = None) -> float | None:
    """
    -3 dB point relative to the passband gain, scanning upward from the
    lowest swept frequency. If reference_db is None the gain at the lowest
    frequency is used as the passband reference. Returns None if the
    response never drops 3 dB within the swept range.
    """
    freq, gain = df["freq"].to_numpy(), df["gain_db"].to_numpy()
    if reference_db is None:
        reference_db = gain[0]
    target = reference_db - 3.0
    below = np.where(gain <= target)[0]
    if len(below) == 0:
        return None
    i = int(below[0])
    return float(freq[0]) if i == 0 else _interp_crossing(freq, gain, target, i - 1, i)


def highpass_cutoff(df: pd.DataFrame, reference_db: float | None = None) -> float | None:
    """
    -3 dB point relative to the passband gain, scanning downward from the
    highest swept frequency. Mirror image of lowpass_cutoff().
    """
    freq, gain = df["freq"].to_numpy(), df["gain_db"].to_numpy()
    if reference_db is None:
        reference_db = gain[-1]
    target = reference_db - 3.0
    below = np.where(gain <= target)[0]
    if len(below) == 0:
        return None
    i = int(below[-1])
    return float(freq[-1]) if i == len(freq) - 1 else _interp_crossing(freq, gain, target, i, i + 1)


def peak(df: pd.DataFrame) -> tuple[float, float]:
    """(frequency, gain_db) of the maximum gain."""
    i = int(np.argmax(df["gain_db"].to_numpy()))
    return float(df["freq"].iloc[i]), float(df["gain_db"].iloc[i])


def notch(df: pd.DataFrame) -> tuple[float, float]:
    """(frequency, gain_db) of the minimum gain."""
    i = int(np.argmin(df["gain_db"].to_numpy()))
    return float(df["freq"].iloc[i]), float(df["gain_db"].iloc[i])


def bandpass_bandwidth(df: pd.DataFrame) -> float | None:
    """-3 dB bandwidth around the peak gain. None if either edge lies
    outside the swept range."""
    freq, gain = df["freq"].to_numpy(), df["gain_db"].to_numpy()
    i_peak = int(np.argmax(gain))
    target = gain[i_peak] - 3.0

    lower = np.where(gain[:i_peak] <= target)[0]
    upper = np.where(gain[i_peak:] <= target)[0]
    if len(lower) == 0 or len(upper) == 0:
        return None

    i_lo = int(lower[-1])
    i_hi = i_peak + int(upper[0])
    f_lo = _interp_crossing(freq, gain, target, i_lo, i_lo + 1)
    f_hi = _interp_crossing(freq, gain, target, i_hi - 1, i_hi)
    return f_hi - f_lo


def worst_attenuation(df: pd.DataFrame, passband_db: float,
                      f_from: float, f_to: float) -> tuple[float, float]:
    """
    The SMALLEST attenuation below `passband_db` anywhere in the band
    between f_from and f_to, and the frequency where it occurs.

    Checking a stopband at a single frequency is not enough: a response
    that happens to dip at that one point and recover elsewhere passes
    while being useless. That is exactly what an inductor's self-resonance
    does - attenuation improves up to the resonance, then degrades again -
    so the stopband has to be judged by its worst point, not a sample.

    Both band edges are included by interpolation, since they rarely land
    exactly on a swept sample point. Order of f_from/f_to does not matter,
    which lets a highpass pass its band in descending order.
    """
    lo, hi = (f_from, f_to) if f_from <= f_to else (f_to, f_from)
    freq = df["freq"].to_numpy()
    gain = df["gain_db"].to_numpy()

    inside = (freq > lo) & (freq < hi)
    freqs = np.concatenate(([lo, hi], freq[inside]))
    gains = np.concatenate(([gain_at(df, lo), gain_at(df, hi)], gain[inside]))

    attens = passband_db - gains
    i = int(np.argmin(attens))
    return float(attens[i]), float(freqs[i])


def rolloff_db_per_decade(df: pd.DataFrame, f_start: float, f_stop: float) -> float:
    """Slope of the gain curve between two frequencies, in dB/decade.
    Negative for a falling response."""
    return (gain_at(df, f_stop) - gain_at(df, f_start)) / np.log10(f_stop / f_start)
