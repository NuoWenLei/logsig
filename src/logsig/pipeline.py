"""End-to-end orchestration: ingest → template → route → bucket → baseline.

Keeps the stage wiring in one place so the CLI, survey, and eval harness share a
single code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .baseline import Baseline, make_baseline_strategy
from .bucket import BinnedSeries, bucketize
from .config import Config
from .descriptors import AnomalyDescriptor
from .detector import detect
from .events import Event
from .ingest.bgl import iter_bgl
from .route import RoutedEvents, route
from .template import Templater
from .trivial import detect_trivial


@dataclass
class PipelineResult:
    events: List[Event]
    routed: RoutedEvents
    binned: BinnedSeries          # spectral-path series
    baseline: Baseline
    spectral_anomalies: List[AnomalyDescriptor]
    trivial_anomalies: List[AnomalyDescriptor]

    @property
    def all_anomalies(self) -> List[AnomalyDescriptor]:
        """Union of [E] and [5] (§6.1)."""
        return self.spectral_anomalies + self.trivial_anomalies


def template_and_route(raws) -> Tuple[List[Event], RoutedEvents, Templater]:
    templater = Templater()
    events = list(templater.template(raws))
    return events, route(events), templater


def build_context_provider(events: List[Event], window_s: float = 30.0):
    """Return a callable (template_id, epoch) -> nearby raw log lines."""
    ev_sorted = sorted(events, key=lambda e: e.timestamp)
    times = np.array([e.timestamp for e in ev_sorted])

    def provider(template_id: str, epoch: float, max_lines: int = 5) -> List[str]:
        lo = np.searchsorted(times, epoch - window_s)
        hi = np.searchsorted(times, epoch + window_s)
        nearby = ev_sorted[lo:hi]
        return [f"[{e.level}] {e.raw}" for e in nearby[:max_lines]]

    return provider


def run_from_events(events: List[Event], config: Config) -> PipelineResult:
    routed = route(events)
    binned = bucketize(routed.spectral, config.bin_width_s)
    strategy = make_baseline_strategy(config)
    baseline = strategy.fit(binned)
    ctx = build_context_provider(events)
    spectral = detect(binned, baseline, config, context_provider=ctx)
    trivial = detect_trivial(routed.trivial, config)
    return PipelineResult(
        events=events,
        routed=routed,
        binned=binned,
        baseline=baseline,
        spectral_anomalies=spectral,
        trivial_anomalies=trivial,
    )


def run_from_bgl_file(path: str, config: Config) -> PipelineResult:
    templater = Templater()
    events = list(templater.template(iter_bgl(path)))
    return run_from_events(events, config)
