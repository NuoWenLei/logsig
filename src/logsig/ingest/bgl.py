"""Parser for the Loghub BGL log format.

BGL line layout (whitespace-delimited, Content is the free-text remainder)::

    Label Timestamp Date Node Time NodeRepeat Type Component Level Content
    -     1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.675872 \\
          R02-M1-N0-C:J12-U11 RAS KERNEL INFO instruction cache parity error corrected

Routing (§2.2): the first column is the alert tag. ``-`` means non-alert
(→ spectral path); anything else is an alert label (→ trivial path). This is a
gift — severity is pre-annotated, we do not infer it from message text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class RawLine:
    timestamp: float
    level: str
    is_alert: bool
    content: str
    alert_label: str  # "-" for non-alert, else the BGL alert category


# Index of each fixed field before Content. There are 9 leading fields.
_N_FIELDS = 9


def parse_bgl_line(line: str) -> Optional[RawLine]:
    """Parse one BGL line. Returns None for blank/malformed lines."""
    line = line.rstrip("\n")
    if not line.strip():
        return None
    parts = line.split(maxsplit=_N_FIELDS)
    if len(parts) < _N_FIELDS:
        return None
    label = parts[0]
    ts_raw = parts[1]
    level = parts[8]
    content = parts[9] if len(parts) > _N_FIELDS else ""
    try:
        timestamp = float(ts_raw)
    except ValueError:
        return None
    is_alert = label != "-"
    return RawLine(
        timestamp=timestamp,
        level=level,
        is_alert=is_alert,
        content=content,
        alert_label=label,
    )


def iter_bgl(path: str) -> Iterator[RawLine]:
    """Stream RawLines from a BGL log file (skips malformed lines)."""
    with open(path, "r", errors="replace") as f:
        for line in f:
            parsed = parse_bgl_line(line)
            if parsed is not None:
                yield parsed
