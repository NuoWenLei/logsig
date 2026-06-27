# logsig — Spectral Log Anomaly Detector

> Treat production logs as **time-domain signals** and detect anomalies as
> **disruptions in the frequency-domain structure** of per-log-template count
> series. A frozen LLM sits *downstream as an urgency filter only* — it never
> does detection, it ranks/triages/explains the cheap detector's output.

This is an **open-source repo** (not a product). It is the v0 implementation of
the spec in [`SPEC.md`](SPEC.md); the design rationale lives in
[`docs/DESIGN.md`](docs/DESIGN.md) and the Stage-0 survey findings in
[`docs/stage0_findings.md`](docs/stage0_findings.md).

---

## The thesis (§1)

Logs are parsed into templates (Drain3); each template's occurrences-per-time-bin
become a 1-D signal; a baseline **spectral signature** is learned per template;
anomalies are **deltas against that baseline**. The spectral layer exists to
catch the quiet, slow, absence-shaped failures that are hardest to catch and most
embarrassing to miss:

- **Silence** — a periodic template (e.g. a 30 s health check) stops logging.
  Nothing *new* appears; something *stopped*. Count/threshold detectors are blind
  to absence — this only shows up as a periodicity that flatlined.
- **Drift** — a polling loop degrading from clean 30 s intervals to ragged
  30–90 s intervals. Same count, no new lines, but the spectrum smears.
  Early-warning degradation, invisible to novelty *and* threshold detectors.
- **Frequency shift** — retry logic firing 4× more often but still under any rate
  threshold because the absolute numbers are small.
- **Burst onset** — a quiet/absent template suddenly producing a dense run.

### What this detector explicitly does NOT own (conceded by design, §1.2)

**Novel-template / never-before-seen error anomalies are conceded to a trivial
detector.** This is a deliberate positioning decision, not a gap. A brand-new
ERROR stack trace is loud, already caught by most teams, and trivially handled by
a regex for new templates + an LLM reading them. The spectral layer is justified
**entirely** by the absence-shaped cases above that the trivial detectors miss.
Do not try to make the spectral path compete on novel-error detection — that is a
category error.

### The non-negotiable invariant (§1.3)

> Cheap detectors cast a slightly-too-wide net; the expensive-but-smart LLM
> filter prunes it. Never push judgment down into the layer that cannot afford
> judgment.

The spectral detector must not decide whether an anomaly is benign. It surfaces
structural deltas as structured descriptors; the LLM adjudicates with context the
detector lacks (deploy markers, batch windows, surrounding log text).

---

## Architecture

```
raw logs → [1] template (Drain3) → [2] route by alert tag
                                      ├── alert ───────► [E] trivial detector  (novel template / count spike)
                                      └── non-alert ──► [3] bucket → [4] baseline + eligibility → [5] spectral detect
                                                                                  (silence / drift / freq-shift / burst)
   [E] ∪ [5]  →  [6] LLM urgency filter  (rank / triage / explain; never detects)
```

| Stage | Module | Cost |
|-------|--------|------|
| [1] template | `template.py` (Drain3) | cheap |
| [2] route | `route.py` (alert tag, §2.2) | cheap |
| [3] bucket | `bucket.py` (per-template count series) | cheap |
| [4] baseline + eligibility | `baseline.py` (Welch signature, swappable strategy) | cheap |
| [5] spectral detect | `detector.py` (delta-only firing) | cheap |
| [E] trivial detect | `trivial.py` (novelty + count spike) | cheap |
| [6] LLM filter | `llm_filter.py` (frozen model behind a thin interface) | **expensive** |

---

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # numpy, scipy, drain3
pip install -e '.[dev]'     # + pytest
pip install -e '.[llm]'     # + anthropic, for the live LLM filter (optional)

# Stage 0: verify tones exist and derive the bin width
logsig survey --bin-widths 4 8 16

# Spectral detector eval: precision/recall + latency distribution per type
logsig eval --seeds 3

# LLM urgency-filter eval: real outage ranked above an injected benign deploy
logsig llm-eval

# Run the full pipeline on a log and print the ranked anomaly shortlist
logsig run --rank
```

By default every command uses the **synthetic** generator (deterministic,
offline). Add `--bgl path/to/BGL.log` to run on real BGL once downloaded.

### Sample results (synthetic corpus)

```
=== Spectral detector eval ===
bin_width=8.0s  seeds=3  control_false_positives=0

  type           n  recall   right   prec   latency(bins) min/p50/p90/max
  silence        3    1.00    1.00   1.00   212/212/212/212
  drift          3    1.00    1.00   1.00   564/629/679/692
  freq_shift     3    1.00    1.00   1.00    84/84/84/84
  burst_onset    3    1.00    1.00   1.00   180/180/180/180
```

The **latency distribution** is the metric standard benchmarks don't give you,
and it matters enormously for silence (late detection is the whole failure mode).
Silence latency is bounded by the detection window (the window must fill with
zeros before the energy collapse is visible); freq-shift is caught fastest.

```
=== LLM urgency-filter eval ===
outage rank: 0   outage_above_benign: PASS ✓
   0 page   0.95 silence      T4   template lost its 30.12s periodicity — absence-shaped failure
   3 ignore 0.40 burst_onset  T12  quiet template produced a dense run; could be a one-time backfill
   4 ignore 0.10 burst_onset  T6   co-occurs with 3 other templates — looks like a synchronized deploy/scale
```

The lone structural break (silence) ranks top; the synchronized deploy is
recognized via `cooccurring_flags` and de-prioritized — exactly the separation
the design depends on.

---

## Configuration (§9)

Every signal-processing knob is a **config parameter, not a constant**
(`src/logsig/config.py`): `bin_width_s` (the Nyquist ceiling, §3.2), the training
window bounds (§5), the flatness eligibility threshold (§4.3), and the Welch/STFT
window + overlap (§4.1). Load a JSON config with `--config path.json`.

---

## Data

The spectral premise needs a **long, multi-cycle span** to baseline (real BGL is
~4.7 M lines over **214.7 days**). Two data paths:

- **Synthetic (default).** `logsig gen` / the eval harness produce BGL-format
  logs with *known* periodic tones, broadband-aperiodic streams, and quiet
  templates. This is the deterministic CI/demo path — it lets Stage 0 and the
  eval run end-to-end without a 708 MB download.
- **Real BGL (honest eval).** Download the **Loghub** BGL (not the CFDR version —
  they carry *different labels*):

  ```
  https://zenodo.org/record/3227177/files/BGL.tar.gz
  ```

  Untar to `data/BGL.log` and pass `--bgl data/BGL.log`. The same pipeline runs
  unchanged. (Templates reference: **Loghub-2.0**, 320 annotated BGL templates —
  https://github.com/logpai/loghub-2.0 — not the older 2k sample.)

- **OpenStack (the cadence-bearing corpus).** BGL turned out to be **flat** — it
  has long span but almost no clean operational tones (it's bursty HPC RAS
  logging; see `docs/stage0_findings.md`). **OpenStack** is the corpus where the
  spectral premise actually holds: its `nova.compute.resource_tracker` periodic
  task surfaces as a clean ~60s tone, and 22/33 templates are periodic at a
  5–10s bin width. Download and run:

  ```
  https://zenodo.org/records/8196385/files/OpenStack.tar.gz
  # untar; then:
  logsig survey --openstack data/OpenStack --bin-widths 5 10
  logsig run    --openstack data/OpenStack --rank
  ```

  OpenStack uses the actual log **level** as the router (no alert tag): ERROR/
  CRITICAL → trivial path, else spectral.

Tiny real samples (`data/samples/BGL_2k.log`, `data/samples/OpenStack_2k.log`)
are committed for Stage-1 parsing/routing tests.

> **Do NOT score against the datasets' native anomaly labels (§7.1).** They mark
> the novel-template / error anomalies this project *concedes*. We use the
> *normal* logs as realistic texture and **inject our own** controlled anomalies.

---

## Known limitations (read these — they are by design, §2.2 / §7.7 / §10)

**Two routing leaks (not fixed in v0):**

1. **Periodic errors** (a retry logging at ERROR on a regular cadence, a circuit
   breaker flapping rhythmically) are routed to the count path, so we lose the
   *cadence* signal on them — the count detector still catches them *firing*, just
   not the *rhythm*. Acceptable v0 loss.
2. **Aperiodic non-errors** (one-time INFO backfills, sporadic user events) stay
   in the spectral path as broadband noise. Contained by the eligibility gate +
   delta-only firing, **not** by an up-front classifier.

**Eval honesty caveat (§7.7):** injecting into aggregate logs assumes the
perturbed template is independent of the others, which isn't fully true (a
silenced health check often co-occurs with error bursts elsewhere). And the
synthetic tones are **cleaner than reality**. These are detector *unit-tests*,
not end-to-end production claims. The honest evaluation runs the same pipeline on
real BGL.

**Out of scope for v0 (§10):** no rolling baseline / v1 disagreement matrix (only
the swappable interface, see `docs/DESIGN.md`); no template post-merging
(over-decomposition is intended); no Thunderbird/HDFS until BGL works end-to-end.

---

## Tests

```bash
pytest -q          # 17 tests; deterministic, no network, no LLM
```

Covers: BGL parsing + alert-tag routing on the real sample; flatness/dominant-
period recovery on a known periodic series; the eligibility gate; all four
injected anomaly types firing with full recall and zero control false positives;
trivial novelty + count-spike; and the LLM filter ranking a real outage above a
synchronized deploy.

---

## License & citation

Code: MIT (see `pyproject.toml`).

This project uses the **Loghub** log datasets, which are free for research and
academic use provided you cite the Loghub paper and link the repository:

- Loghub: <https://github.com/logpai/loghub>
- Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. *Loghub: A
  Large Collection of System Log Datasets for AI-driven Log Analytics.* ISSRE 2023.
