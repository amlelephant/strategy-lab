#!/usr/bin/env python
"""
run.py — the command line for strategy-lab.

    python run.py serve                              start the web GUI
    python run.py list                               what strategies exist
    python run.py backtest stat_arb --symbols KO,PEP
    python run.py sweep mean_reversion --symbols KO,PEP,XOM,CVX
    python run.py fetch AAPL MSFT --start 2020-01-01 --end 2024-12-31

Everything the GUI can do is available here, because a research tool you
cannot call from a script is a research tool you cannot automate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252 and choke on anything outside it.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from lab import (SCENARIOS, CostModel, FillTiming, Hub, RunConfig,  # noqa: E402
                 attach_fundamentals, build, describe_all, load_prices,
                 run_sweep, synthetic)
from lab.data.loaders import DATA_DIR, fetch_yfinance  # noqa: E402


# ── shared arguments ──────────────────────────────────────────────────────

def add_data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data", default="synthetic",
                        help="price file under data/, or 'synthetic'")
    parser.add_argument("--fundamentals", default=None,
                        help="fundamentals JSON under data/")
    parser.add_argument("--symbols", default="",
                        help="comma-separated universe; pair strategies "
                             "consume it two at a time")
    parser.add_argument("--all-with-fundamentals", action="store_true",
                        help="use every company that has fundamentals")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--scenario", default="realistic", choices=sorted(SCENARIOS))
    parser.add_argument("--timing", default="next_open",
                        choices=[t.value for t in FillTiming])
    parser.add_argument("--seed", type=int, default=7)


def make_dataset(args):
    if args.data == "synthetic":
        dataset = synthetic(("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"), bars=900)
    else:
        path = Path(args.data)
        if not path.exists():
            path = DATA_DIR / args.data
        dataset = load_prices(path)

    if args.fundamentals:
        fpath = Path(args.fundamentals)
        if not fpath.exists():
            fpath = DATA_DIR / args.fundamentals
        dataset = attach_fundamentals(dataset, fpath)

    if args.all_with_fundamentals:
        symbols = [s for s in dataset.symbols if s in dataset.fundamentals]
        if not symbols:
            raise SystemExit("that dataset has no fundamentals attached")
        dataset = dataset.for_universe(symbols)
    elif args.symbols:
        dataset = dataset.for_universe(
            [s.strip().upper() for s in args.symbols.split(",") if s.strip()])

    if args.start or args.end:
        dataset = dataset.between(args.start, args.end)
    return dataset


def make_config(args) -> RunConfig:
    base = SCENARIOS[args.scenario]
    return RunConfig(
        starting_cash=args.cash,
        costs=CostModel(commission_bps=base.commission_bps,
                        slippage_bps=base.slippage_bps, latency=base.latency,
                        timing=FillTiming(args.timing), seed=args.seed))


def parse_params(pairs: list[str]) -> dict:
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"parameter must be name=value, got {item!r}")
        name, value = item.split("=", 1)
        out[name.strip()] = value.strip()
    return out


# ── commands ──────────────────────────────────────────────────────────────

def cmd_list(_args) -> None:
    for s in describe_all():
        print(f"\n  {s['key']:<20} {s['title']}")
        print(f"  {'':<20} {s['universe']} universe · "
              f"{len(s['params'])} parameters")
        print(f"  {'':<20} {s['summary']}")
        if s["provenance"]:
            print(f"  {'':<20} from: {s['provenance'].splitlines()[0]}")
    print()


def cmd_backtest(args) -> None:
    dataset = make_dataset(args)
    strategies = [build(key, parse_params(args.param)) for key in args.strategy]
    print(f"\n{dataset}\n")

    def progress(fraction, stage):
        bar = "#" * int(fraction * 30)
        print(f"\r  [{bar:<30}] {fraction:5.0%}  {stage}", end="", flush=True)

    result = Hub(dataset, strategies, make_config(args)).run(progress)
    print("\n")
    for warning in result.warnings:
        print(f"  note: {warning}")

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    table = result.table().copy()
    for column in ("return", "cagr", "max_dd"):
        table[column] = table[column].map(
            lambda v: "—" if pd.isna(v) else f"{v:+.2%}")
    table["hit"] = table["hit"].map(lambda v: "—" if pd.isna(v) else f"{v:.0%}")
    for column in ("sharpe", "t"):
        table[column] = table[column].map(
            lambda v: "—" if pd.isna(v) else f"{v:.2f}")
    table["costs"] = table["costs"].map(lambda v: f"${v:,.0f}")
    print(table.to_string(index=False))

    print()
    for sleeve in result.sleeves:
        perf = sleeve.performance
        if not perf.is_significant:
            verdict = "NOT distinguishable from luck"
        elif perf.sharpe < 0:
            verdict = "significantly negative — reliably loses money"
        else:
            verdict = "clears |t| > 2"
        print(f"  {sleeve.title}: {perf.headline()}")
        print(f"  {'':<{len(sleeve.title)}}  {verdict}; "
              f"{sleeve.orders_filled}/{sleeve.orders_requested} orders filled, "
              f"{len(sleeve.rejections)} rejections")
    print(f"\n  {result.elapsed_seconds:.1f}s\n")

    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        import json
        out.write_text(json.dumps(result.to_dict(), default=str), encoding="utf-8")
        print(f"  written to {out}\n")


def cmd_sweep(args) -> None:
    dataset = make_dataset(args)
    overrides = {}
    for item in args.override or []:
        name, values = item.split("=", 1)
        overrides[name.strip()] = [v.strip() for v in values.split(",")]

    print(f"\n{dataset}\n")

    def progress(fraction, stage):
        bar = "#" * int(fraction * 30)
        print(f"\r  [{bar:<30}] {stage}", end="", flush=True)

    result = run_sweep(dataset, args.strategy, overrides=overrides or None,
                       config=make_config(args), progress=progress)
    print("\n")
    pd.set_option("display.width", 220)
    print(result.rows.head(args.top).to_string(index=False))
    print(f"\n  {result.verdict()}\n")


def cmd_fetch(args) -> None:
    dataset = fetch_yfinance(args.symbols, args.start, args.end,
                             interval=args.interval)
    print(f"\n{dataset}\n  cached under {DATA_DIR / 'cache'}\n")


def cmd_serve(args) -> None:
    from lab.web.app import main
    print(f"\n  strategy-lab on http://{args.host}:{args.port}\n")
    main(host=args.host, port=args.port, debug=args.debug)


# ── entry point ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list registered strategies").set_defaults(
        func=cmd_list)

    backtest = sub.add_parser("backtest", help="run one or more strategies")
    backtest.add_argument("strategy", nargs="+")
    backtest.add_argument("-p", "--param", action="append",
                          help="name=value, applied to every named strategy")
    backtest.add_argument("--save", default=None, help="write the result JSON")
    add_data_args(backtest)
    backtest.set_defaults(func=cmd_backtest)

    sweep = sub.add_parser("sweep", help="grid-search one strategy")
    sweep.add_argument("strategy")
    sweep.add_argument("-o", "--override", action="append",
                       help="name=v1,v2,v3")
    sweep.add_argument("--top", type=int, default=15)
    add_data_args(sweep)
    sweep.set_defaults(func=cmd_sweep)

    fetch = sub.add_parser("fetch", help="download and cache prices")
    fetch.add_argument("symbols", nargs="+")
    fetch.add_argument("--start", required=True)
    fetch.add_argument("--end", required=True)
    fetch.add_argument("--interval", default="1d")
    fetch.set_defaults(func=cmd_fetch)

    serve = sub.add_parser("serve", help="start the web GUI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--debug", action="store_true")
    serve.set_defaults(func=cmd_serve)

    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    arguments.func(arguments)
