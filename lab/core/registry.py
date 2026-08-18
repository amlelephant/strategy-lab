"""
lab/core/registry.py
====================
The list of known strategies.

Adding a strategy to this platform is one file and one decorator:

    @register
    class MyThing(Strategy):
        key = "my_thing"
        ...

Import it from `lab/strategies/__init__.py` and it appears in the CLI, as a
card in the GUI with its own page, in the sweep runner and in the test suite,
with no other edit anywhere. That is the whole point of the registry — the
cost of trying a new idea should be the idea, not the plumbing around it.

`scaffold()` at the bottom does those two steps mechanically, for the "new
strategy" form on the home screen.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Type

from .contract import Strategy, Universe

_REGISTRY: dict[str, Type[Strategy]] = {}

#: lab/strategies/ — where a scaffolded strategy file and its registering
#: import line get written. A module-level constant so tests can point it at
#: a throwaway directory instead of the real one.
STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"

_KEY_RE = re.compile(r"[a-z][a-z0-9_]*")

_UNIVERSE_MEMBERS = {u.value: u.name for u in Universe}

_TEMPLATE = '''\
"""
lab/strategies/{key}.py
{underline}
TODO: replace this docstring once the decision rule is written — one sentence
on what edge it claims, and what would falsify it.

Scaffolded from the strategies screen. Read AGENTS.md before writing
`on_bar`; AGENTS.md section 2 is a complete strategy you can copy.
"""

from __future__ import annotations

from ..core.contract import (HOLD, MarketContext, Order, Param, ParamKind,
                             Side, Strategy, Universe)
from ..core.registry import register


@register
class {class_name}(Strategy):

    key = "{key}"
    title = "{title}"
    universe = Universe.{universe_member}
    summary = "{summary}"

    params = (
        # Param("threshold", 2.0, ParamKind.FLOAT, low=0.0, high=10.0,
        #       step=0.25, help="What this knob does.", grid=(1.5, 2.0, 2.5)),
    )

    def on_bar(self, ctx: MarketContext):
        # TODO: read the world from `ctx` and return orders.
        return HOLD


# Run this file directly to test it — `python -m lab.strategies.{key}`.
# This is where parameters get chosen: the GUI has no control for one, so a
# value you want to try goes here, or in the `params` block above.
if __name__ == "__main__":
    from ..api import backtest, sweep                          # noqa: F401

    backtest({class_name}, symbols="KO,PEP,XOM,CVX")

    # sweep({class_name}, symbols="KO,PEP,XOM,CVX",
    #       threshold=[1.5, 2.0, 2.5])
'''


def register(cls: Type[Strategy]) -> Type[Strategy]:
    """Class decorator. Requires a unique, non-empty `key`."""
    key = getattr(cls, "key", "")
    if not key:
        raise ValueError(f"{cls.__name__} must set a class-level `key`")
    if key in _REGISTRY and _REGISTRY[key] is not cls:
        # `python -m lab.strategies.thing` executes that file twice: once as
        # `lab.strategies.thing`, when the package imports it, and again as
        # `__main__`. The second pass builds a second, identical class object
        # and would collide with the first. Keeping the original registration
        # is right — `build(key)` must return one canonical class — and it is
        # what makes a strategy file runnable on its own, which is where
        # parameters are chosen. A genuine collision between two different
        # files still raises, because neither of them is `__main__`.
        if cls.__module__ == "__main__":
            return cls
        raise ValueError(
            f"duplicate strategy key {key!r}: {cls.__name__} collides with "
            f"{_REGISTRY[key].__name__}. Keys appear in saved results and in "
            f"URLs, so they must be unique and stable.")
    _REGISTRY[key] = cls
    return cls


def get(key: str) -> Type[Strategy]:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"no strategy {key!r}; known: {sorted(_REGISTRY)}") from None


def build(key: str, params: dict | None = None) -> Strategy:
    """Instantiate by key, coercing parameters from strings if needed.

    This is what the web layer calls with an HTML form's contents.
    """
    cls = get(key)
    return cls(**(params or {}))


def all_strategies() -> dict[str, Type[Strategy]]:
    return dict(sorted(_REGISTRY.items()))


def describe_all() -> list[dict]:
    return [cls.describe() for cls in all_strategies().values()]


def __iter__() -> Iterator[Type[Strategy]]:      # pragma: no cover
    return iter(_REGISTRY.values())


def scaffold(key: str, title: str, universe: str, summary: str,
            strategies_dir: Path | None = None) -> Path:
    """Write a new strategy file and wire its import into `__init__.py`.

    This is AGENTS.md's "one file, one import line" by hand, done by the
    strategies screen instead of a text editor. It does not register the
    class in the running process — the decorator runs at import time, and
    this process already finished importing `lab.strategies` — so the new
    key appears once the server restarts (or reloads, under `--debug`).
    """
    strategies_dir = strategies_dir or STRATEGIES_DIR
    key = key.strip()
    if not _KEY_RE.fullmatch(key):
        raise ValueError(
            "key must be snake_case, starting with a letter "
            "(e.g. 'my_strategy')")
    if key in _REGISTRY:
        raise ValueError(f"strategy {key!r} already exists")

    universe_member = _UNIVERSE_MEMBERS.get(universe)
    if universe_member is None:
        raise ValueError(f"universe must be one of {sorted(_UNIVERSE_MEMBERS)}")

    target = strategies_dir / f"{key}.py"
    if target.exists():
        raise ValueError(f"{target} already exists")

    class_name = "".join(word.capitalize() for word in key.split("_"))
    heading = f"lab/strategies/{key}.py"
    target.write_text(_TEMPLATE.format(
        key=key, underline="=" * len(heading), class_name=class_name,
        title=title.strip() or key, universe_member=universe_member,
        summary=(summary.strip() or "What edge is this claiming?")
                .replace('"', "'")), encoding="utf-8")

    init_path = strategies_dir / "__init__.py"
    new_line = f"from .{key} import {class_name}\n"
    lines = init_path.read_text(encoding="utf-8").splitlines(keepends=True)
    import_lines = [i for i, line in enumerate(lines) if line.startswith("from .")]
    insert_at = next((i for i in import_lines if lines[i] > new_line),
                     (import_lines[-1] + 1 if import_lines else len(lines)))
    lines.insert(insert_at, new_line)
    init_path.write_text("".join(lines), encoding="utf-8")

    return target
