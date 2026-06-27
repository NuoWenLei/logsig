"""Config round-trips and exposes the required knobs as parameters (§9)."""

import json

from logsig.config import Config, default_config


def test_roundtrip(tmp_path):
    cfg = default_config()
    cfg.bin_width_s = 12.5
    cfg.eligibility.flatness_threshold = 0.33
    p = tmp_path / "cfg.json"
    cfg.save(str(p))
    loaded = Config.from_file(str(p))
    assert loaded.bin_width_s == 12.5
    assert loaded.eligibility.flatness_threshold == 0.33


def test_required_knobs_are_parameters():
    cfg = default_config()
    # §3.2 bin width, §5 training window, §4.3 flatness, §4.1 STFT window/overlap
    assert isinstance(cfg.bin_width_s, float)
    assert 0.0 <= cfg.baseline.train_start_frac < cfg.baseline.train_end_frac <= 1.0
    assert 0.0 < cfg.eligibility.flatness_threshold < 1.0
    assert cfg.baseline.welch_nperseg_bins > 0
    assert 0.0 <= cfg.baseline.welch_overlap_frac < 1.0
