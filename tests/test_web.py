"""
tests/test_web.py
=================
The rendered pages, checked without a browser.

Two tests here earn their place by catching things a reading of the template
cannot.

`test_no_number_input_rejects_its_own_value` — `<input type="number">` derives
its step base from `min`, so `min=1000` with `step=10000` silently makes
`value=100000` invalid and the browser refuses the field before anything is
submitted. That shipped twice, and the markup looks perfectly reasonable both
times.

`test_no_parameter_is_reachable_through_the_interface` — the design rule that
a parameter lives in its strategy's file and nowhere else is only worth
anything if it stays true. It is one careless `{% for p in strategy.params %}`
away from being false, so the test walks every form control on every page and
fails if any of them is bound to a parameter name.
"""

from __future__ import annotations

import re
import time
from decimal import Decimal

import pytest

from lab.data.loaders import DATA_DIR
from lab.web.app import app

#: The web layer has no generated-data path any more — a run goes against a
#: real price file or it does not go. Tests that actually execute a backtest
#: therefore need `data/prices.pkl`, which is gitignored, so they skip rather
#: than fail on a fresh clone. Everything that only renders a page still runs.
PRICES = "prices.pkl"
needs_prices = pytest.mark.skipif(
    not (DATA_DIR / PRICES).is_file(),
    reason=f"needs data/{PRICES} — run `python run.py fetch` to create one")
#: Four liquid names that are in the file and are also a sane pair universe.
SYMBOLS = "KO,PEP,XOM,CVX"

#: Matches one <input …> tag, however its attributes are wrapped across lines.
_INPUT = re.compile(r"<input\b[^>]*>", re.IGNORECASE | re.DOTALL)
#: Any form control, for the parameter sweep-through below.
_CONTROL = re.compile(r"<(?:input|select|textarea)\b[^>]*>",
                      re.IGNORECASE | re.DOTALL)
_ATTR = re.compile(r'([a-zA-Z_:][-\w:.]*)\s*=\s*"([^"]*)"')


@pytest.fixture(scope="module")
def client():
    app.config.update(TESTING=True)
    return app.test_client()


def _number_inputs(html: str) -> list[dict[str, str]]:
    out = []
    for tag in _INPUT.findall(html):
        attrs = dict(_ATTR.findall(tag))
        if attrs.get("type") == "number":
            out.append(attrs)
    return out


def _every_page(client) -> dict[str, str]:
    from lab import all_strategies

    pages = {path: client.get(path).get_data(as_text=True)
             for path in ("/", "/showcase", "/docs")}
    for key in all_strategies():
        pages[f"/strategy/{key}"] = client.get(
            f"/strategy/{key}").get_data(as_text=True)
    return pages


def test_pages_render(client):
    assert client.get("/").status_code == 200
    assert client.get("/showcase").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/strategy/stat_arb_ev").status_code == 200
    assert client.get("/strategy/not_a_strategy").status_code == 404
    assert client.get("/api/strategies").status_code == 200
    assert client.get("/live").status_code == 404      # removed, stays removed


def test_the_docs_page_is_linked_but_not_in_the_nav(client):
    """Reference material, read once — a nav slot would spend attention on
    every page load for something needed on almost none of them."""
    nav = re.search(r"<nav[^>]*>.*?</nav>",
                    client.get("/").get_data(as_text=True), re.S)
    assert nav, "no nav on the page"
    assert "/docs" not in nav.group(0)

    # But reachable from where the question actually comes up.
    page = client.get("/strategy/stat_arb_ev").get_data(as_text=True)
    assert "/docs" in page


def test_a_strategy_page_lists_only_runs_it_was_the_subject_of(client):
    """Runs used to carry control sleeves, so a membership test put nearly
    every run in the repository on the control's page — and put runs where a
    strategy was only a control on that strategy's page. Runs are one strategy
    now, but old multi-sleeve runs are still on disk and must resolve the same
    way."""
    from lab.web.app import runs_for_strategy, subject_of

    runs = [
        {"id": "1", "kind": "backtest", "status": "done",
         "request": {"strategies": ["mean_reversion", "stat_arb"]},
         "result": {"sleeves": [{"key": "mean_reversion"},
                                {"key": "stat_arb"}]}},
        # No request kept: fall back to sleeve order.
        {"id": "2", "kind": "backtest", "status": "done",
         "result": {"sleeves": [{"key": "bw_valuation"},
                                {"key": "stat_arb"}]}},
        {"id": "3", "kind": "backtest", "status": "failed",
         "request": {"strategies": ["mean_reversion"]},
         "result": {"sleeves": [{"key": "mean_reversion"}]}},
    ]

    assert subject_of(runs[0]) == "mean_reversion"
    assert subject_of(runs[1]) == "bw_valuation"

    # Subject of run 1 only; run 3 failed and never counts.
    assert [r["id"] for r in runs_for_strategy("mean_reversion", runs)] == ["1"]
    # Present in run 1 as a control, but the subject only of run 2.
    assert [r["id"] for r in runs_for_strategy("bw_valuation", runs)] == ["2"]
    # Only ever a control.
    assert runs_for_strategy("stat_arb", runs) == []


def test_no_parameter_is_reachable_through_the_interface(client):
    """No form control anywhere is bound to a strategy parameter.

    Checks controls rather than raw text on purpose: a research write-up is
    free to *discuss* `sma_window` in prose, and that is the write-up doing
    its job. What must not exist is a field that sets one.
    """
    from lab import all_strategies

    params = {p.name for cls in all_strategies().values() for p in cls.params}
    assert params, "no strategy declares a parameter — has the contract moved?"

    offenders = []
    for path, html in _every_page(client).items():
        for tag in _CONTROL.findall(html):
            attrs = dict(_ATTR.findall(tag))
            bound = {attrs.get("name"), attrs.get("data-param"),
                     attrs.get("id")} & params
            if bound:
                offenders.append(f"{path}: {sorted(bound)}")
    assert not offenders, (
        "these pages expose a control for a strategy parameter:\n  "
        + "\n  ".join(offenders))


def test_the_strategy_api_does_not_serve_parameters(client):
    """`Strategy.describe()` carries the `Param` table for the CLI and the
    sweeper. The web layer strips it, so no client of this server can render
    one even if it wanted to."""
    payload = client.get("/api/strategies").get_json()
    assert payload, "no strategies served"
    for entry in payload:
        assert "params" not in entry, f"{entry['key']} still ships its params"
        assert entry["source_path"], (
            f"{entry['key']} has no source path — the page cannot point at "
            f"the file where its parameters actually live")


def _await_job(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    data = {}
    while time.monotonic() < deadline:
        data = client.get(f"/api/job/{job_id}").get_json()
        if data["status"] in ("done", "failed"):
            return data
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


@needs_prices
def test_a_single_strategy_job_persists_its_own_request(client, tmp_path, monkeypatch):
    """`subject_of()` reads `job.request` to find which strategy a run is
    about, falling back to sleeve order only for runs saved before requests
    were kept. A job that cannot describe the request that produced it can
    still be shown, but not correctly attributed to its strategy's page."""
    import sys
    # Not `import lab.web.app as web_app`: `lab/web/__init__.py` does
    # `from .app import app`, which rebinds the *package* attribute
    # `lab.web.app` to the Flask instance, so a dotted `import ... as`
    # would silently monkeypatch the wrong object. `sys.modules` is not
    # affected by that shadowing.
    web_app = sys.modules["lab.web.app"]
    monkeypatch.setattr(web_app, "RUNS_DIR", tmp_path)   # don't litter runs/

    spec = {"dataset": PRICES, "symbols": SYMBOLS,
            "strategies": [{"key": "mean_reversion", "params": {}}]}
    response = client.post("/api/run", json=spec)
    assert response.status_code == 202
    job_id = response.get_json()["id"]

    data = _await_job(client, job_id)
    assert data["status"] == "done"
    assert data["request"]["strategies"] == spec["strategies"]
    assert len(data["result"]["sleeves"]) == 1


def test_no_number_input_rejects_its_own_value(client):
    """Every numeric field must accept the value it is rendered with.

    Browsers accept `min + k·step` only. A field whose default is not an
    exact number of steps above its minimum shows "please enter a valid
    value" on a form the user has not touched.
    """
    html = client.get("/strategy/stat_arb_ev").get_data(as_text=True)
    inputs = _number_inputs(html)
    assert inputs, "the strategy screen rendered no number inputs — did the form move?"

    broken = []
    for attrs in inputs:
        value, step = attrs.get("value"), attrs.get("step")
        if value in (None, "") or step in (None, "", "any"):
            continue
        # No `min` means the step base is the value itself, which is always
        # consistent. With a `min`, the grid starts there.
        base = attrs.get("min")
        if base in (None, ""):
            continue
        offset = (Decimal(value) - Decimal(base)) / Decimal(step)
        if offset != offset.to_integral_value():
            broken.append(
                f"{attrs.get('data-param') or attrs.get('name')}: "
                f"value={value} is {offset} steps above min={base} "
                f"(step={step})")

    assert not broken, (
        "these fields reject their own default value:\n  "
        + "\n  ".join(broken))


def test_number_inputs_stay_inside_their_bounds(client):
    html = client.get("/strategy/stat_arb_ev").get_data(as_text=True)
    for attrs in _number_inputs(html):
        value = attrs.get("value")
        if not value:
            continue
        name = attrs.get("data-param") or attrs.get("name")
        if attrs.get("min"):
            assert Decimal(value) >= Decimal(attrs["min"]), f"{name} below min"
        if attrs.get("max"):
            assert Decimal(value) <= Decimal(attrs["max"]), f"{name} above max"


def test_every_strategy_is_a_card_that_opens_its_own_backtest(client):
    """Home is a list of cards; each one opens a page that can run that
    strategy and says where its file is."""
    from lab import all_strategies

    home_html = client.get("/").get_data(as_text=True)
    for key, cls in all_strategies().items():
        assert f'href="/strategy/{key}"' in home_html, f"{key} is missing from home"

        response = client.get(f"/strategy/{key}")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert cls.source_path() in html, (
            f"{key} does not show the path to its own file")
        assert 'id="run-form"' in html, f"{key} has no backtest form"
        assert f'data-key="{key}"' in html, (
            f"{key}'s form does not name the strategy it runs")


def test_run_rejects_an_empty_strategy_list(client):
    response = client.post("/api/run", json={"strategies": []})
    assert response.status_code == 400


@needs_prices
def test_run_ignores_parameters_sent_by_a_client(client, tmp_path, monkeypatch):
    """The endpoint takes strategy keys and nothing else.

    A payload carrying parameters is not an error — an old saved request
    being rerun looks exactly like one — but the values are dropped, and the
    strategy runs at the defaults its file declares. Rejecting the request
    would break rerun; honouring it would put a parameter back in the
    browser's hands.
    """
    import sys
    web_app = sys.modules["lab.web.app"]
    monkeypatch.setattr(web_app, "RUNS_DIR", tmp_path)

    response = client.post("/api/run", json={
        "dataset": PRICES, "symbols": SYMBOLS,
        "strategies": [{"key": "mean_reversion",
                        "params": {"sma_window": 3, "nonsense": "1"}}]})
    assert response.status_code == 202

    data = _await_job(client, response.get_json()["id"])
    assert data["status"] == "done", data.get("error")
    from lab import get
    declared = {p.name: p.default for p in get("mean_reversion").params}
    assert data["result"]["sleeves"][0]["params"]["sma_window"] == \
        declared["sma_window"]


def test_a_run_is_one_strategy(client):
    """Comparison moved to the result page, so a run carries no extra sleeves.

    Runs used to bake in whatever was ticked under "Compare against", which put
    sleeves in a result that the run was not about and made a strategy's own
    page list runs belonging to something else."""
    response = client.post("/api/run", json={
        "dataset": PRICES, "symbols": SYMBOLS,
        "strategies": ["mean_reversion", "stat_arb"]})
    assert response.status_code == 400
    assert "one strategy" in response.get_json()["error"]


def test_the_prerun_form_has_no_comparison_control(client):
    """It is chosen after the fact now, on the chart."""
    page = client.get("/strategy/stat_arb_ev").get_data(as_text=True)
    assert 'name="compare"' not in page


def test_run_rejects_an_unknown_strategy(client):
    response = client.post("/api/run", json={
        "dataset": PRICES, "symbols": SYMBOLS,
        "strategies": ["no_such_strategy"]})
    assert response.status_code == 400


def test_run_rejects_a_missing_strategy_key(client):
    """A null in the list is a client that failed to send its subject.

    Dropping it and running the remainder produces a result page that looks
    fine and answers a question nobody asked — which is exactly what happened
    when `form.dataset.key` was shadowed by a `<select name="dataset">` and
    every run silently became a backtest of the control alone.
    """
    for spec in ([None], [{"params": {}}], [""]):
        response = client.post("/api/run", json={
            "dataset": PRICES, "symbols": SYMBOLS, "strategies": spec})
        assert response.status_code == 400, f"{spec!r} was accepted"


def test_dataset_path_cannot_escape_the_data_directory(client):
    response = client.get("/api/dataset/..%2f..%2fsecrets.csv")
    assert response.status_code in (400, 404)


@needs_prices
def test_unknown_tickers_are_explained_not_raised(client):
    """A ticker that is not in the price file must come back as a sentence.

    This is the regression test for the bug that shipped: the form defaulted
    to symbols that were not in the selected dataset, `Dataset.for_universe`
    raised a bare `KeyError: 'none of [...] have prices'` inside the worker
    thread, and the browser showed a result page reading "The run failed" with
    a repr in it. A user error must be answered, not raised.
    """
    response = client.post("/api/run", json={
        "dataset": PRICES, "symbols": "NOTATICKER,ALSONOTREAL",
        "strategies": ["mean_reversion"]})
    assert response.status_code == 400
    message = response.get_json()["error"]
    assert "NOTATICKER" in message
    assert "KeyError" not in message and "Traceback" not in message
    # and it says what to do about it
    assert "fetch" in message or "spelling" in message


@needs_prices
def test_an_empty_or_impossible_universe_is_rejected_up_front(client):
    for payload, expect in (
        ({"symbols": "", "universe_mode": "symbols"}, "no symbols"),
        ({"symbols": SYMBOLS, "universe_mode": "fundamentals"}, "fundamentals"),
        ({"symbols": SYMBOLS, "start": "2030-01-01", "end": "2030-12-31"}, "bars"),
    ):
        response = client.post("/api/run", json={
            "dataset": PRICES, "strategies": ["mean_reversion"], **payload})
        assert response.status_code == 400, payload
        assert expect in response.get_json()["error"].lower(), payload


def test_a_strategy_can_declare_the_data_and_universe_it_is_for(client):
    """A market-timing rule must not open on four consumer-staples names.

    `default_data` / `default_symbols` seed the form. They are run inputs, not
    parameters — the page stays free to change them — so they are allowed
    where a `Param` is not.
    """
    from lab import all_strategies

    declaring = {key: cls for key, cls in all_strategies().items()
                 if cls.default_symbols}
    assert declaring, "no strategy declares a default universe any more"

    for key, cls in declaring.items():
        html = client.get(f"/strategy/{key}").get_data(as_text=True)
        area = re.search(r'<textarea name="symbols".*?</textarea>', html, re.S)
        assert area, f"{key}: no symbols control"
        assert cls.default_symbols in area.group(0), (
            f"{key}: form does not open on its declared universe")


def test_a_declared_default_never_smuggles_a_parameter_into_the_form(client):
    """The defaults are data and tickers. They must not name a `Param`."""
    from lab import all_strategies

    for key, cls in all_strategies().items():
        names = {p.name for p in cls.params}
        assert cls.default_data not in names, key
        assert cls.default_symbols not in names, key


@needs_prices
def test_an_unavailable_declared_dataset_falls_back(client, monkeypatch):
    """A declaration pointing at a file nobody downloaded must not select an
    option that is not in the list — that posts an empty dataset id."""
    import sys

    # `from lab.web import app` is the Flask object, not this module.
    web = sys.modules["lab.web.app"]
    monkeypatch.setattr(web, "catalog", lambda: [
        {"id": "prices.pkl", "label": "prices", "path": "x", "size_mb": 1.0}])
    html = client.get("/strategy/hundred_day_mov_avg").get_data(as_text=True)
    select = re.search(r'<select name="dataset".*?</select>', html, re.S)
    assert select and "market_spy" not in select.group(0)
    assert select.group(0).count("selected") == 1


@needs_prices
def test_no_dataset_option_is_generated_data(client):
    """The dropdown lists real price files and nothing else.

    A backtest against a simulated series measures the simulation. The
    generator that used to back a `synthetic` option now lives in `tests/`
    and cannot be reached from the app at all.
    """
    html = client.get("/strategy/stat_arb_ev").get_data(as_text=True)
    select = re.search(r'<select name="dataset".*?</select>', html, re.S)
    assert select, "no dataset control on the strategy page"
    assert "synthetic" not in select.group(0).lower()
    assert "generated" not in select.group(0).lower()

    response = client.post("/api/run", json={
        "dataset": "synthetic", "symbols": SYMBOLS,
        "strategies": ["mean_reversion"]})
    assert response.status_code == 400


@needs_prices
def test_a_real_backtest_reports_alpha_against_the_benchmark(client, tmp_path,
                                                             monkeypatch):
    """End-to-end through the web layer, on real prices: the thing a user
    actually does. The result must carry the numbers the page leads with."""
    import sys
    monkeypatch.setattr(sys.modules["lab.web.app"], "RUNS_DIR", tmp_path)

    response = client.post("/api/run", json={
        "dataset": PRICES, "symbols": SYMBOLS,
        "strategies": ["mean_reversion"]})
    assert response.status_code == 202, response.get_json()

    data = _await_job(client, response.get_json()["id"], timeout=60)
    assert data["status"] == "done", data.get("error")

    sleeves = data["result"]["sleeves"]
    assert [s["key"] for s in sleeves] == ["mean_reversion"]
    for sleeve in sleeves:
        p = sleeve["performance"]
        for field in ("alpha", "alpha_t", "beta", "information_ratio",
                      "sharpe", "sharpe_t", "observations"):
            assert p[field] is not None, f"{sleeve['key']} has no {field}"
        assert p["observations"] > 100


def test_scaffold_writes_a_strategy_file_and_one_import_line(client, tmp_path, monkeypatch):
    """The strategies screen's whole job is AGENTS.md's "one file, one import
    line" done mechanically. This runs against a throwaway directory — never
    the real `lab/strategies/` — so a failing assertion can't leave the repo
    dirty."""
    import lab.core.registry as registry

    init_path = tmp_path / "__init__.py"
    init_path.write_text("from .stat_arb import StatArb\n"
                         "from .stat_arb import StatArb\n", encoding="utf-8")
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path)

    path = registry.scaffold("my_new_thing", "My New Thing", "single",
                             "One sentence about the edge.")
    assert path == tmp_path / "my_new_thing.py"
    body = path.read_text(encoding="utf-8")
    assert 'key = "my_new_thing"' in body
    assert "class MyNewThing(Strategy):" in body

    init_text = init_path.read_text(encoding="utf-8")
    assert "from .my_new_thing import MyNewThing\n" in init_text
    # the two lines already there are untouched
    assert "from .stat_arb import StatArb" in init_text
    assert "from .stat_arb import StatArb" in init_text


def test_scaffold_rejects_a_duplicate_or_malformed_key(client, tmp_path, monkeypatch):
    import lab.core.registry as registry

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path)

    with pytest.raises(ValueError):
        registry.scaffold("Not Snake Case", "Bad Key", "single", "")
    with pytest.raises(ValueError):
        registry.scaffold("mean_reversion", "Already Registered", "single", "")


def test_api_new_strategy_endpoint(client, tmp_path, monkeypatch):
    import lab.core.registry as registry

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    # `app.py` imported the `scaffold` function itself, not the module, but
    # a function reads module globals — including `STRATEGIES_DIR` — from
    # wherever it was defined, so patching it here is enough either way.
    monkeypatch.setattr(registry, "STRATEGIES_DIR", tmp_path)

    response = client.post("/api/strategies/new", json={
        "key": "another_thing", "title": "Another Thing", "universe": "pair",
        "summary": "Trades a spread.",
    })
    assert response.status_code == 201
    body = response.get_json()
    assert body["key"] == "another_thing"
    assert (tmp_path / "another_thing.py").exists()

    response = client.post("/api/strategies/new", json={
        "key": "", "title": "", "universe": "single", "summary": "",
    })
    assert response.status_code == 400
