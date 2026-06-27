"""Baseline signatures + eligibility gate (stage 4) behind a swappable interface.

§5.3 (non-negotiable for v0): the baseline lives behind a *named, swappable
interface* — "what is my reference signature for this template?" is a strategy
object, not logic hardcoded into the detector loop. v0 ships only the fixed
strategy (§5.1); v1's rolling/disagreement work (§5.4, documented in
docs/DESIGN.md) is the only thing that needs to reach back and touch this.

§4.2 signature: dominant freq(s), spectral flatness, amplitude at dominant freq.
§4.3 eligibility: a template can fire a periodicity-break only if it established
a stable dominant tone over the baseline window (flatness below threshold and a
prominent peak). Always-flat templates never earn a baseline worth deviating
from — that is how perpetually-aperiodic non-errors self-select out, not via an
upfront classifier. (Burst-onset still fires for non-eligible templates, §5.2.)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Dict, Optional

import numpy as np

from .bucket import BinnedSeries
from .config import Config
from .spectral import Spectrum, welch_spectrum


@dataclass
class TemplateSignature:
    """The per-template reference signature (§4.2) + eligibility verdict (§4.3)."""

    template_id: str
    mean_count: float
    flatness: float
    dominant_freq: Optional[float]
    dominant_period_s: Optional[float]
    dominant_amplitude: float
    peak_prominence: float
    eligible: bool                 # may fire silence/drift/freq_shift
    n_bins: int                    # bins observed in the training window

    def summary(self) -> dict:
        """Compact, human-nameable summary for the descriptor contract (§6.2)."""
        return {
            "dominant_period_s": (round(self.dominant_period_s, 2)
                                  if self.dominant_period_s else None),
            "flatness": round(self.flatness, 3),
            "mean_count": round(self.mean_count, 4),
            "eligible": self.eligible,
        }


def _signature_from_spectrum(tid: str, spec: Spectrum, cfg: Config,
                             n_bins: int) -> TemplateSignature:
    elig_cfg = cfg.eligibility
    flatness = spec.flatness
    established = spec.mean_count >= elig_cfg.min_baseline_mean_count
    peaky = flatness < elig_cfg.flatness_threshold
    prominent = spec.peak_prominence >= elig_cfg.min_peak_prominence
    has_tone = spec.dominant_freq is not None
    eligible = bool(established and peaky and prominent and has_tone)
    return TemplateSignature(
        template_id=tid,
        mean_count=spec.mean_count,
        flatness=flatness,
        dominant_freq=spec.dominant_freq,
        dominant_period_s=spec.dominant_period_s,
        dominant_amplitude=spec.dominant_amplitude,
        peak_prominence=spec.peak_prominence,
        eligible=eligible,
        n_bins=n_bins,
    )


class BaselineStrategy(ABC):
    """Strategy interface: produce a reference signature per template (§5.3)."""

    name: str = "abstract"

    @abstractmethod
    def fit(self, binned: BinnedSeries) -> "Baseline":
        ...


@dataclass
class Baseline:
    """A fitted set of reference signatures plus the window it was fit on."""

    strategy_name: str
    bin_width_s: float
    train_start_bin: int
    train_end_bin: int
    signatures: Dict[str, TemplateSignature]

    def signature(self, template_id: str) -> Optional[TemplateSignature]:
        return self.signatures.get(template_id)

    @property
    def eligible_ids(self):
        return [t for t, s in self.signatures.items() if s.eligible]


class FixedBaseline(BaselineStrategy):
    """v0: a single fixed training window defines 'normal' (§5.1).

    Known weakness — every deploy looks like a cliff of simultaneous deltas. The
    spec treats that as a *feature*: a synchronized cliff is a strong
    deploy-detection signal handed to the LLM filter (via cooccurring_flags),
    not a problem to suppress here.
    """

    name = "fixed"

    def __init__(self, config: Config):
        self.config = config

    def _window_bins(self, n_bins: int) -> tuple:
        bc = self.config.baseline
        start = int(round(bc.train_start_frac * n_bins))
        end = int(round(bc.train_end_frac * n_bins))
        end = max(end, start + 8)
        end = min(end, n_bins)
        return start, end

    def fit(self, binned: BinnedSeries) -> Baseline:
        cfg = self.config
        fs = 1.0 / cfg.bin_width_s
        start, end = self._window_bins(binned.n_bins)
        sigs: Dict[str, TemplateSignature] = {}
        for tid, series in binned.series.items():
            window = series[start:end]
            spec = welch_spectrum(
                window, fs=fs,
                nperseg_bins=cfg.baseline.welch_nperseg_bins,
                overlap_frac=cfg.baseline.welch_overlap_frac,
                window=cfg.baseline.welch_window,
                detrend=cfg.baseline.welch_detrend,
            )
            sigs[tid] = _signature_from_spectrum(tid, spec, cfg, len(window))
        return Baseline(
            strategy_name=self.name,
            bin_width_s=cfg.bin_width_s,
            train_start_bin=start,
            train_end_bin=end,
            signatures=sigs,
        )


_STRATEGIES = {
    "fixed": FixedBaseline,
    # v1 (DO NOT BUILD): "rolling", "disagreement" — see docs/DESIGN.md §5.4.
}


def make_baseline_strategy(config: Config) -> BaselineStrategy:
    """Factory honoring config.baseline_strategy (§5.3 swappable interface)."""
    name = config.baseline_strategy
    if name not in _STRATEGIES:
        raise ValueError(
            f"unknown baseline_strategy {name!r}; available: {list(_STRATEGIES)} "
            f"(v1 strategies are documented but not built in v0)"
        )
    return _STRATEGIES[name](config)
