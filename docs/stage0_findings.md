# Stage 0 — Periodicity Survey Findings

> Spec §7.3 / §8. The entire spectral premise rests on the logs actually
> containing clean tones to break. This note records the survey that **verifies
> that, rather than assuming it**, and derives the default bin width empirically.

Reproduce with:

```bash
logsig survey --bin-widths 4 8 16            # synthetic corpus (default)
logsig survey --bgl data/BGL.log --bin-widths 30 60 120   # real BGL once downloaded
```

## What the survey does

1. Templates the corpus (Drain3).
2. Builds per-template count series at several candidate bin widths.
3. Computes **spectral flatness** per template (0 = clean tone, 1 = white noise).
4. Prints the flatness distribution + the periodic (low-flatness) candidates.
5. Suggests a default bin width = ~T/4 of the fastest periodic tone (§3.2 Nyquist
   oversampling).

## Gate verdict: **HEALTHY ✓**

On the synthetic baseline corpus (which stands in for real BGL when the 708 MB
Zenodo download is unavailable — see README "Data") the flatness distribution is
strongly **bimodal**: a small cluster of clean tones near 0, and a broadband mass
near 1. That bimodality is exactly what the detector needs — there are real tones
to lose.

```
=== Periodicity survey @ bin_width = 8.0s ===
templates: 14   periodic (flatness < 0.4): 2
GATE: HEALTHY ✓
suggested default bin width (~T/4 of fastest tone): 7.53s

flatness distribution (0=clean tone, 1=white noise):
  [0.00,0.10)     1 #
  [0.10,0.20)     1 #
  [0.20,0.30)     1 #
  [0.30,0.40)     0
  [0.40,0.50)     0
  [0.50,0.60)     1 #
  [0.60,0.70)     0
  [0.70,0.80)     0
  [0.80,0.90)     0
  [0.90,1.00)    10 ##########

periodic candidates (low flatness):
  template_id  period_s  flatness   prom     mean  example
  T4             30.12     0.035   0.35    0.267  health check ok ...
  T2             60.24     0.205   0.18    0.133  poll upstream status ...
```

## Derived default bin width

Across candidate widths the fastest robust tone is ~30 s, so the suggested bin
width lands at **~7.5–8.6 s** (oversampling the 30 s tone at ~T/4). We adopt
**`bin_width_s = 8.0`** as the default in `config.py`. This is a *derived*
default, not a guess — and it is a config parameter, so a real-BGL survey can
re-derive it (BGL's fastest interesting tones tend to be slower, so on real BGL
you will likely land on a larger bin width, e.g. 30–60 s).

## Note for real BGL

When you point the survey at a downloaded `BGL.log`, expect:
- a larger template count (~320 in Loghub-2.0 labeling; default Drain
  granularity over-decomposes to far more — see below),
- slower dominant tones (node heartbeats / periodic RAS messages),
- therefore a **larger** suggested bin width.

If real BGL comes back *flatter than expected* (few low-flatness templates), you
learn that on day one — before building anything else on top — which is the whole
point of running this first.

---

## Real BGL findings (run on the full 4.74M-line BGL, 2026-06-27)

Reproduce: `logsig survey --bgl data/BGL.log --bin-widths 30 60 120`.

**This is the experiment the whole spectral thesis stands or falls on, and the
result is sobering. Recording it honestly.**

| bin width | templates | "periodic" (flatness < 0.4) | share of templates ≥ 0.9 flatness |
|-----------|-----------|------------------------------|------------------------------------|
| 30 s      | 1776      | 7                            | 1598 / 1776 ≈ **90%**              |
| 60 s      | 1776      | 7                            | 1605 / 1776 ≈ **90%**              |
| 120 s     | 1776      | 5                            | 1593 / 1776 ≈ **90%**              |

Two things stand out:

### 1. BGL is overwhelmingly flat

~90% of templates sit in the top flatness decile (white-noise / sparse). Only
**5–7 of 1776** templates clear the periodicity bar at any bin width. The naive
gate prints `HEALTHY ✓` (it only requires ≥1 candidate), but that verdict is
**too generous** — see below. The honest read is: BGL does **not** contain a
healthy population of clean periodic *operational* tones.

### 2. The few "periodic" candidates are drift artifacts, not cadences

The reported dominant **period of every candidate equals the Welch segment
length** (or its second bin):

```
bin=30s : candidates at 7680s (=30×256, lowest bin) and 3840s (2nd bin)
bin=60s : candidates at 15360s (=60×256) and 7680s
bin=120s: candidates at 30720s (=120×256) and 15360s
```

A genuine jittered cadence (a 30 s health check) would put its peak at a
period **independent of the analysis window**. A peak pinned to the lowest
resolvable frequency means the energy is **slow drift / a trend over the
214-day run** (the job mix and load changing across months), *not* a periodic
tone. The candidate templates confirm it by content — `instruction cache parity
error corrected`, `double-hummer alignment exceptions`, `MACHINE CHECK PLB write
IRQ`, `generating core.304` — these are bursty RAS/error messages whose slow
envelope is being read as "low flatness", not heartbeats or polling loops.

This is exactly the non-stationarity §4.1 warns about, and it surfaces the
brittleness of single-bin `argmax` dominant-frequency picking (see
`docs/DESIGN.md` and the fuzzy-matching discussion): the detector would currently
mark these drift templates **eligible** and could fire spurious `freq_shift` /
`silence` on them — false positives born of a trend, not a broken rhythm.

### Implications (honest)

- **For BGL specifically:** it is a *poor exemplar* for this detector. BGL is HPC
  supercomputer RAS logging; it has bursty error clustering and slow load drift,
  but few crisp operational cadences. The spec picked it for its long continuous
  span, which is real — but span ≠ periodicity. The "right problem" texture
  (health checks, polling, cron, heartbeats) is sparse here.
- **This does not condemn the thesis globally** — a web-services / microservice
  corpus (liveness probes, schedulers, cron, retry loops) would plausibly look
  very different. But it does mean **BGL cannot be the validation that the
  premise holds in production**; the synthetic harness remains the only place the
  detector sees clean tones, and that is circular. Validating on a real corpus
  with genuine cadences is now the top open question.
- **Detector hardening this motivates** (not yet built):
  1. **Exclude the DC-adjacent / trend shelf** (lowest 1–2 frequency bins) or
     detrend more aggressively, so slow drift can't masquerade as a tone.
  2. **Require a real peak above the low-frequency shelf** — tighten
     `min_peak_prominence` and add an absolute-period sanity bound
     (reject "period == segment length").
  3. **Band-energy matching against the baseline's per-window distribution**
     instead of single-bin `argmax`, so eligibility reflects a genuine,
     jitter-tolerant cadence rather than a trend artifact.

Bottom line: **Stage 0 did its job** — it told us on day one, cheaply, that BGL
is the wrong dataset to prove this detector, and that the current eligibility
gate is too permissive about low-frequency drift. Better to know now than after
building an eval on top of it.
