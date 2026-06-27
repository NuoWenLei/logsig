"""Spectral detector eval harness (§7.4-7.6).

Builds a baseline corpus, injects each of the four anomaly transforms into
genuinely-periodic / quiet templates (chosen by the Stage-0 survey so we break
real tones, not sines we drew), runs the detector, and scores:
  - did the detector fire?  (precision/recall over injections)
  - detection latency       (bins between true injection point and the flag;
                             reported as a distribution, not just a mean — §7.5)
  - right template?         (fired on the perturbed template, not a bystander)

Deterministic and LLM-free, so it runs in CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import numpy as np

from ..config import Config
from ..events import Event
from ..ingest.bgl import iter_bgl, parse_bgl_line
from ..ingest.synthetic import generate_synthetic_bgl, SyntheticConfig, default_specs
from ..pipeline import run_from_events, template_and_route
from ..survey import survey_at_bin_width
from ..template import Templater
from . import inject as inj


# Which detector anomaly_type(s) count as a correct catch for each injection.
_ACCEPT = {
    "silence": {"silence", "drift"},        # a smeared-then-gone tone may read as drift first
    "drift": {"drift", "freq_shift"},       # smearing can look like a mild freq move
    "freq_shift": {"freq_shift", "drift"},
    "burst_onset": {"burst_onset"},
}


@dataclass
class InjectionResult:
    anomaly_type: str
    target_template: str
    inject_bin: int
    fired: bool
    fired_type: Optional[str]
    detected_bin: Optional[int]
    latency_bins: Optional[int]
    right_template: bool
    n_bystander_fires: int


@dataclass
class TypeSummary:
    anomaly_type: str
    n: int
    recall: float
    right_template_rate: float
    precision: float
    latencies: List[int] = field(default_factory=list)

    def latency_stats(self) -> dict:
        if not self.latencies:
            return {"min": None, "p50": None, "p90": None, "max": None,
                    "mean": None}
        a = np.array(self.latencies)
        return {
            "min": int(a.min()),
            "p50": int(np.percentile(a, 50)),
            "p90": int(np.percentile(a, 90)),
            "max": int(a.max()),
            "mean": round(float(a.mean()), 2),
        }


@dataclass
class EvalReport:
    bin_width_s: float
    n_seeds: int
    per_type: Dict[str, TypeSummary]
    results: List[InjectionResult]
    control_false_positives: int  # spectral flags fired with NO injection

    def to_dict(self) -> dict:
        return {
            "bin_width_s": self.bin_width_s,
            "n_seeds": self.n_seeds,
            "control_false_positives": self.control_false_positives,
            "per_type": {
                k: {**{f: getattr(v, f) for f in
                       ("anomaly_type", "n", "recall", "right_template_rate",
                        "precision")},
                    "latency_bins": v.latency_stats()}
                for k, v in self.per_type.items()
            },
        }


def _base_events(config: Config, seed: int, duration_s: float) -> List[Event]:
    cfg = SyntheticConfig(seed=seed, duration_s=duration_s, specs=default_specs())
    lines = generate_synthetic_bgl(cfg)
    raws = [parse_bgl_line(l) for l in lines]
    raws = [r for r in raws if r]
    events, _, _ = template_and_route(raws)
    return events


def _pick_targets(events: List[Event], config: Config):
    """Return (periodic_target_tid, period_s, quiet_target_tid) from the survey."""
    from ..route import route
    spectral = route(events).spectral
    survey = survey_at_bin_width(spectral, config, config.bin_width_s)
    periodic_tid = None
    period_s = None
    if survey.periodic:
        top = survey.periodic[0]
        periodic_tid = top.template_id
        period_s = top.dominant_period_s
    # quiet target: lowest-mean template that still has at least a few events
    quiet_tid = None
    quiet_mean = 1e9
    for r in survey.all_templates:
        if 0 < r.total_events and r.mean_count < quiet_mean and r.flatness > 0.6:
            quiet_mean = r.mean_count
            quiet_tid = r.template_id
    return periodic_tid, period_s, quiet_tid


def _bin_of(events: List[Event], epoch: float, config: Config) -> int:
    from ..route import route
    from ..bucket import bucketize
    binned = bucketize(route(events).spectral, config.bin_width_s)
    return binned.bin_index_for_time(epoch)


def _evaluate_one(base_events: List[Event], gt: inj.Injection,
                  config: Config) -> InjectionResult:
    result = run_from_events(base_events, config)
    inject_bin = result.binned.bin_index_for_time(gt.inject_epoch)
    accept = _ACCEPT.get(gt.anomaly_type, {gt.anomaly_type})

    fired = False
    fired_type = None
    detected_bin = None
    bystanders = 0
    for d in result.spectral_anomalies:
        on_target = d.template_id == gt.template_id
        after = d.detected_at >= gt.inject_epoch - 1e-6
        if on_target and after and d.anomaly_type in accept:
            if not fired or d.detected_at_bin < detected_bin:
                fired = True
                fired_type = d.anomaly_type
                detected_bin = d.detected_at_bin
        elif not on_target and after:
            bystanders += 1

    latency = (detected_bin - inject_bin) if fired else None
    return InjectionResult(
        anomaly_type=gt.anomaly_type,
        target_template=gt.template_id,
        inject_bin=inject_bin,
        fired=fired,
        fired_type=fired_type,
        detected_bin=detected_bin,
        latency_bins=latency,
        right_template=fired,
        n_bystander_fires=bystanders,
    )


def run_spectral_eval(config: Config, n_seeds: int = 3,
                      duration_s: float = 24 * 3600.0) -> EvalReport:
    """Run all four injections across ``n_seeds`` synthetic corpora."""
    results: List[InjectionResult] = []
    control_fp = 0

    for seed in range(n_seeds):
        base = _base_events(config, seed=1000 + seed, duration_s=duration_s)
        periodic_tid, period_s, quiet_tid = _pick_targets(base, config)

        # control run (no injection): count any spectral flag as a false positive
        control = run_from_events(base, config)
        control_fp += len(control.spectral_anomalies)

        # injection epoch: 60% into the span (well inside the live region)
        ts = [e.timestamp for e in base]
        t0, t1 = min(ts), max(ts)
        inject_epoch = t0 + 0.60 * (t1 - t0)

        if periodic_tid:
            ev, gt = inj.inject_silence(base, periodic_tid, inject_epoch)
            results.append(_evaluate_one(ev, gt, config))

            ev, gt = inj.inject_drift(base, periodic_tid, inject_epoch,
                                      max_jitter_s=(period_s or 30.0) * 1.0,
                                      seed=seed)
            results.append(_evaluate_one(ev, gt, config))

            ev, gt = inj.inject_freq_shift(base, periodic_tid, inject_epoch,
                                           base_period_s=period_s or 30.0,
                                           factor=4.0, seed=seed)
            results.append(_evaluate_one(ev, gt, config))

        if quiet_tid:
            ev, gt = inj.inject_burst_onset(base, quiet_tid, inject_epoch,
                                            burst_len_s=config.detect.detect_window_bins
                                            * config.bin_width_s,
                                            burst_count=200, seed=seed)
            results.append(_evaluate_one(ev, gt, config))

    per_type = _summarize(results)
    return EvalReport(
        bin_width_s=config.bin_width_s,
        n_seeds=n_seeds,
        per_type=per_type,
        results=results,
        control_false_positives=control_fp,
    )


def _summarize(results: List[InjectionResult]) -> Dict[str, TypeSummary]:
    by_type: Dict[str, List[InjectionResult]] = {}
    for r in results:
        by_type.setdefault(r.anomaly_type, []).append(r)
    out: Dict[str, TypeSummary] = {}
    for atype, rs in by_type.items():
        n = len(rs)
        fired = [r for r in rs if r.fired]
        recall = len(fired) / n if n else 0.0
        right = sum(1 for r in rs if r.right_template) / n if n else 0.0
        total_fires = sum(1 + r.n_bystander_fires for r in fired)
        precision = (len(fired) / total_fires) if total_fires else 0.0
        latencies = [r.latency_bins for r in fired if r.latency_bins is not None]
        out[atype] = TypeSummary(atype, n, round(recall, 3),
                                 round(right, 3), round(precision, 3),
                                 latencies)
    return out


def format_report(report: EvalReport) -> str:
    lines = []
    lines.append("=== Spectral detector eval ===")
    lines.append(f"bin_width={report.bin_width_s}s  seeds={report.n_seeds}  "
                 f"control_false_positives={report.control_false_positives}")
    lines.append("")
    lines.append(f"  {'type':<12} {'n':>3} {'recall':>7} {'right':>7} "
                 f"{'prec':>6}   latency(bins) min/p50/p90/max")
    for atype in ("silence", "drift", "freq_shift", "burst_onset"):
        s = report.per_type.get(atype)
        if not s:
            continue
        st = s.latency_stats()
        lat = f"{st['min']}/{st['p50']}/{st['p90']}/{st['max']}"
        lines.append(f"  {atype:<12} {s.n:>3} {s.recall:>7.2f} "
                     f"{s.right_template_rate:>7.2f} {s.precision:>6.2f}   {lat}")
    return "\n".join(lines)
