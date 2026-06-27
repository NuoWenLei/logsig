"""Trivial error/alert-path detector [E] (stage 4, §2.1).

The conceded path (§1.2): novel-template and count-spike anomalies on the
error/alert stream. These are loud, already caught by most teams, and trivially
handled — this exists so the LLM filter receives the *union* of [E] and [5],
not to compete with the spectral layer.

  - novel_template: an alert template that never appeared in the training window
    shows up in the live region.
  - count_spike:    a known alert template firing far above its trailing rate.
"""

from __future__ import annotations

from typing import Dict, List, Set

import numpy as np

from .bucket import BinnedSeries, bucketize
from .config import Config
from .descriptors import AnomalyDescriptor
from .events import Event


def detect_trivial(trivial_events: List[Event], config: Config,
                   train_end_frac: float = None) -> List[AnomalyDescriptor]:
    """Run novelty + count-spike detection on the alert stream."""
    if not trivial_events:
        return []
    cfg = config
    binned = bucketize(trivial_events, cfg.bin_width_s)
    if train_end_frac is None:
        train_end_frac = cfg.baseline.train_end_frac
    train_end = int(round(train_end_frac * binned.n_bins))
    train_end = max(train_end, 8)

    descriptors: List[AnomalyDescriptor] = []

    # template_id -> first bin it ever appears
    first_seen: Dict[str, int] = {}
    for tid, series in binned.series.items():
        nz = np.nonzero(series)[0]
        if nz.size:
            first_seen[tid] = int(nz[0])

    known: Set[str] = {t for t, b in first_seen.items() if b < train_end}

    # --- novel_template ---
    for tid, first_bin in first_seen.items():
        if first_bin >= train_end and tid not in known:
            epoch = binned.time_for_bin(first_bin)
            descriptors.append(AnomalyDescriptor(
                template_id=tid,
                template_example=binned.templates.get(tid, ""),
                anomaly_type="novel_template",
                detected_at=epoch,
                detected_at_bin=first_bin,
                detection_latency_bins=None,
                baseline_summary={"seen_in_training": False},
                observed_summary={"first_count": int(binned.series[tid][first_bin])},
                source="trivial",
            ))

    # --- count_spike on known templates ---
    tc = cfg.trivial
    for tid in known:
        series = binned.series[tid].astype(float)
        train = series[:train_end]
        mu = float(np.mean(train))
        sd = float(np.std(train))
        thresh = max(mu + tc.spike_zscore * sd, float(tc.spike_min_count))
        live = series[train_end:]
        over = np.nonzero(live > thresh)[0]
        if over.size:
            bin_index = train_end + int(over[0])
            epoch = binned.time_for_bin(bin_index)
            descriptors.append(AnomalyDescriptor(
                template_id=tid,
                template_example=binned.templates.get(tid, ""),
                anomaly_type="count_spike",
                detected_at=epoch,
                detected_at_bin=bin_index,
                detection_latency_bins=None,
                baseline_summary={"mean": round(mu, 3), "std": round(sd, 3)},
                observed_summary={"count": int(series[bin_index]),
                                  "threshold": round(thresh, 2)},
                source="trivial",
            ))

    return descriptors
