"""Evaluation harnesses (§7).

Two harnesses, kept apart because the two layers fail for different reasons:
  - ``harness``      — spectral detector eval: pure signal processing,
                       deterministic, no LLM, CI-friendly (§7.4-7.5).
  - ``llm_eval``     — LLM filter eval: does the filter rank a real outage above
                       an injected benign deploy/backfill (§7.6).
"""

from .inject import (  # noqa: F401
    Injection, inject_silence, inject_drift, inject_freq_shift,
    inject_burst_onset, inject_deploy,
)
from .harness import run_spectral_eval, EvalReport  # noqa: F401
