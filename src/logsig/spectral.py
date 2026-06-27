"""Spectral primitives: Welch power spectrum, flatness, dominant frequency.

§4.1: use Welch (averaged windowed periodograms) — a *distribution of windowed
spectra*, not one global FFT — so non-stationarity (ramps, deploys, diurnal
cycles) does not drown real anomalies in leakage.

§4.2: the per-template signature is {dominant freq(s), spectral flatness,
amplitude at dominant freq}.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy import signal as sp_signal


@dataclass
class Spectrum:
    """A Welch power spectral estimate of a count series."""

    freqs: np.ndarray            # Hz (cycles per second), excludes DC at index 0
    power: np.ndarray            # power at each freq (DC-excluded, same length)
    fs: float                    # sampling rate (1 / bin_width_s)
    mean_count: float            # base rate: mean of the input series
    total_power: float           # sum of non-DC power

    @property
    def flatness(self) -> float:
        """Spectral flatness = geometric-mean / arithmetic-mean of the power.

        Near 0 ⇒ clean tone (peaky). Near 1 ⇒ white noise (no structure).
        Computed over non-DC bins."""
        return spectral_flatness(self.power)

    @property
    def dominant_idx(self) -> Optional[int]:
        if self.power.size == 0 or self.total_power <= 0:
            return None
        return int(np.argmax(self.power))

    @property
    def dominant_freq(self) -> Optional[float]:
        i = self.dominant_idx
        if i is None:
            return None
        f = float(self.freqs[i])
        return f if f > 0 else None

    @property
    def dominant_period_s(self) -> Optional[float]:
        f = self.dominant_freq
        if f is None or f <= 0:
            return None
        return 1.0 / f

    @property
    def dominant_amplitude(self) -> float:
        i = self.dominant_idx
        if i is None:
            return 0.0
        return float(self.power[i])

    @property
    def peak_prominence(self) -> float:
        """Fraction of total non-DC power carried by the dominant peak."""
        if self.total_power <= 0:
            return 0.0
        return self.dominant_amplitude / self.total_power

    def amplitude_at_period(self, period_s: float) -> float:
        """Power at the frequency bin nearest a target period (for rate-shift
        and silence comparisons)."""
        if period_s is None or period_s <= 0 or self.freqs.size == 0:
            return 0.0
        target_f = 1.0 / period_s
        idx = int(np.argmin(np.abs(self.freqs - target_f)))
        return float(self.power[idx])


def spectral_flatness(power: np.ndarray) -> float:
    """Geometric/arithmetic mean ratio of a non-negative power spectrum."""
    p = np.asarray(power, dtype=float)
    p = p[np.isfinite(p)]
    if p.size == 0:
        return 1.0
    amean = float(np.mean(p))
    if amean <= 0:
        return 1.0
    # add a tiny floor so an exact zero bin does not zero out the geo mean
    floor = amean * 1e-12
    gmean = float(np.exp(np.mean(np.log(p + floor))))
    return min(1.0, gmean / amean)


def welch_spectrum(series: np.ndarray, fs: float, nperseg_bins: int,
                   overlap_frac: float = 0.5, window: str = "hann",
                   detrend: str = "constant") -> Spectrum:
    """Compute a Welch power spectrum of a 1-D count series.

    nperseg is clamped to the series length. DC bin is dropped (we care about
    periodicity, not the base level — the base level is tracked separately via
    ``mean_count``)."""
    x = np.asarray(series, dtype=float)
    mean_count = float(np.mean(x)) if x.size else 0.0
    if x.size < 4:
        return Spectrum(np.array([]), np.array([]), fs, mean_count, 0.0)

    nperseg = int(min(nperseg_bins, x.size))
    nperseg = max(nperseg, 8)
    noverlap = int(nperseg * overlap_frac)
    try:
        f, pxx = sp_signal.welch(
            x, fs=fs, window=window, nperseg=nperseg, noverlap=noverlap,
            detrend=detrend, scaling="spectrum",
        )
    except ValueError:
        return Spectrum(np.array([]), np.array([]), fs, mean_count, 0.0)

    # drop DC
    f = f[1:]
    pxx = pxx[1:]
    total = float(np.sum(pxx))
    return Spectrum(freqs=f, power=pxx, fs=fs, mean_count=mean_count,
                    total_power=total)
