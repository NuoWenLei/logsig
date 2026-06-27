"""Stage 2 tests: spectral primitives, signature, eligibility gate (§4)."""

import numpy as np

from logsig.config import default_config
from logsig.spectral import welch_spectrum, spectral_flatness
from logsig.bucket import bucketize
from logsig.baseline import FixedBaseline
from logsig.events import Event


def _periodic_series(period_bins=8, n=2048, amp=5):
    t = np.arange(n)
    base = amp * (1 + np.cos(2 * np.pi * t / period_bins))
    return np.round(base).astype(int)


def test_flatness_bounds():
    assert spectral_flatness(np.ones(100)) <= 1.0
    # a clean tone is peaky -> low flatness; white noise -> high
    tone = _periodic_series().astype(float)
    rng = np.random.default_rng(0)
    noise = rng.poisson(5, 2048).astype(float)
    fs = 1.0
    s_tone = welch_spectrum(tone, fs=fs, nperseg_bins=256)
    s_noise = welch_spectrum(noise, fs=fs, nperseg_bins=256)
    assert s_tone.flatness < 0.3
    assert s_noise.flatness > 0.5
    assert s_tone.flatness < s_noise.flatness


def test_known_periodic_series_recovers_dominant_period():
    period_bins = 10
    series = _periodic_series(period_bins=period_bins, n=4096)
    fs = 1.0  # 1 sample per bin
    spec = welch_spectrum(series.astype(float), fs=fs, nperseg_bins=512)
    assert spec.dominant_period_s is not None
    # recover the injected period within one bin of resolution
    assert abs(spec.dominant_period_s - period_bins) < 1.5


def test_eligibility_gate_separates_tone_from_noise():
    cfg = default_config()
    cfg.bin_width_s = 1.0
    rng = np.random.default_rng(1)
    n = 4096
    t0 = 1_000_000.0
    events = []
    # periodic template every 10s
    for i in range(0, n, 10):
        events.append(Event(t0 + i, "T_periodic", "INFO", "tick", False))
    # aperiodic (poisson) template
    for ts in np.cumsum(rng.exponential(5.0, 800)):
        events.append(Event(t0 + ts, "T_noise", "INFO", "noise", False))
    binned = bucketize(events, cfg.bin_width_s)
    baseline = FixedBaseline(cfg).fit(binned)
    sig_p = baseline.signature("T_periodic")
    sig_n = baseline.signature("T_noise")
    assert sig_p.eligible is True       # earned a tone -> can fire structure-break
    assert sig_n.eligible is False      # always-flat -> self-selects out (§4.3)


def test_baseline_behind_swappable_interface():
    from logsig.baseline import make_baseline_strategy
    cfg = default_config()
    strat = make_baseline_strategy(cfg)
    assert strat.name == "fixed"
    import pytest
    cfg.baseline_strategy = "rolling"  # v1, not built
    with pytest.raises(ValueError):
        make_baseline_strategy(cfg)
