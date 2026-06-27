# Design notes

This captures the design rationale and, importantly, the **v1 vision that is
deliberately NOT built in v0** (spec §5.4, §10). v0 builds only the fixed
baseline behind a swappable interface; everything in "§ v1" below is documented
here so the interface is shaped correctly, then left alone.

## The non-negotiable invariant (§1.3)

> Cheap detectors cast a slightly-too-wide net; the expensive-but-smart LLM
> filter prunes it. Never push judgment down into the layer that cannot afford
> judgment.

The spectral detector must **not** decide whether an anomaly is benign. It
surfaces structural deltas with structured descriptors; the LLM adjudicates with
context the detector does not have. This separation is the whole design.

## Why delta-only firing (§5.2)

The detector's output is never "is aperiodic" — it is "spectral signature
**changed** from baseline". The `always-flat stays flat ⇒ do not flag` row is the
entire reason the LLM filter stays usable: every flag is a *delta against
baseline*, so the LLM only ever sees transitions, never standing facts. If the
filter is ever receiving a firehose, the bug is upstream in the firing rule, not
in the prompt (§6.3).

## Why the eligibility gate instead of an up-front classifier (§4.3)

> You are not detecting aperiodicity; you are detecting the loss/absence of
> periodicity, which is the same measurement.

Aperiodicity is the *default* state — the absence of a peak — and falls out of
the same FFT for free. A template is "spectrally eligible" to fire a
periodicity-break only if it earned a stable dominant tone over the baseline.
Perpetually-aperiodic non-errors (one-time backfills, sporadic events) simply
never earn a baseline worth deviating from, so they self-select out — **not** via
an up-front classifier. (Burst-onset is the documented exception: it still fires
for non-eligible templates, because "quiet → dense run" is itself a delta.)

## Why fixed baseline for v0 (§5.1)

A single fixed training window defines "normal". Its known weakness — every
deploy looks like a cliff of simultaneous deltas — is treated as a **feature**:
"many streams went anomalous at once" is a strong *deploy-detection* signal
handed to the LLM (via `cooccurring_flags`), not a problem to suppress. The
rolling baseline's failure (silently normalizing a slow real degradation as it
creeps) is strictly worse because it is *silent* — which is why fixed wins for
v0.

The baseline lives behind `BaselineStrategy` (`src/logsig/baseline.py`) so v1 can
add strategies without touching the detector loop (§5.3). This is the only thing
v1 needs to reach back and touch.

---

## § v1 — DO NOT BUILD (documented only, §5.4)

v1 runs **fixed + rolling baselines simultaneously and uses their
agreement/disagreement as the signal.** This is best-of-both, not a naive union
(a naive union gives the *worst* of both: deploy-cliffs from fixed AND missed
creep from rolling). The two baselines fail on opposite, distinguishable
signatures:

- **Fixed's false positive = synchronized cliff** (many streams deviate at the
  same instant ⇒ deploy). Response: don't suppress — **re-baseline** (snap fixed
  forward, hand the LLM one "deploy detected" event instead of N anomalies).
- **Rolling's false negative = slow creep** (one stream drifting monotonically,
  each step within tolerance). Caught by keeping fixed as the **long-memory
  anchor**: flag when rolling says "fine" but fixed says "far from origin".

### The disagreement matrix (the v1 detector)

| Fixed | Rolling | Shape                       | Verdict                              |
|-------|---------|-----------------------------|--------------------------------------|
| flag  | flag    | same instant                | real abrupt anomaly                  |
| flag  | quiet   | synchronized, many streams  | deploy → re-baseline                  |
| flag  | quiet   | single stream, monotonic    | **creep (high-value catch)**         |
| quiet | flag    | —                           | recent local anomaly vs current regime |
| quiet | quiet   | —                           | normal                               |

To implement v1, add `RollingBaseline` and a `DisagreementStrategy` to
`_STRATEGIES` in `baseline.py`, and have the detector consult two signatures per
template. The descriptor contract (§6.2) does not change — `cooccurring_flags`
already carries the "synchronized" signal the deploy row needs.

---

## Two documented v0 routing leaks (§2.2) — NOT fixed in v0

1. **Periodic errors** (a retry logging at ERROR on a regular cadence, a circuit
   breaker flapping rhythmically) are routed to the count path, so we lose the
   *cadence* signal on them. The count detector still catches them *firing*, just
   not the *rhythm*. Acceptable v0 loss.
2. **Aperiodic non-errors** (one-time INFO backfills, sporadic user events) stay
   in the spectral path and contribute broadband noise with no real tone. This is
   contained by the eligibility gate (§4.3) + delta-only firing (§5.2), **not** by
   an up-front classifier.

## Eval honesty caveat (§7.7)

Injecting into aggregate logs assumes the perturbed template is independent of
the others, which isn't fully true (a silenced health check in reality often
co-occurs with error bursts elsewhere). For v0 this independence is a fine
simplification — we test "can the detector see the structural break", not
cross-stream causality. The synthetic tones are also **cleaner than reality**:
the synthetic corpus is the deterministic CI/demo path; the honest evaluation
runs the same pipeline on a real downloaded BGL. These are detector unit-tests,
not end-to-end production claims.
