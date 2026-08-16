"""
tests/test_web.py
=================
The rendered pages, checked without a browser.

The one test that earns its place here is `test_no_number_input_rejects_its_own_value`.
`<input type="number">` derives its step base from `min`, so `min=1000` with
`step=10000` silently makes `value=100000` invalid and the browser refuses the
field before anything is submitted. That shipped twice — once in a generated
field and once in a hand-written one — and neither the Python tests nor a
glance at the template would catch it, because the markup looks perfectly
reasonable.

Parsing the real HTML catches both sources at once, and keeps catching them
when someone adds a field by hand later.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from lab.web.app import app

#: Matches one <input …> tag, however its attributes are wrapped across lines.
_INPUT = re.compile(r"<input\b[^>]*>", re.IGNORECASE | re.DOTALL)
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


def test_pages_render(client):
    assert client.get("/").status_code == 200
    assert client.get("/strategy/stat_arb").status_code == 200
    assert client.get("/strategy/not_a_strategy").status_code == 404
    assert client.get("/api/strategies").status_code == 200


def test_no_number_input_rejects_its_own_value(client):
    """Every numeric field must accept the value it is rendered with.

    Browsers accept `min + k·step` only. A field whose default is not an
    exact number of steps above its minimum shows "please enter a valid
    value" on a form the user has not touched.
    """
    html = client.get("/").get_data(as_text=True)
    inputs = _number_inputs(html)
    assert inputs, "the console rendered no number inputs — did the form move?"

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
    html = client.get("/").get_data(as_text=True)
    for attrs in _number_inputs(html):
        value = attrs.get("value")
        if not value:
            continue
        name = attrs.get("data-param") or attrs.get("name")
        if attrs.get("min"):
            assert Decimal(value) >= Decimal(attrs["min"]), f"{name} below min"
        if attrs.get("max"):
            assert Decimal(value) <= Decimal(attrs["max"]), f"{name} above max"


def test_every_strategy_gets_a_form_and_a_details_page(client):
    from lab import all_strategies

    html = client.get("/").get_data(as_text=True)
    for key, cls in all_strategies().items():
        assert f'value="{key}"' in html, f"{key} is missing from the console"
        assert client.get(f"/strategy/{key}").status_code == 200
        for param in cls.params:
            assert f'data-param="{param.name}"' in html, (
                f"{key}.{param.name} has no control on the console")


def test_run_rejects_an_empty_strategy_list(client):
    response = client.post("/api/run", json={"strategies": []})
    assert response.status_code == 400


def test_run_rejects_an_unknown_parameter(client):
    response = client.post("/api/run", json={
        "dataset": "synthetic", "symbols": "AAA,BBB",
        "strategies": [{"key": "stat_arb", "params": {"nonsense": "1"}}]})
    assert response.status_code == 400


def test_dataset_path_cannot_escape_the_data_directory(client):
    response = client.get("/api/dataset/..%2f..%2fsecrets.csv")
    assert response.status_code in (400, 404)
