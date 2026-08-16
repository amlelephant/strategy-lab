"""
lab/core/registry.py
====================
The list of known strategies.

Adding a strategy to this platform is one file and one decorator:

    @register
    class MyThing(Strategy):
        key = "my_thing"
        ...

Import it from `lab/strategies/__init__.py` and it appears in the CLI, in the
GUI's dropdown, in the sweep runner and in the test suite, with no other edit
anywhere. That is the whole point of the registry — the cost of trying a new
idea should be the idea, not the plumbing around it.
"""

from __future__ import annotations

from typing import Iterator, Type

from .contract import Strategy

_REGISTRY: dict[str, Type[Strategy]] = {}


def register(cls: Type[Strategy]) -> Type[Strategy]:
    """Class decorator. Requires a unique, non-empty `key`."""
    key = getattr(cls, "key", "")
    if not key:
        raise ValueError(f"{cls.__name__} must set a class-level `key`")
    if key in _REGISTRY and _REGISTRY[key] is not cls:
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
