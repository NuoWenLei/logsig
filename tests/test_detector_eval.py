"""Stage 3 + 5 tests: each spectral anomaly type fires; eval metrics hold."""

from logsig.config import default_config
from logsig.eval.harness import run_spectral_eval


def test_all_four_anomaly_types_fire_with_full_recall():
    cfg = default_config()
    report = run_spectral_eval(cfg, n_seeds=2)
    for atype in ("silence", "drift", "freq_shift", "burst_onset"):
        summ = report.per_type.get(atype)
        assert summ is not None, f"{atype} not evaluated"
        assert summ.recall >= 0.99, f"{atype} recall too low: {summ.recall}"
        assert summ.right_template_rate >= 0.99, f"{atype} wrong template"


def test_no_false_positives_on_clean_corpus():
    cfg = default_config()
    report = run_spectral_eval(cfg, n_seeds=2)
    # the control run (no injection) must produce zero spectral flags:
    # delta-only firing means always-flat stays flat => no flag (§5.2).
    assert report.control_false_positives == 0


def test_silence_latency_is_bounded_by_detection_window():
    cfg = default_config()
    report = run_spectral_eval(cfg, n_seeds=2)
    stats = report.per_type["silence"].latency_stats()
    # latency must be finite and not absurd (within a couple detection windows)
    assert stats["max"] is not None
    assert stats["max"] <= 3 * cfg.detect.detect_window_bins
