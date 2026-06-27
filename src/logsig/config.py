"""Configuration for the spectral pipeline.

Every knob the spec calls out as "must be a config parameter, not a constant"
lives here (§3.2 bin width, §5 training window, §4.3 flatness eligibility,
§4.1 STFT/Welch window+overlap). Defaults are sane starting points; the Stage-0
periodicity survey (§7.3) is what justifies the bin-width default empirically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class BaselineConfig:
    """Training-window bounds + spectral-estimator settings (§4, §5)."""

    # Training window expressed as fractions of the full time span [0, 1].
    # v0 uses a single fixed window (§5.1). A fraction keeps it dataset-agnostic.
    train_start_frac: float = 0.0
    train_end_frac: float = 0.35

    # Welch / STFT parameters (§4.1). nperseg is in *bins*; overlap is a fraction.
    # Welch = averaged windowed periodograms == the "distribution of windowed
    # spectra" the spec wants instead of one global FFT.
    welch_nperseg_bins: int = 256
    welch_overlap_frac: float = 0.5
    welch_window: str = "hann"
    welch_detrend: str = "constant"  # remove per-segment mean so a base-rate
    # offset does not masquerade as DC power.


@dataclass
class EligibilityConfig:
    """The eligibility gate (§4.3): who is allowed to fire a structure-break."""

    # A template is "spectrally eligible" only if it established a stable
    # dominant tone over the baseline window: flatness strictly below this.
    # Near 0 ⇒ clean tone (peaky); near 1 ⇒ white noise (no structure).
    flatness_threshold: float = 0.4

    # The dominant peak must carry at least this fraction of total (non-DC)
    # power to count as a real tone (guards against a flat spectrum that
    # happens to dip under the flatness threshold by luck).
    min_peak_prominence: float = 0.10

    # A template needs at least this mean count per bin over the training
    # window to be considered "established" at all (otherwise it is treated as
    # quiet/absent, eligible only for burst-onset).
    min_baseline_mean_count: float = 0.05


@dataclass
class DetectConfig:
    """Delta-vs-baseline firing thresholds for the spectral detector (§5.2).

    Every rule here is a *transition* test (current vs baseline signature),
    never a state test — that is what keeps the LLM filter from drowning.
    """

    # Sliding detection window over the live (post-training) region.
    detect_window_bins: int = 256
    detect_hop_bins: int = 32

    # silence: an eligible (had-a-tone) template whose energy collapses.
    # Fire when the live window's mean count falls below this fraction of the
    # baseline mean count.
    silence_rate_frac: float = 0.25

    # freq_shift: eligible template whose dominant period moved beyond this
    # relative tolerance (|p_live - p_base| / p_base).
    freq_shift_period_tol: float = 0.30

    # drift: eligible template, tone smeared — flatness rose by at least this
    # absolute amount while energy is retained and period roughly held.
    drift_flatness_increase: float = 0.15

    # burst_onset: any template (eligible or not) whose live mean count jumps
    # by at least this factor AND by at least this absolute amount over the
    # baseline mean. Fires for quiet/absent baselines (the headline case) and
    # for sudden dense runs on otherwise-eligible templates.
    burst_factor: float = 4.0
    burst_min_abs_increase: float = 0.5
    # A baseline is "quiet enough to burst from" if its mean count per bin is
    # below this; used to keep burst distinct from a periodic template merely
    # getting busier (that is freq_shift).
    burst_baseline_quiet_max: float = 0.5


@dataclass
class TrivialConfig:
    """Error/alert-path detector (§2.1 [E], stage 4)."""

    # Count-spike: a known alert template firing more than (mean + k*std) over
    # the trailing baseline window, or above an absolute floor.
    spike_zscore: float = 4.0
    spike_min_count: int = 5
    spike_window_bins: int = 64


@dataclass
class Config:
    """Top-level pipeline config."""

    # §3.2: bin width is the Nyquist ceiling. Derive from the Stage-0 survey;
    # do NOT treat as a constant. Default chosen for synthetic/BGL-scale tones
    # in the tens-of-seconds range (oversampled ~T/4..T/5).
    bin_width_s: float = 8.0

    baseline: BaselineConfig = field(default_factory=BaselineConfig)
    eligibility: EligibilityConfig = field(default_factory=EligibilityConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    trivial: TrivialConfig = field(default_factory=TrivialConfig)

    # Which baseline strategy to use (§5.3 swappable interface). v0: "fixed".
    baseline_strategy: str = "fixed"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        d = dict(d)
        sub = {
            "baseline": BaselineConfig,
            "eligibility": EligibilityConfig,
            "detect": DetectConfig,
            "trivial": TrivialConfig,
        }
        kwargs = {}
        for key, klass in sub.items():
            if key in d and d[key] is not None:
                kwargs[key] = klass(**d.pop(key))
        kwargs.update(d)
        return cls(**kwargs)

    @classmethod
    def from_file(cls, path: str) -> "Config":
        with open(path) as f:
            return cls.from_dict(json.load(f))

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


def default_config() -> Config:
    return Config()
