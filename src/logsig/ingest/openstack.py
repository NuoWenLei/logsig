"""Parser for the Loghub OpenStack log format.

OpenStack line layout (whitespace-delimited, Content is the remainder)::

    <logfile> <date> <time> <pid> <level> <component> <message...>
    nova-api.log.1.2017-05-16_13:53:08 2017-05-16 00:00:00.008 25746 INFO \\
        nova.osapi_compute.wsgi.server [req-...] "GET /v2/... HTTP/1.1" status: 200

Unlike BGL there is no alert tag, so routing (§2.2) falls back to the actual
log **level**: ERROR/CRITICAL → trivial path, everything else → spectral path.

Why this corpus: the Stage-0 survey shows OpenStack contains *genuine* operational
cadences (e.g. ``nova.compute.resource_tracker`` running its periodic task on a
clean ~60s tone) — the health-check / polling texture BGL lacks. See
docs/stage0_findings.md.
"""

from __future__ import annotations

import glob
import os
from datetime import datetime, timezone
from typing import Iterator, List, Optional

from .bgl import RawLine

# Levels that route to the trivial/alert path.
_ALERT_LEVELS = {"ERROR", "CRITICAL"}
_N_FIELDS = 6  # logfile, date, time, pid, level, component (then message)


def parse_openstack_line(line: str) -> Optional[RawLine]:
    """Parse one OpenStack line. Returns None for blank/malformed lines."""
    line = line.rstrip("\n")
    if not line.strip():
        return None
    parts = line.split(maxsplit=_N_FIELDS)
    if len(parts) < _N_FIELDS:
        return None
    date, tm, _pid, level, component = parts[1], parts[2], parts[3], parts[4], parts[5]
    try:
        dt = datetime.strptime(f"{date} {tm}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None
    timestamp = dt.replace(tzinfo=timezone.utc).timestamp()
    message = parts[_N_FIELDS] if len(parts) > _N_FIELDS else ""
    # Keep the component on the content so distinct services stay distinct
    # templates (over-decompose, §3.1); the [req-...] ids get wildcarded by Drain.
    content = f"{component} {message}"
    is_alert = level.upper() in _ALERT_LEVELS
    return RawLine(
        timestamp=timestamp,
        level=level,
        is_alert=is_alert,
        content=content,
        alert_label=level if is_alert else "-",
    )


def _resolve_paths(path: str) -> List[str]:
    """Accept a single file or a directory of openstack_normal*.log files."""
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "openstack_normal*.log")))
        return files or sorted(glob.glob(os.path.join(path, "*.log")))
    return [path]


def iter_openstack(path: str) -> Iterator[RawLine]:
    """Stream RawLines from an OpenStack log file or directory."""
    for p in _resolve_paths(path):
        with open(p, "r", errors="replace") as f:
            for line in f:
                parsed = parse_openstack_line(line)
                if parsed is not None:
                    yield parsed
