# Spectral Log Anomaly Detector — Implementation Spec (v0)

> This is the canonical v0 handoff spec this repository implements. The
> implementation notes that explain *how* each section is realized live in
> [`docs/DESIGN.md`](docs/DESIGN.md); the per-section `§` references in the code
> point back here.

## 0. One-paragraph summary

This is an **open-source repo** (not a product, not a startup) that treats
production logs as **time-domain signals** and detects anomalies as
**disruptions in the frequency-domain structure** of per-log-template count
series. Logs are parsed into templates (Drain3); each template's
occurrences-per-time-bin become a 1-D signal; a baseline spectral signature is
learned per template; anomalies are **deltas against that baseline** (a periodic
template losing its tone, a quiet template bursting, a rhythm smearing, a base
rate shifting). A frozen LLM sits **downstream as an urgency filter only** — it
never does detection, it ranks/triages/explains the cheap detector's output. The
deliverable includes a **synthetic-anomaly evaluation harness** built on the BGL
dataset, because standard log-anomaly benchmarks test a different problem (novel
error templates) than this detector targets (temporal-structure anomalies on
known templates).

## 1. Core thesis and scope boundaries

### 1.1 What this detector owns (the high-value cases)

Temporal-structure anomalies in **known** templates — the quiet, slow,
absence-shaped failures: **Silence** (a periodic template stops logging),
**Drift** (clean intervals degrading to ragged ones — the spectrum smears),
**Frequency shift** (a known-benign template's base rate quietly multiplying),
**Burst onset** (a quiet/absent template suddenly producing a dense run).

### 1.2 What this detector explicitly does NOT own (conceded by design)

**Novel-template / never-before-seen error anomalies are conceded to a trivial
detector.** A brand-new ERROR stack trace is loud, already caught by most teams,
and trivially handled by a regex for new templates + an LLM reading them. The
spectral layer is justified entirely by the §1.1 cases the trivial detectors
miss. Do not make the spectral path compete on novel-error detection.

### 1.3 The non-negotiable architectural invariant

> Cheap detectors cast a slightly-too-wide net; the expensive-but-smart LLM
> filter prunes it. Never push judgment down into the layer that cannot afford
> judgment.

The spectral detector must not decide whether an anomaly is benign. It surfaces
structural deltas with structured descriptors; the LLM adjudicates with context
the detector does not have.

## 2. System architecture (v0)

`raw logs → [1] template (Drain3) → [2] route by level → {[E] trivial | [3]
bucket → [4] baseline+eligibility → [5] spectral detect} → [6] LLM urgency
filter`.

### 2.2 The routing rule (and why it's a proxy)

Error-level templates → trivial path `[E]`; non-error templates → spectral path.
For BGL the first-column alert tag *is* the router: `-` = non-alert (→ spectral),
anything else = alert (→ trivial). Two documented v0 leaks (NOT fixed in v0):
periodic errors lose their cadence signal; aperiodic non-errors stay in the
spectral path (handled by the eligibility gate + delta firing, not an up-front
classifier).

## 3. Signal extraction (templating + bucketing)

- §3.1 **Over-decompose rather than collapse.** Use Drain3 standard granularity;
  do not post-merge templates in v0.
- §3.2 **Bin width is the Nyquist ceiling.** To see period `T` you need bin width
  `≤ T/2`; oversample to `≤ T/4..T/5`. Bin width is a **config parameter**,
  derived empirically from the Stage-0 survey — not a constant.
- §3.3 Output: a table keyed by `template_id` of uniformly-binned integer count
  series + the bin width and edges. Sparse series self-select out at the gate.

## 4. Baseline + eligibility

- §4.1 Use **STFT/Welch**, not a single global FFT — a distribution of windowed
  spectra so non-stationarity doesn't drown anomalies in leakage.
- §4.2 Per-template signature: windowed power spectrum, dominant frequency/-ies,
  **spectral flatness** (geo/arith mean; ~0 ⇒ clean tone, ~1 ⇒ white noise),
  amplitude at the dominant frequency.
- §4.3 **Eligibility gate.** A template can fire a periodicity-break only if it
  established a stable dominant tone over the baseline (flatness below a
  configurable threshold). Always-flat templates never earn a baseline worth
  deviating from. (Burst-onset still fires for non-eligible templates.)

## 5. Baseline strategy: fixed for v0, two-baseline for v1

- §5.1 v0 = a single **fixed** training window. Its deploy-cliff weakness is a
  *feature*: a synchronized cliff is a deploy-detection signal for the LLM.
- §5.2 **Firing rule: flag transitions (deltas), not states.** Output is never
  "is aperiodic" — it is "signature changed from baseline". `always-flat stays
  flat ⇒ do not flag`.
- §5.3 Store the baseline behind a **named, swappable interface** (the only thing
  v1 reaches back to touch).
- §5.4 v1 (**DO NOT BUILD** — document only): fixed + rolling baselines run
  simultaneously; their disagreement matrix is the detector. See `docs/DESIGN.md`.

## 6. LLM urgency filter

- §6.1 Frozen model, no fine-tuning. Input: the union of `[E]` and `[5]` as
  structured descriptors + context. Output: ranked / triaged / explained
  shortlist (page / watch / ignore). Novel errors → *read & explain*; spectral →
  *adjudicate*.
- §6.2 Structured descriptor contract (nameable, not a raw distance): fields
  `template_id, template_example, anomaly_type, detected_at,
  detection_latency_bins, baseline_summary, observed_summary,
  surrounding_context, cooccurring_flags`. `cooccurring_flags` lets the LLM
  recognize the synchronized-deploy pattern without the detector deciding it.
- §6.3 Cost scales with flag **volume**; the delta firing rule + eligibility gate
  keep volume to genuine events. A firehose means an upstream firing-rule bug.

## 7. Evaluation harness (first-class deliverable)

- §7.1 **Do not use the datasets' native labels** — they mark the conceded
  novel-template anomalies. Use *normal* logs as texture; inject your own.
- §7.2 Primary dataset: **BGL** (Loghub version). Stress-test later:
  Thunderbird. Skip: HDFS.
- §7.3 **Stage 0 (write first):** template, build series at candidate bin widths,
  compute spectral flatness, print the distribution + periodic candidates, derive
  the default bin width. Gate: confirm a healthy low-flatness population.
- §7.4 Four injection transforms (into genuinely-periodic templates): **silence**,
  **drift**, **frequency shift**, **burst onset** — each a timestamped transform
  on one template's series, with the injection window recorded as ground truth.
- §7.5 Metrics: did it fire (precision/recall over injections); **detection
  latency** as a *distribution* (matters most for silence); right template.
- §7.6 **Two separate harnesses**: spectral (deterministic, no LLM, CI-friendly)
  and LLM-filter (does it rank a real outage above an injected benign deploy).
- §7.7 Honesty caveat: injected anomalies assume template independence and are
  cleaner than reality — a detector unit-test, not an end-to-end production claim.

## 8. Implementation stages (build order)

Stage 0 survey → Stage 1 ingest+template+route → Stage 2 bucket+baseline+
eligibility → Stage 3 spectral detector → Stage 4 trivial detector → Stage 5 eval
harness → Stage 6 LLM filter.

## 10. Anti-scope (v0)

No rolling baseline / v1 disagreement matrix (only the swappable interface); don't
fix the two routing leaks; no template post-merging; don't score against native
labels; LLM never detects and the detector never judges benign/urgent; no global
FFT baseline; don't hardcode bin width or use an up-front aperiodicity classifier;
no Thunderbird/HDFS until BGL works end-to-end.

## 11. Definition of done (v0)

Stage-0 findings note; an `eval` command reporting precision/recall + latency
distribution + right-template per type; the spectral detector firing on all four
injected types; the trivial path firing on novel templates + count spikes; the
LLM filter ranking a real outage above an injected benign deploy/backfill; the
baseline behind a swappable interface; a README documenting the thesis, conceded
scope, routing leaks, eval honesty caveat, and the Loghub citation.
