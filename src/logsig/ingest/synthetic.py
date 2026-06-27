"""Synthetic BGL-format log generator with *known* spectral structure.

Why this exists: the spectral premise needs a long, multi-cycle span to baseline
(the real BGL is 214 days / 4.7 M lines). When the full Zenodo download is not
available, this generates logs in the exact BGL line format with a controlled
population of periodic tones, aperiodic broadband streams, and quiet templates —
so the Stage-0 survey finds a healthy low-flatness population *by construction*
and the eval harness has real tones to break.

Honesty caveat (mirrored in the README, §7.7): tones generated here are cleaner
than reality. The synthetic path is the deterministic CI/demo path; the same
pipeline runs on a real downloaded BGL.log for the honest evaluation.

Each template is emitted as a BGL line so the whole ingest+template+route path is
exercised end to end.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TemplateSpec:
    """Ground-truth description of one synthetic template's behavior."""

    name: str                       # short id used in the message text
    kind: str                       # "periodic" | "aperiodic" | "quiet" | "alert"
    level: str = "INFO"
    is_alert: bool = False
    period_s: Optional[float] = None  # for periodic: inter-arrival period
    jitter_s: float = 0.0             # gaussian jitter on arrival times
    rate_per_s: Optional[float] = None  # for aperiodic: poisson rate
    node: str = "R01-M0-N0-C:J00-U00"
    message: str = "event"

    def line_content(self) -> str:
        return f"{self.message} node={self.node} tmpl={self.name}"


@dataclass
class SyntheticConfig:
    duration_s: float = 24 * 3600.0   # one day of logs by default
    seed: int = 1234
    start_epoch: float = 1_117_800_000.0
    specs: List[TemplateSpec] = field(default_factory=list)


def default_specs() -> List[TemplateSpec]:
    """A representative population: clean tones at several periods, broadband
    aperiodic streams, quiet/absent templates, and a couple of alert templates.

    Periods are chosen so the default 8s bin width oversamples the fastest tone
    (30s) at ~T/4 (§3.2)."""
    return [
        # --- clean periodic tones (low flatness; the eval's injection targets) ---
        TemplateSpec("health_30s", "periodic", period_s=30.0, jitter_s=0.5,
                     message="health check ok"),
        TemplateSpec("poll_60s", "periodic", period_s=60.0, jitter_s=1.0,
                     message="poll upstream status"),
        TemplateSpec("heartbeat_120s", "periodic", period_s=120.0, jitter_s=2.0,
                     message="heartbeat node alive"),
        TemplateSpec("cron_300s", "periodic", period_s=300.0, jitter_s=3.0,
                     message="flush metrics buffer"),
        # --- aperiodic broadband (always-flat; must self-select out, §4.3) ---
        TemplateSpec("request_log", "aperiodic", rate_per_s=0.20,
                     message="handled request id=<*>"),
        TemplateSpec("user_action", "aperiodic", rate_per_s=0.05,
                     message="user clicked widget"),
        # --- quiet / mostly absent (only burst-onset may fire, §5.2) ---
        # Several quiet streams so a synthetic deploy can light up many of them
        # synchronously (the co-occurring "cliff" the LLM filter de-prioritizes).
        TemplateSpec("backfill_job", "quiet", rate_per_s=0.002,
                     message="backfill batch processed"),
        TemplateSpec("rare_warn", "quiet", level="WARNING", rate_per_s=0.001,
                     message="cache miss storm averted"),
        # Distinct wording so Drain keeps them as separate streams (it would
        # wildcard a single varying token and collapse them otherwise).
        TemplateSpec("quiet_svc_a", "quiet", rate_per_s=0.0015,
                     message="indexer flushed segment to disk"),
        TemplateSpec("quiet_svc_b", "quiet", rate_per_s=0.0015,
                     message="scheduler released stale lease"),
        TemplateSpec("quiet_svc_c", "quiet", rate_per_s=0.0015,
                     message="gc reclaimed tenured objects"),
        TemplateSpec("quiet_svc_d", "quiet", rate_per_s=0.0012,
                     message="replica caught up to leader"),
        TemplateSpec("quiet_svc_e", "quiet", rate_per_s=0.0012,
                     message="snapshot uploaded to object store"),
        TemplateSpec("quiet_svc_f", "quiet", rate_per_s=0.0012,
                     message="ticket queue compacted"),
        # --- alert-tagged templates (→ trivial path, §2.2) ---
        TemplateSpec("kernel_panic", "alert", level="FATAL", is_alert=True,
                     rate_per_s=0.0008, message="kernel panic on cpu<*>"),
    ]


def _periodic_times(spec: TemplateSpec, cfg: SyntheticConfig,
                    rng: random.Random) -> List[float]:
    times = []
    t = rng.uniform(0, spec.period_s)
    while t < cfg.duration_s:
        jit = rng.gauss(0.0, spec.jitter_s) if spec.jitter_s else 0.0
        ts = t + jit
        if 0 <= ts < cfg.duration_s:
            times.append(ts)
        t += spec.period_s
    return times


def _poisson_times(rate_per_s: float, cfg: SyntheticConfig,
                   rng: random.Random) -> List[float]:
    times = []
    t = 0.0
    if rate_per_s <= 0:
        return times
    while t < cfg.duration_s:
        # exponential inter-arrival
        t += rng.expovariate(rate_per_s)
        if t < cfg.duration_s:
            times.append(t)
    return times


def _bgl_line(spec: TemplateSpec, epoch_ts: float) -> str:
    """Render one event as a BGL-format line."""
    label = "KERNPANIC" if spec.is_alert else "-"
    # Date + time fields are cosmetic for our parser (it reads col 1 epoch and
    # col 8 level); fill them plausibly.
    import time as _time

    lt = _time.gmtime(epoch_ts)
    date = _time.strftime("%Y.%m.%d", lt)
    dt = _time.strftime("%Y-%m-%d-%H.%M.%S", lt) + f".{int((epoch_ts%1)*1e6):06d}"
    return (
        f"{label} {int(epoch_ts)} {date} {spec.node} {dt} {spec.node} "
        f"RAS KERNEL {spec.level} {spec.line_content()}"
    )


def generate_synthetic_bgl(cfg: Optional[SyntheticConfig] = None) -> List[str]:
    """Generate a list of BGL-format log lines, sorted by timestamp."""
    if cfg is None:
        cfg = SyntheticConfig(specs=default_specs())
    if not cfg.specs:
        cfg.specs = default_specs()
    rng = random.Random(cfg.seed)

    events = []  # (relative_time_s, spec)
    for spec in cfg.specs:
        if spec.kind == "periodic":
            ts = _periodic_times(spec, cfg, rng)
        else:  # aperiodic / quiet / alert -> poisson
            ts = _poisson_times(spec.rate_per_s or 0.0, cfg, rng)
        for t in ts:
            events.append((t, spec))

    events.sort(key=lambda e: e[0])
    return [_bgl_line(spec, cfg.start_epoch + t) for t, spec in events]


def write_synthetic_bgl(path: str, cfg: Optional[SyntheticConfig] = None) -> int:
    """Write synthetic logs to ``path``. Returns line count."""
    lines = generate_synthetic_bgl(cfg)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)
