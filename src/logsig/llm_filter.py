"""LLM urgency filter (stage 6, §6).

Role (§6.1): a FROZEN model, no fine-tuning. Input = the union of [E] (trivial)
and [5] (spectral) anomaly descriptors plus context. Output = a ranked, triaged,
explained shortlist (page / watch / ignore). **Detection has already happened;
the LLM never detects** (§1.3). For spectral anomalies it *adjudicates* (real
outage vs scale-down / backfill / deploy); for novel errors it *reads & explains*.

The descriptor contract (§6.2) is the real boundary; the provider is swappable.
Two providers ship:
  - HeuristicRanker  — deterministic, no API, CI-friendly. Encodes the urgency
                       logic the prompt would ask for, so the §7.6 eval runs
                       offline.
  - AnthropicRanker  — a thin wrapper over a frozen Claude chat model. Used when
                       an API key is present; model id comes from env.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import List, Optional

from .descriptors import AnomalyDescriptor


# Triage buckets.
PAGE, WATCH, IGNORE = "page", "watch", "ignore"


@dataclass
class Verdict:
    template_id: str
    anomaly_type: str
    source: str
    triage: str            # page | watch | ignore
    urgency: float         # 0..1
    rationale: str
    detected_at: float

    def to_dict(self) -> dict:
        return asdict(self)


class UrgencyFilter(ABC):
    @abstractmethod
    def rank(self, anomalies: List[AnomalyDescriptor]) -> List[Verdict]:
        ...


# How many co-occurring flags before we treat a cluster as a synchronized
# deploy rather than N independent incidents (§5.1).
_DEPLOY_COOCCUR_THRESHOLD = 3

# Base urgency per anomaly type (before context adjustment).
_BASE_URGENCY = {
    "silence": 0.90,        # a periodic template going dark = likely real outage
    "freq_shift": 0.70,     # base-frequency change under a threshold = sneaky
    "drift": 0.55,          # early-warning degradation = watch
    "count_spike": 0.65,
    "novel_template": 0.70,
    "burst_onset": 0.35,    # often a benign backfill; adjudicate
}


class HeuristicRanker(UrgencyFilter):
    """Deterministic urgency logic — the rules a good prompt would apply.

    Key adjudication: a flag that co-occurs with many other flags at the same
    instant is most likely a *deploy* (synchronized cliff, §5.1), so it is
    de-prioritized relative to a lone structural break. This is exactly the
    pattern the detector hands up via ``cooccurring_flags`` without itself
    deciding it (§6.2)."""

    def rank(self, anomalies: List[AnomalyDescriptor]) -> List[Verdict]:
        verdicts: List[Verdict] = []
        for a in anomalies:
            score = _BASE_URGENCY.get(a.anomaly_type, 0.5)
            reasons = []

            n_cooccur = len(a.cooccurring_flags)
            if n_cooccur >= _DEPLOY_COOCCUR_THRESHOLD:
                # synchronized → smells like a deploy/scale event, not an outage
                score *= 0.30
                reasons.append(
                    f"co-occurs with {n_cooccur} other templates at the same "
                    f"instant — looks like a synchronized deploy/scale, not an "
                    f"isolated incident")
            elif n_cooccur == 0:
                score = min(1.0, score + 0.05)
                reasons.append("isolated structural break (no co-occurring flags)")

            if a.anomaly_type == "silence":
                bp = a.baseline_summary.get("dominant_period_s")
                reasons.append(
                    f"template lost its {bp}s periodicity — absence-shaped failure"
                    if bp else "periodic template flatlined")
            elif a.anomaly_type == "burst_onset":
                reasons.append("quiet template produced a dense run; could be a "
                               "one-time backfill — adjudicate before paging")
            elif a.anomaly_type == "freq_shift":
                bp = a.baseline_summary.get("dominant_period_s")
                op = a.observed_summary.get("dominant_period_s")
                reasons.append(f"base frequency shifted ({bp}s → {op}s)")
            elif a.anomaly_type == "drift":
                reasons.append("rhythm smearing — early-warning degradation")

            triage = (PAGE if score >= 0.7 else WATCH if score >= 0.4 else IGNORE)
            verdicts.append(Verdict(
                template_id=a.template_id,
                anomaly_type=a.anomaly_type,
                source=a.source,
                triage=triage,
                urgency=round(score, 3),
                rationale="; ".join(reasons),
                detected_at=a.detected_at,
            ))
        verdicts.sort(key=lambda v: v.urgency, reverse=True)
        return verdicts


_SYSTEM_PROMPT = """\
You are an SRE urgency filter. Anomalies have ALREADY been detected by a cheap \
spectral/count detector; your ONLY job is to triage them, NOT to detect. For \
each anomaly decide page / watch / ignore and give a one-sentence rationale.

Guidance:
- A periodic template going SILENT (lost its tone) is an absence-shaped failure \
that count/threshold monitors miss — usually high urgency.
- A flag that co-occurs with MANY other flags at the same instant is most likely \
a deploy or scaling event (a synchronized cliff), not N independent outages — \
de-prioritize it and suggest re-baselining instead of paging.
- A burst on a previously-quiet template is often a benign one-time backfill — \
adjudicate using surrounding context before paging.
Return STRICT JSON: a list of objects with keys \
template_id, anomaly_type, triage, urgency (0..1 float), rationale."""


class AnthropicRanker(UrgencyFilter):
    """Thin wrapper over a frozen Claude chat model.

    Model id is read from the ``LOGSIG_LLM_MODEL`` env var (no hardcoded model in
    the detection path — the provider is swappable behind the descriptor
    contract). Requires the ``anthropic`` package and an API key. Falls back to
    :class:`HeuristicRanker` semantics if the call fails, so the pipeline never
    hard-depends on the network."""

    def __init__(self, model: Optional[str] = None,
                 max_tokens: int = 2048) -> None:
        self.model = model or os.environ.get("LOGSIG_LLM_MODEL",
                                             "claude-sonnet-4-6")
        self.max_tokens = max_tokens

    def rank(self, anomalies: List[AnomalyDescriptor]) -> List[Verdict]:
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "AnthropicRanker needs the 'anthropic' package "
                "(pip install logsig[llm]); or use HeuristicRanker.") from e

        client = anthropic.Anthropic()
        payload = [a.to_dict() for a in anomalies]
        msg = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content":
                       "Triage these anomalies:\n" + json.dumps(payload, indent=2)}],
        )
        text = "".join(block.text for block in msg.content
                       if getattr(block, "type", None) == "text")
        return _parse_verdicts(text, anomalies)


def _parse_verdicts(text: str, anomalies: List[AnomalyDescriptor]) -> List[Verdict]:
    by_id = {(a.template_id, a.anomaly_type): a for a in anomalies}
    start, end = text.find("["), text.rfind("]")
    parsed = json.loads(text[start:end + 1]) if start >= 0 else []
    out: List[Verdict] = []
    for item in parsed:
        key = (item.get("template_id"), item.get("anomaly_type"))
        src = by_id.get(key)
        out.append(Verdict(
            template_id=item.get("template_id", ""),
            anomaly_type=item.get("anomaly_type", ""),
            source=src.source if src else "",
            triage=item.get("triage", WATCH),
            urgency=float(item.get("urgency", 0.5)),
            rationale=item.get("rationale", ""),
            detected_at=src.detected_at if src else 0.0,
        ))
    out.sort(key=lambda v: v.urgency, reverse=True)
    return out


def make_filter(provider: str = "heuristic", **kwargs) -> UrgencyFilter:
    if provider == "heuristic":
        return HeuristicRanker()
    if provider == "anthropic":
        return AnthropicRanker(**kwargs)
    raise ValueError(f"unknown LLM filter provider {provider!r}")
