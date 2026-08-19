#!/usr/bin/env python
"""
run.py — the command line for strategy-lab.

    python run.py serve                              start the web GUI
    python run.py list                               what strategies exist
    python run.py backtest stat_arb --symbols KO,PEP
    python run.py sweep mean_reversion --symbols KO,PEP,XOM,CVX
    python run.py fetch AAPL MSFT --start 2020-01-01 --end 2024-12-31
    python run.py fetch-market                       refresh the S&P 500 benchmark
    python run.py scan-pairs --all-with-fundamentals --min-price 5 --out pairs.txt
    python run.py freeze --out dist                   static mirror of the read-only pages

Everything the GUI can do is available here, because a research tool you
cannot call from a script is a research tool you cannot automate. The reverse
is not true and is not meant to be: `sweep` and `-p name=value` choose
parameter values, and the GUI has no control for either. See `lab/api.py`
for the same two operations shaped for calling from a strategy file.
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
                 run_sweep)
from lab.api import print_result, print_sweep  # noqa: E402
from lab.data.loaders import DATA_DIR, fetch_yfinance  # noqa: E402


def progress_bar(fraction: float, stage: str) -> None:
    bar = "#" * int(fraction * 30)
    print(f"\r  [{bar:<30}] {fraction:5.0%}  {stage}", end="", flush=True)


# ── shared arguments ──────────────────────────────────────────────────────

def add_data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data", default="prices.pkl",
                        help="price file under data/ — real prices only")
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
    path = Path(args.data)
    if not path.exists():
        path = DATA_DIR / args.data
    if not path.exists():
        raise SystemExit(
            f"no price file at {args.data!r}. Download one first:\n"
            f"  python run.py fetch AAPL MSFT --start 2021-01-01 --end 2025-01-01")
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

    result = Hub(dataset, strategies, make_config(args)).run(progress_bar)
    print_result(result)

    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        import json
        out.write_text(json.dumps(result.to_dict(), default=str), encoding="utf-8")
        print(f"  written to {out}\n")

    if args.open:
        from lab.web.app import write_run
        label = args.label or ", ".join(s.title for s in strategies)
        job_id = write_run("backtest", label, result.to_dict())
        print(f"  saved — `python run.py serve`, then /run/{job_id}\n")


def cmd_sweep(args) -> None:
    dataset = make_dataset(args)
    overrides = {}
    for item in args.override or []:
        name, values = item.split("=", 1)
        overrides[name.strip()] = [v.strip() for v in values.split(",")]

    print(f"\n{dataset}\n")
    result = run_sweep(dataset, args.strategy, overrides=overrides or None,
                       config=make_config(args), progress=progress_bar)
    # Printed, never persisted for the GUI: a sweep result is a table of
    # parameter values, and the interface does not show those anywhere.
    print_sweep(result, top=args.top)


def cmd_fetch(args) -> None:
    dataset = fetch_yfinance(args.symbols, args.start, args.end,
                             interval=args.interval)
    print(f"\n{dataset}\n  cached under {DATA_DIR / 'cache'}\n")


def cmd_fetch_market(args) -> None:
    """Download the two series every result is measured against.

    Separate from `fetch` because neither is a universe — they are the
    yardsticks, they go to fixed paths, and they are the two data files
    committed to the repository so that a clone reproduces every alpha and
    every Sharpe offline.

      * `market_spy.csv`  — the S&P 500, what alpha and beta are measured
                            against.
      * `riskfree_3m.csv` — the 13-week T-bill yield, annualised decimal.
                            Sharpe and alpha are both defined on returns in
                            excess of it, and assuming it is zero hands a
                            low-beta strategy alpha it did not earn.
    """
    from lab.data.loaders import (CLOSE, MARKET_FILE, MARKET_SYMBOL,
                                  RISK_FREE_FILE, RISK_FREE_SYMBOL,
                                  fetch_yfinance)

    dataset = fetch_yfinance([MARKET_SYMBOL], args.start, args.end)
    out = DATA_DIR / MARKET_FILE
    frame = dataset._fields[CLOSE]
    tidy = (frame[MARKET_SYMBOL].rename(CLOSE).dropna().rename_axis("timestamp")
            .reset_index())
    tidy.insert(1, "symbol", MARKET_SYMBOL)
    tidy.to_csv(out, index=False)
    print(f"\n{dataset}\n  written to {out}")

    rates = fetch_yfinance([RISK_FREE_SYMBOL], args.start, args.end)
    series = rates._fields[CLOSE][RISK_FREE_SYMBOL].dropna()
    # ^IRX quotes the yield in percent; stored as a decimal so no consumer has
    # to remember the factor of 100. Clipped at zero — the series prints small
    # negative values a few days in 2015 and 2020, which are quirks of the
    # discount-rate quote rather than a rate anyone was paid.
    rf_out = DATA_DIR / RISK_FREE_FILE
    ((series / 100.0).clip(lower=0.0).rename("rate").rename_axis("timestamp")
     .reset_index().to_csv(rf_out, index=False))
    print(f"  risk-free {series.index[0].date()} to {series.index[-1].date()}, "
          f"mean {series.mean() / 100:.2%} — written to {rf_out}\n")


def cmd_scan_pairs(args) -> None:
    """Engle-Granger over an entire universe, not one named pair at a time.

    `stat_arb_ev`'s pairs come from wherever `--symbols` puts them, two at a
    time in list order — naming a universe of unrelated tickers there trades
    unrelated tickers. This finds pairs that actually pass the test, so the
    strategy has something real to trade instead of whatever order a symbol
    list happened to arrive in. See `lab/analysis/cointegration.py` for what
    the test does and why the significance level below is corrected, not the
    0.05 a single pair would use.
    """
    dataset = make_dataset(args)
    from lab.analysis import scan_pairs

    all_symbols = list(dataset.symbols)
    last = len(dataset.index) - 1
    prices: dict[str, "np.ndarray"] = {}
    dropped_price = dropped_ratio = 0
    for symbol in all_symbols:
        series = dataset.history(symbol, last)
        if len(series) < 60:
            continue
        if args.min_price > 0 and series.min() < args.min_price:
            dropped_price += 1
            continue
        # A halted or delisted ticker's stale quote getting repeated, or a
        # reverse split adjusted inconsistently across the series, produces
        # a price ratio no continuously-traded stock reaches — GME's 2021
        # squeeze, about as violent as real trading gets, peaks at ~20x its
        # low. `--max-ratio` catches these before they can look "related" to
        # everything else in the universe on the strength of one bad tick.
        if args.max_ratio > 0 and series.min() > 0 \
                and series.max() / series.min() > args.max_ratio:
            dropped_ratio += 1
            continue
        prices[symbol] = series

    # engle_granger requires equal-length series; the universe rarely shares
    # one calendar exactly (a listing, a halt), so every series is trimmed to
    # the shortest one, keeping the most recent bars rather than the oldest.
    if prices:
        shortest = min(len(v) for v in prices.values())
        prices = {s: v[-shortest:] for s, v in prices.items()}

    total_pairs = len(prices) * (len(prices) - 1) // 2
    print(f"\n  {len(all_symbols)} symbols requested, {dropped_price} dropped "
          f"below ${args.min_price:.2f} at some point, {dropped_ratio} dropped "
          f"for a max/min ratio over {args.max_ratio:.0f}x, {len(prices)} "
          f"scanned ({total_pairs:,} pairs)\n")
    if total_pairs == 0:
        raise SystemExit("nothing to scan — widen the universe or loosen "
                         "--min-price / --max-ratio")

    scans = scan_pairs(prices, level=args.level, max_half_life=args.max_half_life,
                       correction=None if args.no_correction else "bonferroni",
                       progress=progress_bar)
    print()

    tested = len(scans)
    tradeable = [s for s in scans if s.tradeable]
    effective = args.level / tested if (tested and not args.no_correction) else args.level
    print(f"  {tested:,} pairs cleared the cheap prefilter and reached the full "
          f"test; {len(tradeable)} tradeable at p < {effective:.2e}"
          f"{' (bonferroni-corrected)' if not args.no_correction else ' (uncorrected)'}\n")

    for s in tradeable[:args.top]:
        r = s.result
        print(f"  {s.a:<10} {s.b:<10} p={r.p_value:.4f}  alpha={r.alpha:+.3f}  "
              f"half-life={r.half_life:5.1f}d  beta={r.beta:+.3f}")
    print()

    if args.out:
        chosen = tradeable[:args.pairs]
        if not chosen:
            print("  nothing tradeable — no file written\n")
        else:
            flat = ",".join(sym for pair in chosen for sym in (pair.a, pair.b))
            Path(args.out).write_text(flat, encoding="utf-8")
            print(f"  {len(chosen)} pairs ({len(chosen) * 2} symbols) written to "
                  f"{args.out}\n\n  python run.py backtest stat_arb_ev --symbols "
                  f"\"$(cat {args.out})\" -p require_cointegration=True "
                  f"--start {args.start or '...'} --end {args.end or '...'}\n")


def cmd_serve(args) -> None:
    from lab.web.app import main
    print(f"\n  strategy-lab on http://{args.host}:{args.port}\n")
    main(host=args.host, port=args.port, debug=args.debug)


def cmd_freeze(args) -> None:
    from lab.web.freeze import freeze
    freeze(Path(args.out), base_path=args.base_path)



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
    backtest.add_argument("--open", action="store_true",
                          help="also save it where the GUI can open it")
    backtest.add_argument("--label", default=None,
                          help="name this run in the GUI")
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

    market = sub.add_parser("fetch-market",
                            help="download the S&P 500 benchmark to data/")
    market.add_argument("--start", default="2005-01-01")
    # Dated explicitly rather than left as None: the fetch cache keys on the
    # range, and "None" would key one entry that silently means a different
    # window every day.
    market.add_argument("--end",
                        default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    market.set_defaults(func=cmd_fetch_market)

    scan = sub.add_parser(
        "scan-pairs",
        help="Engle-Granger over a whole universe; writes a pair list a pairs "
             "strategy can actually use")
    scan.add_argument("--data", default="prices.pkl",
                      help="price file under data/ — real prices only")
    scan.add_argument("--fundamentals", default=None,
                      help="fundamentals JSON under data/")
    scan.add_argument("--symbols", default="",
                      help="comma-separated universe to scan; every pair "
                           "within it is tested")
    scan.add_argument("--all-with-fundamentals", action="store_true",
                      help="scan every company that has fundamentals, "
                           "instead of naming symbols")
    scan.add_argument("--start", default=None)
    scan.add_argument("--end", default=None)
    scan.add_argument("--min-price", type=float, default=5.0,
                      help="drop a symbol if it ever closes below this in "
                           "the window — sub-penny and halted tickers "
                           "produce prices no real position could clear at "
                           "that size, not a tradeable relationship")
    scan.add_argument("--max-ratio", type=float, default=25.0,
                      help="drop a symbol if its max close divided by its "
                           "min close in the window exceeds this — stale "
                           "quotes and bad split adjustments produce ratios "
                           "no continuously-traded stock reaches (GME's 2021 "
                           "squeeze peaks near 20x). 0 disables.")
    scan.add_argument("--level", type=float, default=0.05,
                      help="significance level before correction")
    scan.add_argument("--max-half-life", type=float, default=60.0,
                      help="reject a pair whose spread reverts slower than "
                           "this many bars, even if cointegrated")
    scan.add_argument("--no-correction", action="store_true",
                      help="use --level as-is instead of Bonferroni-"
                           "correcting it by the number of pairs tested — "
                           "the count that scan prints will be inflated by "
                           "chance")
    scan.add_argument("--top", type=int, default=30,
                      help="how many tradeable pairs to print")
    scan.add_argument("--pairs", type=int, default=40,
                      help="how many of the best pairs --out writes")
    scan.add_argument("--out", default=None,
                      help="write the chosen pairs as a flat --symbols-"
                           "ready comma list to this file")
    scan.set_defaults(func=cmd_scan_pairs)

    serve = sub.add_parser("serve", help="start the web GUI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--debug", action="store_true")
    serve.set_defaults(func=cmd_serve)

    freeze = sub.add_parser(
        "freeze", help="render the read-only pages to static HTML/JSON")
    freeze.add_argument("--out", default="dist",
                        help="output directory (default: dist/)")
    freeze.add_argument("--base-path", default="",
                        help="subdirectory the site will be served under, "
                             "e.g. strategy-lab for a GitHub Pages project "
                             "site at user.github.io/strategy-lab/")
    freeze.set_defaults(func=cmd_freeze)

    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    arguments.func(arguments)
