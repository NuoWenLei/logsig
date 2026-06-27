"""logsig command-line interface.

Subcommands:
  survey       Stage-0 periodicity survey (§7.3) — verify tones exist, derive bin width.
  eval         Spectral detector eval (§7.4-7.5) — precision/recall/latency per type.
  llm-eval     LLM urgency-filter eval (§7.6) — outage ranked above benign deploy.
  run          Run the full pipeline on a log file and print the anomaly shortlist.
  gen          Write a synthetic BGL-format log file.

By default the data source is the synthetic generator (deterministic, offline).
Pass --bgl <path> to run on a real downloaded BGL.log instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .config import Config, default_config
from .events import Event


def _load_config(path: Optional[str]) -> Config:
    return Config.from_file(path) if path else default_config()


def _events_from_args(args, config: Config) -> List[Event]:
    from .ingest.bgl import iter_bgl, parse_bgl_line
    from .ingest.synthetic import (generate_synthetic_bgl, SyntheticConfig,
                                   default_specs)
    from .template import Templater

    templater = Templater()
    if args.bgl:
        return list(templater.template(iter_bgl(args.bgl)))
    cfg = SyntheticConfig(seed=args.seed, duration_s=args.duration,
                          specs=default_specs())
    lines = generate_synthetic_bgl(cfg)
    raws = [r for r in (parse_bgl_line(l) for l in lines) if r]
    return list(templater.template(raws))


def cmd_survey(args) -> int:
    from .route import route
    from .survey import survey_at_bin_width, format_survey

    config = _load_config(args.config)
    events = _events_from_args(args, config)
    spectral = route(events).spectral
    widths = args.bin_widths or [config.bin_width_s]
    out = []
    for bw in widths:
        res = survey_at_bin_width(spectral, config, bw)
        print(format_survey(res))
        print()
        out.append(res.to_dict())
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"[wrote survey json → {args.json}]")
    return 0


def cmd_eval(args) -> int:
    from .eval.harness import run_spectral_eval, format_report

    config = _load_config(args.config)
    report = run_spectral_eval(config, n_seeds=args.seeds, duration_s=args.duration)
    print(format_report(report))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\n[wrote eval json → {args.json}]")
    # exit non-zero if any type missed (CI gate)
    ok = all(s.recall >= 0.99 for s in report.per_type.values())
    return 0 if ok else 1


def cmd_llm_eval(args) -> int:
    from .eval.llm_eval import run_llm_eval, format_llm_eval
    from .llm_filter import make_filter

    config = _load_config(args.config)
    filt = make_filter(args.provider)
    res = run_llm_eval(config, filt=filt, seed=args.seed, duration_s=args.duration)
    print(format_llm_eval(res))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(res.to_dict(), f, indent=2)
        print(f"\n[wrote llm-eval json → {args.json}]")
    return 0 if res.outage_above_benign else 1


def cmd_run(args) -> int:
    from .pipeline import run_from_events
    from .llm_filter import make_filter

    config = _load_config(args.config)
    events = _events_from_args(args, config)
    result = run_from_events(events, config)
    print(f"events={len(events)}  templates(spectral)={len(result.binned.series)}  "
          f"eligible={len(result.baseline.eligible_ids)}")
    print(f"spectral anomalies={len(result.spectral_anomalies)}  "
          f"trivial anomalies={len(result.trivial_anomalies)}")
    print()
    if args.rank:
        filt = make_filter(args.provider)
        for i, v in enumerate(filt.rank(result.all_anomalies)):
            print(f"  {i:>2} {v.triage:<7} {v.urgency:>5.2f} {v.anomaly_type:<13} "
                  f"{v.template_id:<8} {v.rationale}")
    else:
        for a in result.all_anomalies:
            print(f"  [{a.source}] {a.anomaly_type:<13} {a.template_id:<8} "
                  f"@bin {a.detected_at_bin}  baseline={a.baseline_summary}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump([a.to_dict() for a in result.all_anomalies], f, indent=2)
        print(f"\n[wrote anomalies json → {args.json}]")
    return 0


def cmd_gen(args) -> int:
    from .ingest.synthetic import SyntheticConfig, default_specs, write_synthetic_bgl

    cfg = SyntheticConfig(seed=args.seed, duration_s=args.duration,
                          specs=default_specs())
    n = write_synthetic_bgl(args.out, cfg)
    print(f"wrote {n} synthetic BGL lines → {args.out}")
    return 0


def _add_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--bgl", help="path to a real BGL.log (else synthetic)")
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--duration", type=float, default=24 * 3600.0,
                   help="synthetic span in seconds")
    p.add_argument("--config", help="path to a JSON config file")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="logsig",
                                description="Spectral Log Anomaly Detector")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("survey", help="Stage-0 periodicity survey")
    _add_source_args(s)
    s.add_argument("--bin-widths", type=float, nargs="*", dest="bin_widths",
                   help="candidate bin widths in seconds")
    s.add_argument("--json", help="write survey result to JSON path")
    s.set_defaults(func=cmd_survey)

    e = sub.add_parser("eval", help="spectral detector eval")
    _add_source_args(e)
    e.add_argument("--seeds", type=int, default=3)
    e.add_argument("--json", help="write eval report to JSON path")
    e.set_defaults(func=cmd_eval)

    le = sub.add_parser("llm-eval", help="LLM urgency-filter eval")
    _add_source_args(le)
    le.add_argument("--provider", default="heuristic",
                    choices=["heuristic", "anthropic"])
    le.add_argument("--json", help="write llm-eval result to JSON path")
    le.set_defaults(func=cmd_llm_eval)

    r = sub.add_parser("run", help="run the full pipeline and list anomalies")
    _add_source_args(r)
    r.add_argument("--rank", action="store_true",
                   help="rank via the LLM urgency filter")
    r.add_argument("--provider", default="heuristic",
                   choices=["heuristic", "anthropic"])
    r.add_argument("--json", help="write anomalies to JSON path")
    r.set_defaults(func=cmd_run)

    g = sub.add_parser("gen", help="write a synthetic BGL log file")
    g.add_argument("--out", default="data/synthetic_bgl.log")
    g.add_argument("--seed", type=int, default=1234)
    g.add_argument("--duration", type=float, default=24 * 3600.0)
    g.set_defaults(func=cmd_gen)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
