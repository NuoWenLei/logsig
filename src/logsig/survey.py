"""Stage-0 periodicity survey (§7.3).

The entire spectral premise rests on the logs actually containing clean tones to
break. Verify, don't assume. This:
  1. templates the logs (already done upstream),
  2. builds per-template count series at a few candidate bin widths,
  3. computes spectral flatness per template,
  4. reports the flatness distribution + the periodic (low-flatness) candidates,
  5. derives a default bin width from the fastest periodic template (§3.2).

Gate: confirm a healthy low-flatness population exists before proceeding.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import numpy as np

from .bucket import bucketize
from .config import Config
from .events import Event
from .spectral import welch_spectrum


@dataclass
class TemplateSurvey:
    template_id: str
    template_example: str
    level: str
    mean_count: float
    total_events: int
    flatness: float
    dominant_period_s: Optional[float]
    peak_prominence: float


@dataclass
class SurveyResult:
    bin_width_s: float
    n_templates: int
    n_periodic: int
    flatness_threshold: float
    periodic: List[TemplateSurvey]
    all_templates: List[TemplateSurvey]
    suggested_bin_width_s: Optional[float]
    healthy: bool   # gate verdict

    def histogram(self, nbins: int = 10) -> List[tuple]:
        vals = np.array([t.flatness for t in self.all_templates])
        if vals.size == 0:
            return []
        counts, edges = np.histogram(vals, bins=nbins, range=(0.0, 1.0))
        return [(round(edges[i], 2), round(edges[i + 1], 2), int(counts[i]))
                for i in range(len(counts))]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["flatness_histogram"] = self.histogram()
        return d


def survey_at_bin_width(events: List[Event], config: Config,
                        bin_width_s: float) -> SurveyResult:
    """Run the survey at one candidate bin width over the spectral-path events."""
    binned = bucketize(events, bin_width_s)
    fs = 1.0 / bin_width_s
    flat_thr = config.eligibility.flatness_threshold
    prom_thr = config.eligibility.min_peak_prominence
    min_mean = config.eligibility.min_baseline_mean_count

    rows: List[TemplateSurvey] = []
    for tid, series in binned.series.items():
        spec = welch_spectrum(
            series, fs=fs,
            nperseg_bins=config.baseline.welch_nperseg_bins,
            overlap_frac=config.baseline.welch_overlap_frac,
            window=config.baseline.welch_window,
            detrend=config.baseline.welch_detrend,
        )
        rows.append(TemplateSurvey(
            template_id=tid,
            template_example=binned.templates.get(tid, ""),
            level=binned.levels.get(tid, ""),
            mean_count=round(spec.mean_count, 5),
            total_events=int(series.sum()),
            flatness=round(spec.flatness, 4),
            dominant_period_s=(round(spec.dominant_period_s, 2)
                               if spec.dominant_period_s else None),
            peak_prominence=round(spec.peak_prominence, 4),
        ))

    periodic = [r for r in rows
                if r.flatness < flat_thr and r.peak_prominence >= prom_thr
                and r.mean_count >= min_mean and r.dominant_period_s]
    periodic.sort(key=lambda r: r.flatness)

    # Suggested bin width: oversample the fastest periodic template at ~T/4.
    suggested = None
    if periodic:
        fastest = min(r.dominant_period_s for r in periodic)
        suggested = round(fastest / 4.0, 2)

    healthy = len(periodic) >= 1
    rows.sort(key=lambda r: r.flatness)
    return SurveyResult(
        bin_width_s=bin_width_s,
        n_templates=len(rows),
        n_periodic=len(periodic),
        flatness_threshold=flat_thr,
        periodic=periodic,
        all_templates=rows,
        suggested_bin_width_s=suggested,
        healthy=healthy,
    )


def format_survey(result: SurveyResult, max_rows: int = 15) -> str:
    """Pretty text report for the findings note / CLI."""
    lines = []
    lines.append(f"=== Periodicity survey @ bin_width = {result.bin_width_s}s ===")
    lines.append(f"templates: {result.n_templates}   "
                 f"periodic (flatness < {result.flatness_threshold}): "
                 f"{result.n_periodic}")
    lines.append(f"GATE: {'HEALTHY ✓' if result.healthy else 'FLAT ✗ (no tones!)'}")
    if result.suggested_bin_width_s:
        lines.append(f"suggested default bin width (~T/4 of fastest tone): "
                     f"{result.suggested_bin_width_s}s")
    lines.append("")
    lines.append("flatness distribution (0=clean tone, 1=white noise):")
    for lo, hi, c in result.histogram():
        bar = "#" * c
        lines.append(f"  [{lo:.2f},{hi:.2f})  {c:4d} {bar}")
    lines.append("")
    lines.append("periodic candidates (low flatness):")
    lines.append(f"  {'template_id':<10} {'period_s':>9} {'flatness':>9} "
                 f"{'prom':>6} {'mean':>8}  example")
    for r in result.periodic[:max_rows]:
        ex = (r.template_example[:48] + "…") if len(r.template_example) > 49 \
            else r.template_example
        lines.append(f"  {r.template_id:<10} {str(r.dominant_period_s):>9} "
                     f"{r.flatness:>9.3f} {r.peak_prominence:>6.2f} "
                     f"{r.mean_count:>8.3f}  {ex}")
    return "\n".join(lines)
