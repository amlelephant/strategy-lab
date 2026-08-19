"""
lab/web/freeze.py
==================
Render the read-only pages to static files, for hosting where nothing can run
— a GitHub Pages mirror, in particular.

What this can and cannot do:

  * **Frozen**: `/`, `/showcase`, `/docs`, every `/strategy/<key>`, and
    `/run/<id>` for every finished backtest already on disk — plus the
    `/api/job/<id>` and `/api/curve/<id>` JSON those result pages fetch
    client-side. This is everything a reader needs to see the platform, the
    strategies, and results already run.
  * **Not frozen**: starting a job, polling one, scaffolding a new strategy
    file. There is no server behind a static host to do any of that against.
    `app.config["STATIC_BUILD"]` tells the templates to drop those controls
    rather than render ones that would silently fail — see `strategy.html`
    and `home.html`.

Every route is written as `<path>/index.html` (or, for the JSON endpoints,
`<path>/index.html` too — the browser follows the same directory redirect a
`fetch()` does, and `.json()` does not care what the server claimed the
content-type was). That is the one URL shape every static host — GitHub
Pages included — resolves the same way, so nothing here needs a redirects
file or a list of rewrite rules.

Re-run whenever a new strategy or a new run is worth publishing; there is no
incremental mode; a fresh run every time is what keeps a frozen copy from
quietly drifting from the app that produced it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _write(out_dir: Path, url_path: str, body: bytes) -> Path:
    """`url_path` becomes `<out_dir><url_path>/index.html`, `/` included."""
    segments = [s for s in url_path.split("/") if s]
    target = out_dir.joinpath(*segments) / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return target


def _crawl(client, out_dir: Path, url_path: str) -> bool:
    response = client.get(url_path)
    if response.status_code != 200:
        print(f"  skip  {url_path}  ({response.status_code})")
        return False
    _write(out_dir, url_path, response.get_data())
    print(f"  write {url_path}")
    return True


def freeze(out_dir: Path) -> None:
    # Imported here, not at module level: importing `lab.web.app` registers
    # routes and opens the dataset cache, which a CLI that never freezes has
    # no reason to pay for.
    from ..core.registry import all_strategies
    from .app import RUNS_DIR, app, load_all_runs, subject_of

    app.config["STATIC_BUILD"] = True
    client = app.test_client()

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    pages: list[str] = ["/", "/showcase", "/docs"]
    pages += [f"/strategy/{key}" for key in all_strategies()]

    run_ids: list[str] = []
    for run in load_all_runs():
        if run.get("kind") != "backtest" or run.get("status") != "done":
            continue
        if not subject_of(run):
            continue
        run_ids.append(run["id"])
        pages.append(f"/run/{run['id']}")

    api: list[str] = []
    api += [f"/api/job/{rid}" for rid in run_ids]
    api += [f"/api/curve/{rid}" for rid in run_ids]

    print(f"Freezing {len(pages)} pages and {len(api)} API responses to "
         f"{out_dir}/\n")
    written = sum(_crawl(client, out_dir, p) for p in pages)
    written += sum(_crawl(client, out_dir, p) for p in api)

    if STATIC_DIR.is_dir():
        shutil.copytree(STATIC_DIR, out_dir / "static")
        print(f"  copy  static/ -> {out_dir}/static/")

    app.config["STATIC_BUILD"] = False
    print(f"\n{written}/{len(pages) + len(api)} routes written. "
         f"Serve locally with:\n\n  python -m http.server -d {out_dir} 8000\n")
