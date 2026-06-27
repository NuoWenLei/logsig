"""Level/alert-based routing (stage 2, §2.2).

Rule: error-level (alert-tagged) templates → trivial path [E]; non-error
templates → spectral path [3-5]. For BGL the alert tag (first column) *is* the
router (``Event.is_alert``), so we do not infer severity from text.

Two documented v0 leaks (see README "Known limitations"):
  - periodic errors are sent to the count path (we lose their cadence);
  - aperiodic non-errors stay in the spectral path (handled by the eligibility
    gate + delta-only firing, NOT by an upfront classifier).
We do NOT try to fix these in v0 (§10).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from .events import Event


@dataclass
class RoutedEvents:
    spectral: List[Event]  # non-alert events → spectral path
    trivial: List[Event]   # alert events → trivial/novelty path

    @property
    def counts(self) -> dict:
        return {"spectral": len(self.spectral), "trivial": len(self.trivial)}


def route(events: Iterable[Event]) -> RoutedEvents:
    spectral: List[Event] = []
    trivial: List[Event] = []
    for ev in events:
        if ev.is_alert:
            trivial.append(ev)
        else:
            spectral.append(ev)
    return RoutedEvents(spectral=spectral, trivial=trivial)
