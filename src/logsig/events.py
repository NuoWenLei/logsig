"""The core event record that flows through the pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    """One templated log line.

    Fields are exactly the stage-[1] contract from the spec (§2.1):
    ``(timestamp, template_id, level, raw)`` plus the routing bit.
    """

    timestamp: float          # unix epoch seconds
    template_id: str          # Drain3 cluster id, e.g. "T47"
    level: str                # INFO / WARNING / ERROR / FATAL / ...
    raw: str                  # original message content (post-fields)
    is_alert: bool            # §2.2 router: True => trivial path, False => spectral
    template: str = ""        # the template string (for descriptors §6.2)
