"""Bucketing: events → per-template count-per-bin series (stage 3).

§3.2: bin width is the Nyquist ceiling — to observe a period T you need bin
width ≤ T/2, and you want to oversample (≤ T/4..T/5). Bin width is a config
parameter, not a constant; the Stage-0 survey justifies the default empirically.

§3.3 output: a table keyed by template_id, each value a uniformly-binned integer
count series over the full span, plus the bin width and bin edges used. Sparse
(mostly-zero) series are fine — they self-select out at the eligibility gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

import numpy as np

from .events import Event


@dataclass
class BinnedSeries:
    """Per-template uniformly-binned count series over a shared time grid."""

    bin_width_s: float
    t0: float                       # epoch of the left edge of bin 0
    n_bins: int
    series: Dict[str, np.ndarray]   # template_id -> int count array, len n_bins
    levels: Dict[str, str]          # template_id -> representative level
    templates: Dict[str, str]       # template_id -> example template string

    def bin_edges(self) -> np.ndarray:
        return self.t0 + np.arange(self.n_bins + 1) * self.bin_width_s

    def bin_centers(self) -> np.ndarray:
        return self.t0 + (np.arange(self.n_bins) + 0.5) * self.bin_width_s

    def bin_index_for_time(self, ts: float) -> int:
        return int((ts - self.t0) // self.bin_width_s)

    def time_for_bin(self, idx: int) -> float:
        return self.t0 + idx * self.bin_width_s

    @property
    def template_ids(self) -> List[str]:
        return list(self.series.keys())


def bucketize(events: Iterable[Event], bin_width_s: float) -> BinnedSeries:
    """Bin events into per-template integer count series on a shared grid."""
    events = list(events)
    if not events:
        return BinnedSeries(bin_width_s, 0.0, 0, {}, {}, {})

    ts_all = np.array([e.timestamp for e in events], dtype=float)
    t0 = float(ts_all.min())
    t1 = float(ts_all.max())
    span = max(t1 - t0, bin_width_s)
    n_bins = int(np.ceil(span / bin_width_s)) + 1

    # group events by template
    by_tmpl: Dict[str, List[float]] = {}
    levels: Dict[str, str] = {}
    templates: Dict[str, str] = {}
    for e in events:
        by_tmpl.setdefault(e.template_id, []).append(e.timestamp)
        levels.setdefault(e.template_id, e.level)
        templates.setdefault(e.template_id, e.template)

    edges = t0 + np.arange(n_bins + 1) * bin_width_s
    series: Dict[str, np.ndarray] = {}
    for tid, times in by_tmpl.items():
        counts, _ = np.histogram(np.asarray(times, dtype=float), bins=edges)
        series[tid] = counts.astype(np.int64)

    return BinnedSeries(
        bin_width_s=bin_width_s,
        t0=t0,
        n_bins=n_bins,
        series=series,
        levels=levels,
        templates=templates,
    )
