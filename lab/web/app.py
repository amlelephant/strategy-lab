"""
lab/web/app.py
==============
The Flask front end.

Three screens, one question each — the discipline is borrowed from minFit and
it is what keeps a research tool from turning into a dashboard:

    /                 What do I want to test?
    /run/<id>         Did it work?
    /strategy/<key>   What is this algorithm claiming?

Runs happen on a background thread with a job id, because a five-strategy pass
over 1,700 symbols takes the better part of a minute and a page that blocks on
that is a page nobody uses. Finished runs are written to `runs/<id>.json`, so a
result outlives the process that produced it — a result you cannot link someone
to is not really a result.

The server holds no strategy-specific code. Every form on every page is
generated from `Param` declarations, so a new strategy appears here the moment
it is registered.
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

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   url_for)

from ..core.costs import SCENARIOS, CostModel, FillTiming
from ..core.hub import Hub, RunConfig
from ..core.registry import all_strategies, build, describe_all, get
from ..core.sweep import run_sweep
from ..data.dataset import Dataset
from ..data.loaders import (DATA_DIR, attach_fundamentals, catalog,
                            fundamentals_catalog, load_prices, synthetic)

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "runs"

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_SORT_KEYS"] = False


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

    if price_id == "synthetic":
        dataset = synthetic(("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"), bars=900)
    else:
        path = (DATA_DIR / price_id).resolve()
        if not str(path).startswith(str(DATA_DIR.resolve())):
            abort(400, "dataset path escapes the data directory")
        dataset = load_prices(path, name=Path(price_id).stem)

    if fundamentals_id:
        fpath = (DATA_DIR / fundamentals_id).resolve()
        if not str(fpath).startswith(str(DATA_DIR.resolve())):
            abort(400, "fundamentals path escapes the data directory")
        dataset = attach_fundamentals(dataset, fpath)

    with _dataset_lock:
        _datasets[key] = dataset
    return dataset


def resolve_universe(dataset: Dataset, mode: str, raw: str,
                     limit: int = 2000) -> Dataset:
    """Narrow a dataset to the universe a form asked for."""
    if mode == "all":
        symbols = list(dataset.symbols)[:limit]
    elif mode == "fundamentals":
        symbols = [s for s in dataset.symbols if s in dataset.fundamentals][:limit]
        if not symbols:
            abort(400, "that price dataset has no fundamentals attached")
    else:
        symbols = [s.strip().upper() for s in raw.replace("\n", ",").split(",")
                   if s.strip()]
        if not symbols:
            abort(400, "no symbols given")
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

    def public(self, with_result: bool = False) -> dict[str, Any]:
        out = {"id": self.id, "kind": self.kind, "label": self.label,
               "created": self.created, "status": self.status,
               "progress": round(self.progress, 3), "stage": self.stage,
               "error": self.error}
        if with_result and self.result is not None:
            out["result"] = self.result
        return out


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _new_job(kind: str, label: str) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label,
              created=datetime.now(timezone.utc).isoformat(timespec="seconds"))
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
              result=data.get("result"))
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


def build_strategies(spec: list[dict[str, Any]]):
    out = []
    for item in spec:
        key = item.get("key")
        if not key:
            continue
        params = {k: v for k, v in (item.get("params") or {}).items()
                  if v not in ("", None)}
        out.append(build(key, params))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Pages
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/")
def console():
    with _jobs_lock:
        recent = sorted(_jobs.values(), key=lambda j: j.created, reverse=True)[:8]
    return render_template(
        "console.html",
        strategies=describe_all(),
        datasets=[{"id": "synthetic", "label": "synthetic (generated)",
                   "size_mb": 0.0}] + catalog(),
        fundamentals=fundamentals_catalog(),
        scenarios=sorted(SCENARIOS),
        recent=[j.public() for j in recent])


@app.route("/strategy/<key>")
def strategy_page(key: str):
    try:
        cls = get(key)
    except KeyError:
        abort(404)
    return render_template("strategy.html", strategy=cls.describe())


@app.route("/run/<job_id>")
def run_page(job_id: str):
    job = _load_job(job_id)
    if job is None:
        abort(404)
    template = "sweep.html" if job.kind == "sweep" else "result.html"
    return render_template(template, job=job.public(), job_id=job_id)


# ═══════════════════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/strategies")
def api_strategies():
    return jsonify(describe_all())


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

    try:
        strategies = build_strategies(strategies_spec)
        config = build_config(payload)
    except (TypeError, ValueError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400

    label = payload.get("label") or ", ".join(s.title for s in strategies)
    job = _new_job("backtest", label)

    def work(progress):
        dataset = load_dataset(payload.get("dataset", "synthetic"),
                               payload.get("fundamentals") or None)
        dataset = resolve_universe(dataset,
                                   payload.get("universe_mode", "symbols"),
                                   payload.get("symbols", ""))
        if payload.get("start") or payload.get("end"):
            dataset = dataset.between(payload.get("start") or None,
                                      payload.get("end") or None)
        result = Hub(dataset, strategies, config).run(progress)
        return result.to_dict()

    _run_async(job, work)
    return jsonify(job.public()), 202


@app.post("/api/sweep")
def api_sweep():
    payload = request.get_json(force=True, silent=True) or {}
    key = payload.get("key")
    if not key:
        return jsonify({"error": "no strategy given"}), 400
    try:
        get(key)
        config = build_config(payload)
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    overrides = {}
    for name, values in (payload.get("overrides") or {}).items():
        if isinstance(values, str):
            values = [v.strip() for v in values.split(",") if v.strip()]
        if values:
            overrides[name] = values

    job = _new_job("sweep", f"{key} sweep")

    def work(progress):
        dataset = load_dataset(payload.get("dataset", "synthetic"),
                               payload.get("fundamentals") or None)
        dataset = resolve_universe(dataset,
                                   payload.get("universe_mode", "symbols"),
                                   payload.get("symbols", ""))
        if payload.get("start") or payload.get("end"):
            dataset = dataset.between(payload.get("start") or None,
                                      payload.get("end") or None)
        return run_sweep(dataset, key, overrides=overrides or None,
                         config=config, progress=progress).to_dict()

    _run_async(job, work)
    return jsonify(job.public()), 202


@app.get("/api/job/<job_id>")
def api_job(job_id: str):
    job = _load_job(job_id)
    if job is None:
        return jsonify({"error": "no such job"}), 404
    want_result = job.status == "done" and request.args.get("result") != "0"
    return jsonify(job.public(with_result=want_result))


@app.get("/api/runs")
def api_runs():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for path in sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:50]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({k: data.get(k) for k in
                    ("id", "kind", "label", "created", "status")})
    return jsonify(out)


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
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main(debug=True)
