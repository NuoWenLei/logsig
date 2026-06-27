"""The detector → LLM descriptor contract (§6.2).

A spectral anomaly handed up is *nameable and structured* ("template #47 lost
its 30s periodicity at 14:32"), never a raw distance. This interpretability is
what makes the LLM's urgency call reliable instead of hallucinated.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Allowed anomaly types (the union the LLM filter consumes, §6.1).
ANOMALY_TYPES = {
    "silence", "drift", "freq_shift", "burst_onset",   # spectral path [5]
    "novel_template", "count_spike",                    # trivial path [E]
}


@dataclass
class AnomalyDescriptor:
    template_id: str
    template_example: str
    anomaly_type: str
    detected_at: float                     # epoch timestamp of detection
    detected_at_bin: int                   # bin index of detection
    detection_latency_bins: Optional[int]  # filled by the eval harness vs truth
    baseline_summary: Dict[str, Any] = field(default_factory=dict)
    observed_summary: Dict[str, Any] = field(default_factory=dict)
    surrounding_context: List[str] = field(default_factory=list)
    cooccurring_flags: List[str] = field(default_factory=list)
    source: str = "spectral"               # "spectral" | "trivial"

    def __post_init__(self):
        if self.anomaly_type not in ANOMALY_TYPES:
            raise ValueError(f"unknown anomaly_type {self.anomaly_type!r}")

    def to_dict(self) -> dict:
        return asdict(self)
