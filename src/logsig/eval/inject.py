"""The four injection transforms + a synthetic deploy (§7.4, §7.6).

Each injection is a timestamped transform on ONE template's event series, with
the injection window recorded as ground truth. Operating at the Event level (not
the raw-line level) means these run identically on synthetic logs and on a real
downloaded BGL.

Honesty caveat (§7.7, also in README): injecting into aggregate real logs
assumes the perturbed template is independent of the others, which isn't fully
true. For v0 this is a fine simplification — we test "can the detector see the
structural break", not cross-stream causality.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import List, Optional

from ..events import Event


@dataclass
class Injection:
    """Ground truth for one injected anomaly."""

    template_id: str
    anomaly_type: str         # silence | drift | freq_shift | burst_onset | deploy
    inject_epoch: float       # when the perturbation begins
    detail: dict              # transform-specific params + label (urgent?)


def _target_events(events: List[Event], template_id: str) -> List[Event]:
    return sorted([e for e in events if e.template_id == template_id],
                  key=lambda e: e.timestamp)


def _span(events: List[Event]) -> tuple:
    ts = [e.timestamp for e in events]
    return min(ts), max(ts)


def inject_silence(events: List[Event], template_id: str,
                   inject_epoch: float) -> tuple:
    """Delete a periodic template's events after ``inject_epoch`` (headline
    flatline case)."""
    out = [e for e in events
           if not (e.template_id == template_id and e.timestamp >= inject_epoch)]
    gt = Injection(template_id, "silence", inject_epoch,
                   {"urgent": True, "label": "outage: periodic template went silent"})
    return out, gt


def inject_drift(events: List[Event], template_id: str, inject_epoch: float,
                 max_jitter_s: float, seed: int = 0) -> tuple:
    """Progressively jitter inter-arrival times after ``inject_epoch`` (rhythm
    smear). Jitter ramps from 0 to ``max_jitter_s`` over the remaining span."""
    rng = random.Random(seed)
    _, t_end = _span(events)
    horizon = max(t_end - inject_epoch, 1.0)
    out: List[Event] = []
    for e in events:
        if e.template_id == template_id and e.timestamp >= inject_epoch:
            frac = (e.timestamp - inject_epoch) / horizon
            jit = rng.gauss(0.0, max_jitter_s * frac)
            out.append(replace(e, timestamp=e.timestamp + jit))
        else:
            out.append(e)
    gt = Injection(template_id, "drift", inject_epoch,
                   {"urgent": True, "max_jitter_s": max_jitter_s,
                    "label": "degradation: polling rhythm smearing"})
    return out, gt


def inject_freq_shift(events: List[Event], template_id: str, inject_epoch: float,
                      base_period_s: float, factor: float, seed: int = 0) -> tuple:
    """Resample a periodic template to a higher base rate after ``inject_epoch``
    (factor>1 ⇒ fires factor× more often). Tests amplitude-at-dominant-freq."""
    rng = random.Random(seed)
    targ = _target_events(events, template_id)
    if not targ:
        return list(events), Injection(template_id, "freq_shift", inject_epoch, {})
    proto = targ[0]
    _, t_end = _span(events)
    kept = [e for e in events
            if not (e.template_id == template_id and e.timestamp >= inject_epoch)]
    new_period = base_period_s / factor
    new_events: List[Event] = []
    t = inject_epoch + new_period
    while t < t_end:
        jit = rng.gauss(0.0, new_period * 0.05)
        new_events.append(replace(proto, timestamp=t + jit))
        t += new_period
    out = kept + new_events
    gt = Injection(template_id, "freq_shift", inject_epoch,
                   {"urgent": True, "factor": factor,
                    "label": f"retry firing {factor}x more often"})
    return out, gt


def inject_burst_onset(events: List[Event], template_id: str, inject_epoch: float,
                       burst_len_s: float, burst_count: int, seed: int = 0) -> tuple:
    """Inject a sudden dense run into a quiet/absent template (backfill / periodic
    error onset)."""
    rng = random.Random(seed)
    targ = _target_events(events, template_id)
    proto = targ[0] if targ else None
    if proto is None:
        return list(events), Injection(template_id, "burst_onset", inject_epoch, {})
    out = list(events)
    for _ in range(burst_count):
        t = inject_epoch + rng.uniform(0.0, burst_len_s)
        out.append(replace(proto, timestamp=t))
    gt = Injection(template_id, "burst_onset", inject_epoch,
                   {"urgent": False, "burst_count": burst_count,
                    "label": "one-time backfill (benign-ish)"})
    return out, gt


def inject_deploy(events: List[Event], template_ids: List[str],
                  inject_epoch: float, burst_len_s: float = 2000.0,
                  burst_count: int = 200, seed: int = 0) -> tuple:
    """Synthetic *deploy*: a SYNCHRONIZED dense run across MANY templates inside
    the same window (§5.1, §7.6). Because all the templates light up at the same
    instant, each anomaly's ``cooccurring_flags`` lists the others — the signal
    the LLM filter uses to recognize a deploy/scale event and de-prioritize it.
    Labeled benign — the filter must rank a real outage above this."""
    rng = random.Random(seed)
    out: List[Event] = []
    protos = {}
    for e in events:
        protos.setdefault(e.template_id, e)
        out.append(e)
    for tid in template_ids:
        proto = protos.get(tid)
        if proto is None:
            continue
        for _ in range(burst_count):
            t = inject_epoch + rng.uniform(0.0, burst_len_s)
            out.append(replace(proto, timestamp=t))
    gt = Injection("|".join(template_ids), "deploy", inject_epoch,
                   {"urgent": False, "n_templates": len(template_ids),
                    "label": "deploy: synchronized shift across many templates"})
    return out, gt
