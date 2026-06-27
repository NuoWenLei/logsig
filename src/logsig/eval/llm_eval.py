"""LLM-filter eval (§7.6), kept separate from the spectral eval.

Feeds the filter a MIX of a real injected anomaly (a silence — a genuine outage)
AND known-benign deltas (an injected synthetic deploy: a synchronized shift
across many templates, plus a benign one-time backfill burst). Measures whether
the filter ranks the real outage ABOVE the benign deltas.

This is deliberately apart from the spectral eval because the two layers fail for
different reasons and you need to know which is broken.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..config import Config
from ..llm_filter import UrgencyFilter, HeuristicRanker, Verdict
from ..pipeline import run_from_events, template_and_route
from ..ingest.bgl import parse_bgl_line
from ..ingest.synthetic import generate_synthetic_bgl, SyntheticConfig, default_specs
from ..route import route
from ..survey import survey_at_bin_width
from . import inject as inj


@dataclass
class LLMEvalResult:
    outage_template: str
    deploy_templates: List[str]
    backfill_template: Optional[str]
    ranked: List[Verdict]
    outage_rank: Optional[int]
    outage_above_benign: bool

    def to_dict(self) -> dict:
        return {
            "outage_template": self.outage_template,
            "deploy_templates": self.deploy_templates,
            "backfill_template": self.backfill_template,
            "outage_rank": self.outage_rank,
            "outage_above_benign": self.outage_above_benign,
            "ranked": [v.to_dict() for v in self.ranked],
        }


def run_llm_eval(config: Config, filt: Optional[UrgencyFilter] = None,
                 seed: int = 7, duration_s: float = 24 * 3600.0) -> LLMEvalResult:
    if filt is None:
        filt = HeuristicRanker()

    cfg = SyntheticConfig(seed=seed, duration_s=duration_s, specs=default_specs())
    lines = generate_synthetic_bgl(cfg)
    raws = [r for r in (parse_bgl_line(l) for l in lines) if r]
    base, _, _ = template_and_route(raws)

    spectral = route(base).spectral
    survey = survey_at_bin_width(spectral, config, config.bin_width_s)
    periodic = [r.template_id for r in survey.periodic]
    outage_tid = periodic[0] if periodic else None
    # quiet templates (always-flat, low rate): several for the synchronized
    # deploy, one held out for the lone benign backfill.
    quiet_ids = [r.template_id for r in survey.all_templates
                 if r.total_events > 0 and r.flatness > 0.6 and r.mean_count < 0.05]
    deploy_targets = quiet_ids[:4]
    quiet_tid = quiet_ids[4] if len(quiet_ids) > 4 else None

    ts = [e.timestamp for e in base]
    t0, t1 = min(ts), max(ts)
    inject_epoch = t0 + 0.60 * (t1 - t0)

    events = base
    win_s = config.detect.detect_window_bins * config.bin_width_s
    # 1) the REAL outage: silence a periodic template
    if outage_tid:
        events, _ = inj.inject_silence(events, outage_tid, inject_epoch)
    # 2) a benign DEPLOY: synchronized burst across many quiet templates
    if deploy_targets:
        events, _ = inj.inject_deploy(events, deploy_targets, inject_epoch + 5_000.0,
                                      burst_len_s=win_s, burst_count=200, seed=seed)
    # 3) a benign one-time BACKFILL burst on a separate quiet template
    if quiet_tid:
        events, _ = inj.inject_burst_onset(events, quiet_tid,
                                            inject_epoch + 9_000.0,
                                            burst_len_s=config.detect.detect_window_bins
                                            * config.bin_width_s,
                                            burst_count=200, seed=seed)

    result = run_from_events(events, config)
    ranked = filt.rank(result.all_anomalies)

    # find the outage's rank and whether it is above all benign deltas
    outage_rank = None
    for i, v in enumerate(ranked):
        if v.template_id == outage_tid and v.anomaly_type == "silence":
            outage_rank = i
            break

    benign_ranks = [i for i, v in enumerate(ranked)
                    if (v.template_id in deploy_targets
                        or v.template_id == quiet_tid)
                    and (outage_tid is None or v.template_id != outage_tid)]
    above = (outage_rank is not None and
             (not benign_ranks or outage_rank < min(benign_ranks)))

    return LLMEvalResult(
        outage_template=outage_tid or "",
        deploy_templates=deploy_targets,
        backfill_template=quiet_tid,
        ranked=ranked,
        outage_rank=outage_rank,
        outage_above_benign=above,
    )


def format_llm_eval(res: LLMEvalResult) -> str:
    lines = []
    lines.append("=== LLM urgency-filter eval ===")
    lines.append(f"real outage (silence) template: {res.outage_template}")
    lines.append(f"benign deploy templates: {res.deploy_templates}")
    lines.append(f"benign backfill template: {res.backfill_template}")
    lines.append(f"outage rank: {res.outage_rank}   "
                 f"outage_above_benign: "
                 f"{'PASS ✓' if res.outage_above_benign else 'FAIL ✗'}")
    lines.append("")
    lines.append("ranked shortlist (most urgent first):")
    lines.append(f"  {'#':>2} {'triage':<7} {'urg':>5} {'type':<13} "
                 f"{'template':<8}  rationale")
    for i, v in enumerate(res.ranked):
        lines.append(f"  {i:>2} {v.triage:<7} {v.urgency:>5.2f} "
                     f"{v.anomaly_type:<13} {v.template_id:<8}  {v.rationale}")
    return "\n".join(lines)
