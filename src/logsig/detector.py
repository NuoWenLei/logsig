"""Spectral detector — delta-only firing (stage 5, §5.2).

The output is NEVER "is aperiodic"; it is "spectral signature changed from
baseline". Every flag is a transition (delta) against the per-template baseline
signature. The "always-flat stays flat ⇒ do not flag" row is the entire reason
the LLM filter stays usable.

Firing rules (all thresholds in DetectConfig):

| baseline state        | current state          | fire | type        |
|-----------------------|------------------------|------|-------------|
| periodic (had a tone) | energy collapsed       | yes  | silence     |
| periodic              | dominant freq shifted  | yes  | freq_shift  |
| periodic              | tone smeared (flatter) | yes  | drift       |
| quiet / absent        | sudden dense run       | yes  | burst_onset |
| periodic              | tone stable            | no   | normal      |
| always-flat           | still flat             | no   | (suppressed)|

We slide a detection window across the live (post-training) region. For each
window position we compute the same Welch signature and compare to baseline. A
template fires at most once per anomaly type (first detection wins → latency).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from .baseline import Baseline, TemplateSignature
from .bucket import BinnedSeries
from .config import Config
from .descriptors import AnomalyDescriptor
from .spectral import welch_spectrum, Spectrum


@dataclass
class _Hit:
    template_id: str
    anomaly_type: str
    bin_index: int
    observed_summary: dict


def _classify_window(sig: TemplateSignature, live: Spectrum,
                     cfg: Config) -> Optional[str]:
    """Return an anomaly type for this live window vs baseline, or None.

    Order matters: silence (energy gone) is checked before freq_shift/drift,
    and burst is checked last so a real periodic template merely getting busier
    is read as freq_shift, not burst (§ routing leak containment)."""
    dc = cfg.detect
    base_mean = sig.mean_count
    live_mean = live.mean_count

    # --- burst_onset: fires even for non-eligible templates (§5.2) ---
    # quiet/absent baseline + sudden dense run.
    if base_mean <= dc.burst_baseline_quiet_max:
        jump_factor = live_mean / base_mean if base_mean > 1e-9 else np.inf
        if (jump_factor >= dc.burst_factor
                and (live_mean - base_mean) >= dc.burst_min_abs_increase):
            return "burst_onset"

    if not sig.eligible:
        # Non-eligible templates never earned a tone → only burst can fire.
        return None

    # --- silence: an eligible template whose energy collapses ---
    if base_mean > 0 and live_mean < base_mean * dc.silence_rate_frac:
        return "silence"

    # --- freq_shift: dominant period moved beyond tolerance ---
    base_p = sig.dominant_period_s
    live_p = live.dominant_period_s
    if base_p and live_p and live.peak_prominence >= 0.05:
        rel = abs(live_p - base_p) / base_p
        if rel >= dc.freq_shift_period_tol:
            return "freq_shift"

    # --- drift: tone smeared — flatness rose while energy retained, period held
    if (live.flatness - sig.flatness) >= dc.drift_flatness_increase:
        # require the template still has meaningful energy (not silence) and we
        # did not already call it a freq_shift.
        if live_mean >= base_mean * dc.silence_rate_frac:
            return "drift"

    return None


def _scan_burst(series: np.ndarray, sig, cfg: Config, live_start: int,
                w: int) -> Optional["_Hit"]:
    """Vectorized burst-onset scan for a non-eligible template.

    Returns the first window (by hop) whose mean count jumps past the burst
    thresholds, or None. Equivalent to the per-window burst branch of
    ``_classify_window`` but computed with a cumsum rolling mean."""
    dc = cfg.detect
    base_mean = sig.mean_count
    if base_mean > dc.burst_baseline_quiet_max:
        return None  # not quiet enough to "burst from" (that would be freq_shift)
    n = len(series)
    if n - live_start < max(8, w):
        return None
    x = series.astype(np.float64)
    csum = np.concatenate(([0.0], np.cumsum(x)))
    starts = np.arange(live_start, n - w + 1, dc.detect_hop_bins)
    if starts.size == 0:
        return None
    window_sums = csum[starts + w] - csum[starts]
    means = window_sums / w
    base = base_mean if base_mean > 1e-9 else 0.0
    factor_ok = (means >= base * dc.burst_factor) if base > 0 else (means > 0)
    abs_ok = (means - base) >= dc.burst_min_abs_increase
    hits = np.flatnonzero(factor_ok & abs_ok)
    if hits.size == 0:
        return None
    start = int(starts[hits[0]])
    detect_bin = start + w - 1
    live_mean = float(means[hits[0]])
    return _Hit(sig.template_id, "burst_onset", detect_bin,
                {"dominant_period_s": None, "flatness": None,
                 "mean_count": round(live_mean, 4)})


def _window_summary(live: Spectrum) -> dict:
    return {
        "dominant_period_s": (round(live.dominant_period_s, 2)
                              if live.dominant_period_s else None),
        "flatness": round(live.flatness, 3),
        "mean_count": round(live.mean_count, 4),
    }


def detect(binned: BinnedSeries, baseline: Baseline, config: Config,
           context_provider=None) -> List[AnomalyDescriptor]:
    """Run the spectral detector over the live region; emit descriptors (§6.2).

    ``context_provider`` is an optional callable (template_id, epoch) -> list[str]
    of nearby raw log lines, used to fill surrounding_context.
    """
    cfg = config
    fs = 1.0 / cfg.bin_width_s
    dc = cfg.detect
    live_start = baseline.train_end_bin  # detect only on post-training region

    # First pass: collect first-hit per (template, anomaly_type).
    first_hit: Dict[tuple, _Hit] = {}
    # Track, per bin window, which templates fired — for cooccurring_flags.
    fires_by_window: Dict[int, List[str]] = {}

    w = dc.detect_window_bins
    for tid, series in binned.series.items():
        sig = baseline.signature(tid)
        if sig is None:
            continue
        n = len(series)

        if not sig.eligible:
            # A non-eligible template can ONLY fire burst_onset (it never earned
            # a tone to break). Burst is a pure windowed-mean test, so we do it
            # vectorized over the whole live region — no Python loop over the
            # (possibly millions of) window positions. This keeps the scan
            # bounded on pathologically sparse/wide series (e.g. a sample taken
            # across 214 days at an 8s bin width) and is behavior-identical to
            # the per-window burst check.
            hit = _scan_burst(series, sig, cfg, live_start, w)
            if hit is not None:
                first_hit[(tid, "burst_onset")] = hit
                fires_by_window.setdefault(hit.bin_index - w + 1, []).append(tid)
            continue

        # Eligible templates: full per-window spectral comparison. These are few
        # (only templates that established a tone), so the loop is cheap.
        for start in range(live_start, max(live_start + 1, n - w + 1),
                           dc.detect_hop_bins):
            window = series[start:start + w]
            if window.size < 8:
                continue
            live = welch_spectrum(
                window, fs=fs,
                nperseg_bins=min(cfg.baseline.welch_nperseg_bins, window.size),
                overlap_frac=cfg.baseline.welch_overlap_frac,
                window=cfg.baseline.welch_window,
                detrend=cfg.baseline.welch_detrend,
            )
            atype = _classify_window(sig, live, cfg)
            if atype is None:
                continue
            key = (tid, atype)
            # detection bin: the *end* of the window is when we could know.
            detect_bin = start + w - 1
            if key not in first_hit:
                first_hit[key] = _Hit(tid, atype, detect_bin,
                                      _window_summary(live))
                fires_by_window.setdefault(start, []).append(tid)

    # Second pass: build descriptors with cooccurring flags + context.
    descriptors: List[AnomalyDescriptor] = []
    for (tid, atype), hit in sorted(first_hit.items(),
                                    key=lambda kv: kv[1].bin_index):
        sig = baseline.signature(tid)
        epoch = binned.time_for_bin(hit.bin_index)
        # cooccurring = other templates that fired in a nearby window
        cooccur = _cooccurring(first_hit, tid, hit.bin_index, dc.detect_window_bins)
        context = []
        if context_provider is not None:
            context = context_provider(tid, epoch)
        descriptors.append(AnomalyDescriptor(
            template_id=tid,
            template_example=binned.templates.get(tid, ""),
            anomaly_type=atype,
            detected_at=epoch,
            detected_at_bin=hit.bin_index,
            detection_latency_bins=None,
            baseline_summary=sig.summary() if sig else {},
            observed_summary=hit.observed_summary,
            surrounding_context=context,
            cooccurring_flags=cooccur,
            source="spectral",
        ))
    return descriptors


def _cooccurring(first_hit: Dict[tuple, _Hit], tid: str, bin_index: int,
                 window_bins: int) -> List[str]:
    """Other templates whose first-hit is within one window of this one.

    This is what lets the LLM recognize the synchronized-deploy pattern (§5.1)
    without the detector having to decide it (§6.2)."""
    out = []
    for (other_tid, _), hit in first_hit.items():
        if other_tid == tid:
            continue
        if abs(hit.bin_index - bin_index) <= window_bins:
            if other_tid not in out:
                out.append(other_tid)
    return out
