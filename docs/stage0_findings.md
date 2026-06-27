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
- a larger template count (~320 in Loghub-2.0 labeling),
- slower dominant tones (node heartbeats / periodic RAS messages),
- therefore a **larger** suggested bin width.

If real BGL comes back *flatter than expected* (few low-flatness templates), you
learn that on day one — before building anything else on top — which is the whole
point of running this first.
