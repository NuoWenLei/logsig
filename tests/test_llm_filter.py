"""Stage 6 tests: LLM urgency filter ranks the real outage above benign deltas."""

from logsig.config import default_config
from logsig.descriptors import AnomalyDescriptor
from logsig.llm_filter import HeuristicRanker, PAGE, IGNORE
from logsig.eval.llm_eval import run_llm_eval


def _desc(tid, atype, cooccur=None, **kw):
    return AnomalyDescriptor(
        template_id=tid, template_example="x", anomaly_type=atype,
        detected_at=0.0, detected_at_bin=0, detection_latency_bins=None,
        cooccurring_flags=cooccur or [], **kw)


def test_lone_silence_outranks_synchronized_deploy():
    anomalies = [
        _desc("T_outage", "silence",
              baseline_summary={"dominant_period_s": 30}),
        # a synchronized deploy: many templates flag at the same instant
        _desc("T_a", "burst_onset", cooccur=["T_b", "T_c", "T_d"]),
        _desc("T_b", "burst_onset", cooccur=["T_a", "T_c", "T_d"]),
        _desc("T_c", "burst_onset", cooccur=["T_a", "T_b", "T_d"]),
    ]
    ranked = HeuristicRanker().rank(anomalies)
    assert ranked[0].template_id == "T_outage"
    assert ranked[0].triage == PAGE
    # the synchronized deploy templates are de-prioritized to ignore
    deploy = [v for v in ranked if v.template_id in ("T_a", "T_b", "T_c")]
    assert all(v.triage == IGNORE for v in deploy)
    assert all("synchronized" in v.rationale for v in deploy)


def test_llm_eval_passes_offline():
    res = run_llm_eval(default_config())
    assert res.outage_above_benign is True
    assert res.outage_rank == 0
