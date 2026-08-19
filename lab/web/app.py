"""
lab/web/app.py
==============
The Flask front end.

Four screens, one question each — the discipline is borrowed from minFit and
it is what keeps a research tool from turning into a dashboard:

    /                 What have I built?
    /strategy/<key>   Where does this one live, and what happens if I run it?
    /run/<id>         Did it beat the market?
    /showcase         What does this body of work add up to?

Runs happen on a background thread with a job id, because a five-strategy pass
over 1,700 symbols takes the better part of a minute and a page that blocks on
that is a page nobody uses. Finished runs are written to `runs/<id>.json`, so a
result outlives the process that produced it — a result you cannot link someone
to is not really a result.

**No parameter appears anywhere in this interface** — not as a control, not as
a table, not in a JSON response this app serves. A `Param` value is a fact
about a strategy's code, and the moment a browser can change one, the file
stops being the answer to "what does this strategy do?" So a strategy page
offers the things that genuinely belong to a *run* — which data, which
universe, which frictions — and points at the file for everything else.
Choosing a parameter value, and sweeping a grid of them, is `lab.api`, called
from the strategy's own file. See `lab/api.py`.
"""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request

from ..core.contract import Universe
from ..core.costs import SCENARIOS, CostModel, FillTiming
from ..core.hub import Hub, RunConfig
from ..core.registry import all_strategies, build, get, scaffold
from ..data.dataset import Dataset
from ..data.loaders import (DATA_DIR, MARKET_FILE, MARKET_SYMBOL,
                            attach_fundamentals, catalog, fundamentals_catalog,
                            load_prices)
from . import markdown as md

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "runs"
WRITEUPS_DIR = ROOT / "research" / "strategies"

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_SORT_KEYS"] = False
# Set by scripts/freeze.py before it crawls the app, never by a live server.
# Templates read it (as `config.STATIC_BUILD`, injected automatically) to
# drop controls a static host cannot run: starting a job, polling one,
# scaffolding a new strategy file.
app.config["STATIC_BUILD"] = False


# ═══════════════════════════════════════════════════════════════════════════
# Dataset cache — loading 16 MB of prices per request is not a plan
# ═══════════════════════════════════════════════════════════════════════════

_datasets: dict[str, Dataset] = {}
_dataset_lock = threading.Lock()


def load_dataset(price_id: str, fundamentals_id: str | None = None) -> Dataset:
    key = f"{price_id}|{fundamentals_id or ''}"
    with _dataset_lock:
        cached = _datasets.get(key)
        if cached is not None:
            return cached

    path = (DATA_DIR / price_id).resolve()
    if not str(path).startswith(str(DATA_DIR.resolve())):
        abort(400, "dataset path escapes the data directory")
    if not path.is_file():
        abort(400, f"no price file named {price_id!r} in data/")
    dataset = load_prices(path, name=Path(price_id).stem)

    if fundamentals_id:
        fpath = (DATA_DIR / fundamentals_id).resolve()
        if not str(fpath).startswith(str(DATA_DIR.resolve())):
            abort(400, "fundamentals path escapes the data directory")
        dataset = attach_fundamentals(dataset, fpath)

    with _dataset_lock:
        _datasets[key] = dataset
    return dataset


def load_writeup(key: str) -> str | None:
    """Render `research/strategies/<key>.md` to HTML, if it exists.

    The write-up is optional — not every strategy needs a page-length
    explanation — so a missing file is not an error, just an empty section.
    """
    path = WRITEUPS_DIR / f"{key}.md"
    if not path.is_file():
        return None
    return md.render(path.read_text(encoding="utf-8"))


def writeup_path(key: str) -> str:
    """Repo-relative path of a strategy's write-up, whether or not it exists.

    Shown on the page so the Research section reads as a view of a file
    somebody can open and edit, rather than prose the GUI invented.
    """
    return f"research/strategies/{key}.md"


def default_dataset() -> str:
    """The price file a form starts on: the largest one in `data/`.

    Largest, not first alphabetically — the useful default is the full
    universe rather than whatever one-off `run.py fetch` last cached.
    """
    files = catalog()
    if not files:
        return ""
    return max(files, key=lambda d: d["size_mb"])["id"]


def public_strategy(cls) -> dict[str, Any]:
    """`Strategy.describe()` minus its parameters.

    The framework's own description carries the `Param` table — the CLI's
    `list` command prints it and `lab.api.sweep` enumerates it. The web layer
    is the one consumer that must never see it, so the key is dropped here,
    once, rather than being left to each template to not render. That way a
    parameter cannot reach a page by someone adding a loop to a template, and
    it cannot reach `/api/strategies` either.
    """
    described = cls.describe()
    described.pop("params", None)
    return described


def public_strategies() -> list[dict[str, Any]]:
    return [public_strategy(cls) for cls in all_strategies().values()]


def write_run(kind: str, label: str, result: dict[str, Any],
              request_payload: dict[str, Any] | None = None) -> str:
    """Persist a finished result in the same shape a browser-triggered job
    writes, so it opens at `/run/<id>` regardless of how it was produced.

    Used by `python run.py backtest --save-run`: the CLI does the work and
    the GUI reads it back. No HTTP request is involved in producing the
    result, only in viewing it afterward.
    """
    job_id = uuid.uuid4().hex[:12]
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": job_id, "kind": kind, "label": label,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "done", "progress": 1.0, "stage": "complete", "error": "",
        "request": request_payload, "result": result,
    }
    (RUNS_DIR / f"{job_id}.json").write_text(
        json.dumps(payload, default=str), encoding="utf-8")
    return job_id


def load_all_runs(limit: int = 200) -> list[dict[str, Any]]:
    """Every persisted run, newest first, in full — not the projected fields
    `/api/runs` sends over the wire. `home`, `strategy_page` and `showcase`
    all read this rather than re-globbing `runs/`.
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    paths = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime,
                   reverse=True)[:limit]
    for path in paths:
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def subject_of(run: dict[str, Any]) -> str | None:
    """The strategy a run was *about*, as opposed to the ones it compared against.

    The run form posts the page's own strategy first and the checked controls
    after it, and the hub keeps that order, so the subject is the first sleeve.
    `request` is preferred where it exists because it is the actual instruction;
    sleeve order is the fallback for runs saved before requests were kept.
    """
    request = run.get("request") or {}
    spec = (request.get("strategies") or [None])[0]
    if isinstance(spec, dict):
        spec = spec.get("key")
    if isinstance(spec, str) and spec:
        return spec
    sleeves = (run.get("result") or {}).get("sleeves") or []
    return sleeves[0].get("key") if sleeves else None


def runs_for_strategy(key: str, runs: list[dict[str, Any]],
                      limit: int = 8) -> list[dict[str, Any]]:
    """Backtests this strategy was the subject of, newest first.

    Subject, not merely present. Runs are one strategy now, but older
    multi-sleeve runs are still on disk, and a membership test put every run
    that used a strategy as a control onto that strategy's page.
    """
    out = []
    for run in runs:
        if run.get("kind") != "backtest" or run.get("status") != "done":
            continue
        if subject_of(run) == key:
            out.append(run)
        if len(out) >= limit:
            break
    return out


def resolve_universe(dataset: Dataset, mode: str, raw: str,
                     limit: int = 2000) -> Dataset:
    """Narrow a dataset to the universe a form asked for.

    Every failure here is a *user* error with a specific cause — a ticker that
    is not in the file, an empty box — so each one is raised as a sentence
    naming the cause. `Dataset.for_universe` raises a bare
    `KeyError: 'none of [...] have prices'`, which reached the browser as a
    failed job with a stack-trace string in it and told nobody anything.
    """
    if mode == "sp500":
        # SPY is deliberately absent from every other price file — it is the
        # benchmark, not a universe member, so "every symbol in the price
        # file" never quietly includes it (see `Dataset` docstring). Trading
        # the index itself means loading it as its own price file instead,
        # which `data/market_spy.csv` already is: committed, and priced back
        # to 2005 with nothing to fetch.
        if MARKET_SYMBOL not in dataset.symbols:
            raise ValueError(
                f"the S&P 500 universe trades {MARKET_SYMBOL} itself — pick "
                f"{MARKET_FILE!r} under Data, not a stock price file")
        symbols = [MARKET_SYMBOL]
    elif mode == "all":
        symbols = list(dataset.symbols)[:limit]
    elif mode == "fundamentals":
        symbols = [s for s in dataset.symbols if s in dataset.fundamentals][:limit]
        if not symbols:
            raise ValueError(
                "that price file has no fundamentals attached — pick a "
                "fundamentals file above, or choose a different universe")
    else:
        symbols = [s.strip().upper() for s in raw.replace("\n", ",").split(",")
                   if s.strip()]
        if not symbols:
            raise ValueError("no symbols given — name at least one ticker")

        known = set(dataset.symbols)
        missing = [s for s in symbols if s not in known]
        if len(missing) == len(symbols):
            raise ValueError(
                f"none of {', '.join(symbols[:8])} are in this price file "
                f"({len(known):,} tickers, e.g. "
                f"{', '.join(list(dataset.symbols)[:5])}). Check the spelling, "
                f"or download them with `python run.py fetch`.")
        if missing:
            # A partial miss is survivable — the hub reports the dropped names
            # in its warnings — but a pair strategy consumes symbols two at a
            # time, so silently dropping one re-pairs everything after it.
            symbols = [s for s in symbols if s in known]

    return dataset.for_universe(symbols)


# ═══════════════════════════════════════════════════════════════════════════
# Jobs
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Job:
    id: str
    kind: str                       # "backtest" | "sweep"
    label: str
    created: str
    status: str = "queued"          # queued | running | done | failed
    progress: float = 0.0
    stage: str = ""
    error: str = ""
    result: dict[str, Any] | None = field(default=None, repr=False)
    #: The exact `/api/run` body that produced this job — kept so
    #: `subject_of()` can identify which strategy a run is about without
    #: guessing from sleeve order.
    request: dict[str, Any] | None = field(default=None, repr=False)

    def public(self, with_result: bool = False) -> dict[str, Any]:
        out = {"id": self.id, "kind": self.kind, "label": self.label,
               "created": self.created, "status": self.status,
               "progress": round(self.progress, 3), "stage": self.stage,
               "error": self.error, "request": self.request}
        if with_result and self.result is not None:
            out["result"] = self.result
        return out


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _new_job(kind: str, label: str,
            request_payload: dict[str, Any] | None = None) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label,
              created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
              request=request_payload)
    with _jobs_lock:
        _jobs[job.id] = job
    return job


def _persist(job: Job) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    payload = job.public(with_result=True)
    (RUNS_DIR / f"{job.id}.json").write_text(
        json.dumps(payload, default=str), encoding="utf-8")


def _load_job(job_id: str) -> Job | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is not None:
        return job
    path = RUNS_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    job = Job(id=data["id"], kind=data.get("kind", "backtest"),
              label=data.get("label", ""), created=data.get("created", ""),
              status=data.get("status", "done"), progress=1.0,
              result=data.get("result"), request=data.get("request"))
    with _jobs_lock:
        _jobs[job_id] = job
    return job


def _run_async(job: Job, work) -> None:
    def target() -> None:
        job.status = "running"
        try:
            def progress(fraction: float, stage: str) -> None:
                job.progress, job.stage = fraction, stage
            job.result = work(progress)
            job.status = "done"
            job.progress = 1.0
            job.stage = "complete"
            _persist(job)
        except Exception as exc:                              # noqa: BLE001
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.stage = "failed"
            app.logger.error("job %s failed\n%s", job.id, traceback.format_exc())
    threading.Thread(target=target, daemon=True, name=f"job-{job.id}").start()


# ═══════════════════════════════════════════════════════════════════════════
# Request parsing
# ═══════════════════════════════════════════════════════════════════════════

def build_config(form: dict[str, Any]) -> RunConfig:
    scenario = form.get("scenario", "realistic")
    base = SCENARIOS.get(scenario, SCENARIOS["realistic"])
    costs = CostModel(
        commission_bps=float(form.get("commission_bps", base.commission_bps)),
        slippage_bps=float(form.get("slippage_bps", base.slippage_bps)),
        latency=base.latency,
        timing=FillTiming(form.get("timing", FillTiming.NEXT_OPEN.value)),
        seed=int(form.get("seed", 7)))
    return RunConfig(
        starting_cash=float(form.get("starting_cash", 100_000)),
        costs=costs,
        max_position_weight=float(form.get("max_position_weight", 1.0)),
        label=form.get("label", ""))


def build_strategies(keys: list[Any]):
    """Instantiate the named strategies at their declared defaults.

    Deliberately takes keys and nothing else. An earlier version accepted a
    parameter dict per strategy, which is what let the browser tune one; the
    endpoint refusing to carry parameters at all is what makes "parameters
    live in the file" a property of the system rather than a convention the
    front end is trusted to keep.
    """
    out = []
    for item in keys:
        # A dict with a stray "params" key is an old client (or an old saved
        # request being rerun). Take the key, drop the rest.
        key = item.get("key") if isinstance(item, dict) else item
        if not key:
            # Skipping an empty key here would run the *rest* of the list and
            # return a plausible-looking result for a request that named a
            # strategy the client failed to send. That shipped once, as a
            # backtest of nothing but its own control.
            raise ValueError(f"missing strategy key in {item!r}")
        out.append(build(str(key)))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Pages
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/")
def home():
    return render_template("home.html", strategies=public_strategies(),
                           universes=[u.value for u in Universe])


@app.route("/strategy/<key>")
def strategy_page(key: str):
    try:
        cls = get(key)
    except KeyError:
        abort(404)

    described = public_strategy(cls)
    source = described["source_path"]
    runs = runs_for_strategy(key, load_all_runs())
    available = catalog()

    # A strategy may declare the price file it is meant for. Honour it only if
    # that file is actually in `data/` — a declaration pointing at something
    # nobody downloaded should fall back to the usual default, not select an
    # option that does not exist and silently post an empty dataset id.
    wanted = described.get("default_data") or ""
    chosen = (wanted if any(d["id"] == wanted for d in available)
              else default_dataset())

    return render_template(
        "strategy.html",
        strategy=described,
        # The absolute path is the one you can paste into an editor, and this
        # server only ever runs on the machine holding the file.
        source_abs=str(ROOT / source) if source else "",
        module_path=source[:-3].replace("/", ".") if source.endswith(".py") else "",
        class_name=cls.__name__,
        others=[s for s in public_strategies() if s["key"] != key],
        datasets=available,
        chosen_dataset=chosen,
        market_file=MARKET_FILE,
        market_symbol=MARKET_SYMBOL,
        fundamentals=fundamentals_catalog(),
        scenarios=sorted(SCENARIOS),
        writeup=load_writeup(key),
        writeup_path=writeup_path(key),
        runs=[{k: r.get(k) for k in ("id", "label", "created")} for r in runs])


@app.route("/docs")
def docs_page():
    """How to run a strategy from the code.

    Deliberately not in the nav: it is reference, read once, and a nav slot
    spends attention every page load on something needed on almost none of
    them. Each strategy page links to it from its code card, which is where
    the question comes up.
    """
    return render_template("docs.html")


@app.route("/showcase")
def showcase():
    strategies = public_strategies()
    runs = load_all_runs()

    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        if run.get("kind") != "backtest" or run.get("status") != "done":
            continue
        # Subject, not merely present. Featuring a strategy's appearance as
        # somebody else's control linked the card to that other run — clicking
        # one strategy opened another one's result page.
        key = subject_of(run)
        if not key or key in latest:
            continue
        result = run.get("result") or {}
        sleeves = result.get("sleeves") or []
        sleeve = next((s for s in sleeves if s.get("key") == key), None)
        if sleeve is None:
            continue
        # A run saved before alpha existed has no alpha to show, and a card of
        # dashes reads as broken rather than as old.
        if (sleeve.get("performance") or {}).get("alpha") is None:
            continue
        latest[key] = {"sleeve": sleeve, "benchmark": result.get("benchmark"),
                       "run_id": run["id"], "created": run["created"]}

    rows = [{"strategy": s, "featured": latest.get(s["key"])} for s in strategies]
    return render_template("showcase.html", rows=rows)


def comparison_options() -> list[dict[str, Any]]:
    """Other strategies whose latest run can be drawn on this one's chart.

    Comparison moved out of the pre-run form and onto the result: choosing what
    to hold a finished curve against is a question you ask while looking at it,
    and answering it in advance meant every run carried extra sleeves that were
    not what the run was about.
    """
    titles = {s["key"]: s["title"] for s in public_strategies()}
    seen: dict[str, dict[str, Any]] = {}
    for run in load_all_runs():
        if run.get("kind") != "backtest" or run.get("status") != "done":
            continue
        key = subject_of(run)
        if not key or key in seen or key not in titles:
            continue
        seen[key] = {"key": key, "title": titles[key], "run_id": run["id"],
                     "created": run.get("created", "")}
    return list(seen.values())


@app.get("/api/curve/<job_id>")
def api_curve(job_id: str):
    """One finished run's equity curve, for drawing on another run's chart."""
    job = _load_job(job_id)
    if job is None or job.status != "done" or not job.result:
        abort(404)
    sleeves = job.result.get("sleeves") or []
    if not sleeves:
        abort(404)
    sleeve = sleeves[0]
    return jsonify({
        "label": sleeve.get("title") or job.label,
        "equity": sleeve.get("equity") or [],
        "run_id": job_id,
    })


@app.route("/run/<job_id>")
def run_page(job_id: str):
    job = _load_job(job_id)
    if job is None:
        abort(404)
    if job.kind == "sweep":
        # Sweeps used to render here. They now live entirely in the terminal,
        # because a sweep result *is* a table of parameter values and this
        # interface does not show those. Old sweep files may still be sitting
        # in runs/, so say what happened rather than 500 on a missing template.
        return render_template(
            "error.html", code=410,
            message="Parameter sweeps run in code now — `python -m "
                    "lab.strategies.<name>` — and print to the terminal."), 410
    key = subject_of({"request": job.request, "result": job.result})
    return render_template("result.html", job=job.public(), job_id=job_id,
                           comparisons=comparison_options(),
                           writeup=load_writeup(key) if key else None,
                           writeup_path=writeup_path(key) if key else None)


# ═══════════════════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/strategies")
def api_strategies():
    return jsonify(public_strategies())


@app.get("/api/datasets")
def api_datasets():
    return jsonify({"prices": catalog(), "fundamentals": fundamentals_catalog()})


@app.get("/api/dataset/<path:dataset_id>")
def api_dataset(dataset_id: str):
    dataset = load_dataset(dataset_id)
    info = dataset.describe()
    info["sample_symbols"] = list(dataset.symbols[:40])
    return jsonify(info)


@app.post("/api/run")
def api_run():
    payload = request.get_json(force=True, silent=True) or {}
    strategies_spec = payload.get("strategies") or []
    if not strategies_spec:
        return jsonify({"error": "pick at least one strategy"}), 400
    # A run is about one strategy. Comparisons are drawn on the result's chart
    # from other runs, so a run no longer carries sleeves it is not about.
    if len(strategies_spec) > 1:
        return jsonify({"error": "a run is one strategy — compare on the "
                                 "result page instead"}), 400

    # Everything that can be wrong with the *request* is settled here, before
    # a job exists: a mistyped ticker should come back as a sentence under the
    # Run button, not as a result page that says "failed" thirty seconds later.
    # Only the backtest itself — the part that is genuinely slow — is deferred
    # to the thread. The dataset is cached, and the page has already warmed it
    # by calling /api/dataset on load, so this costs nothing after the first
    # request.
    try:
        strategies = build_strategies(strategies_spec)
        config = build_config(payload)
        dataset = load_dataset(payload.get("dataset") or default_dataset(),
                               payload.get("fundamentals") or None)
        dataset = resolve_universe(dataset,
                                   payload.get("universe_mode", "symbols"),
                                   payload.get("symbols", ""))
        if payload.get("start") or payload.get("end"):
            dataset = dataset.between(payload.get("start") or None,
                                      payload.get("end") or None)
        if len(dataset) < 2:
            raise ValueError(
                "that date range leaves fewer than two bars to trade")
    except (TypeError, ValueError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400

    label = payload.get("label") or ", ".join(s.title for s in strategies)
    job = _new_job("backtest", label, request_payload=payload)

    def work(progress):
        return Hub(dataset, strategies, config).run(progress).to_dict()

    _run_async(job, work)
    return jsonify(job.public()), 202


@app.post("/api/strategies/new")
def api_new_strategy():
    """Scaffold a strategy file and its `__init__.py` import line.

    Mirrors AGENTS.md's "one file, one import line" by hand. It does not
    register the class in this process — see `registry.scaffold` — so the
    strategies screen reports the path and asks for a restart rather than
    pretending the strategy is live.
    """
    payload = request.get_json(force=True, silent=True) or {}
    try:
        path = scaffold(payload.get("key", ""), payload.get("title", ""),
                        payload.get("universe", "single"),
                        payload.get("summary", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        shown = str(path.relative_to(ROOT))
    except ValueError:
        shown = str(path)
    return jsonify({"key": payload.get("key", "").strip(),
                    "path": shown.replace("\\", "/")}), 201


@app.get("/api/job/<job_id>")
def api_job(job_id: str):
    job = _load_job(job_id)
    if job is None:
        return jsonify({"error": "no such job"}), 404
    want_result = job.status == "done" and request.args.get("result") != "0"
    return jsonify(job.public(with_result=want_result))


@app.get("/api/runs")
def api_runs():
    return jsonify([{k: r.get(k) for k in
                     ("id", "kind", "label", "created", "status")}
                    for r in load_all_runs(limit=50)])


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404,
                           message="That page does not exist."), 404


@app.errorhandler(500)
def server_error(_):
    return render_template("error.html", code=500,
                           message="Something broke on the server."), 500


def main(host: str = "127.0.0.1", port: int = 5000, debug: bool = False) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    # Templates re-read on every render, even without --debug. This server
    # only ever runs on localhost next to the files it renders, and the
    # alternative is editing a template, refreshing, and seeing the old page
    # with no indication why. Python changes still need a restart; --debug
    # adds the reloader for those.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main(debug=True)
