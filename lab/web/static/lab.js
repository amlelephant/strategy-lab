/* ─────────────────────────────────────────────────────────────────────────
   strategy-lab — front end

   Charts are hand-rolled SVG rather than a charting library, for the same
   reason minFit hand-rolls its graphs: a chart library brings its own visual
   opinions, and reconciling those with a design system costs more than
   drawing two polylines. There is no build step and no dependency; the whole
   page works offline.
   ───────────────────────────────────────────────────────────────────────── */

const Lab = (() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const SVG_NS = "http://www.w3.org/2000/svg";

  /* ── formatting ─────────────────────────────────────────────────── */

  const pct = (v, digits = 2) =>
    v === null || v === undefined || !isFinite(v) ? "—"
      : `${(v * 100).toFixed(digits)}%`;
  const signedPct = (v, digits = 2) =>
    v === null || v === undefined || !isFinite(v) ? "—"
      : `${v > 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
  const num = (v, digits = 2) =>
    v === null || v === undefined || !isFinite(v) ? "—" : v.toFixed(digits);
  const money = (v) =>
    v === null || v === undefined || !isFinite(v) ? "—"
      : `$${Math.round(v).toLocaleString()}`;
  const shortDate = (iso) => String(iso).slice(0, 10);

  function toneFor(value) {
    if (value === null || value === undefined || !isFinite(value)) return "";
    return value > 0 ? "up" : value < 0 ? "down" : "";
  }

  /* The one verdict, written once and used on every screen that shows a
     result. It leads with the plain comparison against the benchmark — the
     S&P 500 — because that is the number a reader can check against the chart.

     Alpha is secondary and never the headline. Divided by a small beta it is a
     large number for anything that made money without market exposure: a
     market-neutral book returning 3% while the index returned 16% has beta
     0.00 and therefore alpha +3%, and leading with that announced a win on a
     result that lost by thirteen points a year. Mirrors
     `Performance.verdict()`; change both together. */
  function alphaVerdict(p, { badge = true } = {}) {
    const active = p.active_return;
    if (active === null || active === undefined) {
      return "No benchmark to compare against.";
    }
    const against = p.benchmark_label || "the benchmark";
    const plain = `<strong>${signedPct(active, 2)} a year</strong> vs ${against}`;

    if (p.alpha_t === null || p.alpha === null) {
      return plain + ".";
    }
    const riskAdj = `${signedPct(p.alpha, 2)} against its ${num(p.beta, 2)} beta
      (t = ${num(p.alpha_t, 1)})`;

    if (!(active > 0)) {
      let tail = `Risk-adjusted, ${riskAdj}.`;
      if (p.beta !== null && p.beta < 0.8 && p.alpha > 0) {
        tail += " That measures how little market exposure it carried, not a"
              + " better return.";
      }
      return `${plain} — <strong>did not beat the market</strong>. ${tail}` +
        (badge ? ' <span class="badge bad">trails the market</span>' : "");
    }
    if (p.alpha_t > 2 && p.alpha > 0) {
      return `${plain} — <strong>beat the market</strong>, and ${riskAdj}
        survives adjusting for risk.` +
        (badge ? ' <span class="badge good">beats the market</span>' : "");
    }
    return `${plain} — ahead of the market, but ${riskAdj} is
      <strong>not distinguishable</strong> from luck.` +
      (badge ? ' <span class="badge warn">inconclusive</span>' : "");
  }

  function el(tag, attrs = {}, text) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* ── charts ─────────────────────────────────────────────────────── */

  const PALETTE = () => {
    const style = getComputedStyle(document.documentElement);
    return {
      accent: style.getPropertyValue("--accent").trim(),
      success: style.getPropertyValue("--success").trim(),
      danger: style.getPropertyValue("--danger").trim(),
      warn: style.getPropertyValue("--warn").trim(),
      tertiary: style.getPropertyValue("--text-tertiary").trim(),
    };
  };

  /* Series order is meaningful: the first series is the subject and gets the
     accent, later ones step through the state colours. Nothing is coloured
     for decoration. */
  function seriesColour(i) {
    const p = PALETTE();
    return [p.accent, p.warn, p.success, p.danger, p.tertiary][i % 5];
  }

  /**
   * Draw one or more time series as polylines.
   * @param {SVGElement} svg
   * @param {Array<{label:string, points:Array<[string,number]>, dashed?:boolean,
   *                colour?:string, fill?:boolean}>} series
   * @param {{format?:function, baseline?:number, height?:number}} opts
   */
  function drawChart(svg, series, opts = {}) {
    const format = opts.format || money;
    const width = Math.max(320, svg.clientWidth || svg.parentElement.clientWidth);
    const height = opts.height || svg.getAttribute("height") || 300;
    const pad = { top: 14, right: 58, bottom: 24, left: 10 };

    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("height", height);
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    const live = series.filter((s) => s.points && s.points.length > 1);
    if (!live.length) {
      svg.appendChild(el("text", {
        x: width / 2, y: height / 2, "text-anchor": "middle",
        class: "axis-label",
      }, "no data"));
      return null;
    }

    const times = live[0].points.map((p) => Date.parse(p[0]));
    const tMin = Math.min(...live.map((s) => Date.parse(s.points[0][0])));
    const tMax = Math.max(...live.map((s) => Date.parse(s.points[s.points.length - 1][0])));
    let vMin = Infinity, vMax = -Infinity;
    for (const s of live) for (const [, v] of s.points) {
      if (v < vMin) vMin = v;
      if (v > vMax) vMax = v;
    }
    if (opts.baseline !== undefined) {
      vMin = Math.min(vMin, opts.baseline);
      vMax = Math.max(vMax, opts.baseline);
    }
    const span = vMax - vMin || 1;
    vMin -= span * 0.06;
    vMax += span * 0.06;
    /* Drawdown passes clampMax: 0 — headroom above the running peak is not a
       thing that exists, and an axis that offers +2% drawdown is a lie. */
    if (opts.clampMax !== undefined) vMax = Math.min(vMax, opts.clampMax);
    if (opts.clampMin !== undefined) vMin = Math.max(vMin, opts.clampMin);

    const x = (t) => pad.left + ((t - tMin) / (tMax - tMin || 1)) *
      (width - pad.left - pad.right);
    const y = (v) => pad.top + (1 - (v - vMin) / (vMax - vMin)) *
      (height - pad.top - pad.bottom);

    /* horizontal guides — four, quiet, with the value on the right */
    for (let i = 0; i <= 4; i++) {
      const v = vMin + ((vMax - vMin) * i) / 4;
      const yy = y(v);
      svg.appendChild(el("line", {
        x1: pad.left, x2: width - pad.right, y1: yy, y2: yy, class: "grid-line",
      }));
      svg.appendChild(el("text", {
        x: width - pad.right + 8, y: yy + 4, class: "axis-label",
      }, format(v)));
    }

    if (opts.baseline !== undefined) {
      const yy = y(opts.baseline);
      svg.appendChild(el("line", {
        x1: pad.left, x2: width - pad.right, y1: yy, y2: yy,
        stroke: PALETTE().tertiary, "stroke-width": 1,
      }));
    }

    live.forEach((s, i) => {
      const colour = s.colour || seriesColour(i);
      const path = s.points
        .map((p, k) => `${k ? "L" : "M"}${x(Date.parse(p[0])).toFixed(1)},${y(p[1]).toFixed(1)}`)
        .join(" ");
      if (s.fill) {
        const base = y(opts.baseline !== undefined ? opts.baseline : vMin);
        const first = x(Date.parse(s.points[0][0]));
        const last = x(Date.parse(s.points[s.points.length - 1][0]));
        svg.appendChild(el("path", {
          d: `${path} L${last.toFixed(1)},${base.toFixed(1)} L${first.toFixed(1)},${base.toFixed(1)} Z`,
          fill: colour, opacity: 0.12, class: "area",
        }));
      }
      svg.appendChild(el("path", {
        d: path, class: s.dashed ? "benchmark" : "series",
        stroke: s.dashed ? undefined : colour,
      }));
    });

    /* date ticks — three is enough to orient without becoming an axis */
    [0, 0.5, 1].forEach((f) => {
      const t = tMin + (tMax - tMin) * f;
      svg.appendChild(el("text", {
        x: x(t), y: height - 6, class: "axis-label",
        "text-anchor": f === 0 ? "start" : f === 1 ? "end" : "middle",
      }, new Date(t).toISOString().slice(0, 10)));
    });

    return { x, y, times, live, tMin, tMax, width, pad, height };
  }

  /* Scrub readout: hovering the chart reports the values under the cursor. */
  function attachScrub(svg, geom, readout, formats) {
    if (!geom || !readout) return;
    const cursor = el("line", { class: "cursor", y1: geom.pad.top,
      y2: geom.height - geom.pad.bottom, opacity: 0 });
    svg.appendChild(cursor);

    const render = (index, when) => {
      readout.innerHTML = geom.live.map((s, i) => {
        const point = s.points[Math.min(index, s.points.length - 1)];
        if (!point) return "";
        const fmt = formats[i] || money;
        return `<span><span style="color:${s.colour || seriesColour(i)}">&#9679;</span>
                ${s.label} <b>${fmt(point[1])}</b></span>`;
      }).join("") + `<span class="tertiary">${when}</span>`;
    };

    svg.addEventListener("mousemove", (event) => {
      const rect = svg.getBoundingClientRect();
      const px = ((event.clientX - rect.left) / rect.width) * geom.width;
      const t = geom.tMin + ((px - geom.pad.left) /
        (geom.width - geom.pad.left - geom.pad.right)) * (geom.tMax - geom.tMin);
      let best = 0, bestGap = Infinity;
      geom.times.forEach((tt, i) => {
        const gap = Math.abs(tt - t);
        if (gap < bestGap) { bestGap = gap; best = i; }
      });
      cursor.setAttribute("x1", geom.x(geom.times[best]));
      cursor.setAttribute("x2", geom.x(geom.times[best]));
      cursor.setAttribute("opacity", 1);
      render(best, new Date(geom.times[best]).toISOString().slice(0, 10));
    });
    svg.addEventListener("mouseleave", () => {
      cursor.setAttribute("opacity", 0);
      readout.innerHTML = "";
    });
  }

  /* ── theme ──────────────────────────────────────────────────────── */

  function initTheme() {
    const saved = localStorage.getItem("lab-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    const toggle = $("#theme-toggle");
    if (!toggle) return;
    toggle.addEventListener("click", (event) => {
      event.preventDefault();
      const now = document.documentElement.getAttribute("data-theme") === "dark"
        ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", now);
      localStorage.setItem("lab-theme", now);
      window.dispatchEvent(new Event("lab:theme"));
    });
  }

  /* ── polling ────────────────────────────────────────────────────── */

  function poll(jobId, onProgress, onDone, onFail) {
    let delay = 350;
    const tick = async () => {
      let job;
      try {
        const response = await fetch(`/api/job/${jobId}`);
        job = await response.json();
      } catch (err) {
        setTimeout(tick, 1500);
        return;
      }
      if (job.status === "done") { onDone(job); return; }
      if (job.status === "failed") { onFail(job); return; }
      onProgress(job);
      delay = Math.min(delay * 1.15, 2000);
      setTimeout(tick, delay);
    };
    tick();
  }

  function showProgress(job) {
    const bar = $("#progress-bar");
    if (bar) bar.style.width = `${Math.round((job.progress || 0) * 100)}%`;
    const stage = $("#progress-stage");
    if (stage) {
      stage.textContent = job.stage
        ? `${job.status} — ${job.stage}` : job.status;
    }
  }

  function showFailure(job) {
    $("#running").hidden = true;
    $("#failed").hidden = false;
    $("#error-text").textContent = job.error || "unknown error";
  }

  /* ═══════════════════════════════════════════════════════════════════
     Strategy page — the backtest form for exactly one strategy

     There is no parameter control anywhere in here, and there is no code
     that would render one. The form collects what belongs to a *run* — the
     data, the universe, the frictions — and the strategy itself arrives as
     a bare key.
     ═══════════════════════════════════════════════════════════════════ */

  function initStrategy() {
    initTheme();
    const form = $("#run-form");
    const runBtn = $("#run-btn");
    const status = $("#run-status");
    /* getAttribute, not `form.dataset.key`. A form exposes its own controls
       as named properties, and that named getter *overrides* built-ins — so
       with a `<select name="dataset">` on this page, `form.dataset` is the
       select element and `.key` on it is undefined. The run then posts a
       null strategy and silently backtests only the controls. */
    const key = form.getAttribute("data-key");

    const copyBtn = $("#copy-path");
    if (copyBtn) {
      copyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(copyBtn.dataset.path);
          copyBtn.textContent = "Copied";
        } catch (err) {
          /* Clipboard needs a secure context; selecting the text is the
             fallback that always works, and is what the user would do next
             anyway. */
          const range = document.createRange();
          range.selectNodeContents($("#source-path"));
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          copyBtn.textContent = "Selected";
        }
        setTimeout(() => { copyBtn.textContent = "Copy"; }, 1600);
      });
    }

    $$(".preset").forEach((button) => {
      button.addEventListener("click", () => {
        $("textarea[name=symbols]").value = button.dataset.symbols;
        $("#universe-mode").value = "symbols";
        $("#symbols-field").hidden = false;
      });
    });

    /* Sync on load as well as on change: a cross-sectional strategy renders
       with "every company with fundamentals" already selected, and the
       symbols box was still sitting there under it looking like it applied. */
    const universeMode = $("#universe-mode");
    const syncUniverse = () => {
      $("#symbols-field").hidden = universeMode.value !== "symbols";

      /* "Every company with fundamentals" against a fundamentals box reading
         "none" is a request that cannot succeed — the run is rejected before
         it starts. Rather than let the form offer a combination it will refuse,
         select the first fundamentals file and say so. */
      const fundamentals = form.fundamentals;
      const notice = $("#universe-notice");
      notice.hidden = true;
      if (universeMode.value === "fundamentals" && fundamentals
          && !fundamentals.value) {
        const first = Array.from(fundamentals.options).find((o) => o.value);
        if (first) {
          fundamentals.value = first.value;
          notice.hidden = false;
          notice.textContent =
            `This universe needs fundamentals, so ${first.textContent.split(" · ")[0]} was selected under Data.`;
        } else {
          notice.hidden = false;
          notice.textContent =
            "This universe needs a fundamentals file, and there are none in data/.";
        }
      } else if (universeMode.value === "all"
                 // Not `form.dataset.universe` — see the comment on `key`
                 // above, same collision, different control.
                 && form.getAttribute("data-universe") === "pair") {
        notice.hidden = false;
        notice.textContent =
          "Pairs are consumed two at a time in this order — every symbol in " +
          "the price file pairs alphabetically-adjacent tickers, not related " +
          "ones. Name specific pairs instead, or turn on the cointegration " +
          "screen and expect it to reject nearly everything it is given.";
      }
    };
    universeMode.addEventListener("change", syncUniverse);
    syncUniverse();

    $("#scenario").addEventListener("change", async (event) => {
      const presets = {
        frictionless: [0, 0], optimistic: [0.5, 0.5],
        realistic: [1, 2], conservative: [2, 8],
      };
      const preset = presets[event.target.value];
      if (preset) {
        form.commission_bps.value = preset[0];
        form.slippage_bps.value = preset[1];
      }
    });

    async function describeDataset() {
      const id = $("#dataset").value;
      const info = $("#dataset-info");
      info.textContent = "reading…";
      try {
        const response = await fetch(`/api/dataset/${encodeURIComponent(id)}`);
        const d = await response.json();
        info.textContent = `${d.symbols.toLocaleString()} symbols · ` +
          `${d.bars.toLocaleString()} bars · ${d.start} to ${d.end}` +
          (d.has_fundamentals ? ` · ${d.fundamental_symbols} with fundamentals` : "");
      } catch (err) {
        info.textContent = "could not read that dataset";
      }
    }
    $("#dataset").addEventListener("change", describeDataset);
    describeDataset();

    function payload() {
      const data = new FormData(form);
      /* One strategy. Comparison happens on the result page, against other
         runs, so a run no longer carries sleeves it is not about. */
      const strategies = [key];
      return {
        dataset: data.get("dataset"),
        fundamentals: data.get("fundamentals"),
        universe_mode: data.get("universe_mode"),
        symbols: data.get("symbols"),
        start: data.get("start"),
        end: data.get("end"),
        scenario: data.get("scenario"),
        commission_bps: data.get("commission_bps"),
        slippage_bps: data.get("slippage_bps"),
        timing: data.get("timing"),
        starting_cash: data.get("starting_cash"),
        seed: data.get("seed"),
        strategies: strategies,
      };
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      runBtn.disabled = true;
      status.className = "status";
      status.textContent = "starting…";
      try {
        const response = await fetch("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload()),
        });
        const job = await response.json();
        if (!response.ok) throw new Error(job.error || "the run was rejected");
        window.location.href = `/run/${job.id}`;
      } catch (err) {
        status.className = "status error";
        status.textContent = err.message;
        runBtn.disabled = false;
      }
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Result
     ═══════════════════════════════════════════════════════════════════ */

  function initResult(jobId) {
    initTheme();
    poll(jobId, showProgress, (job) => renderResult(job), showFailure);
  }

  function renderResult(job) {
    const data = job.result;
    $("#running").hidden = true;
    $("#result").hidden = false;

    const d = data.dataset;
    $("#run-subtitle").textContent =
      `${d.symbols} symbols · ${d.bars} bars · ${d.start} to ${d.end} · ` +
      `${data.config.costs.round_trip_bps} bps round trip · ` +
      `fills at ${data.config.costs.timing.replace("_", " ")} · ` +
      `${data.elapsed_seconds}s`;

    const warnings = $("#warnings");
    warnings.innerHTML = (data.warnings || []).map((w) =>
      `<div class="notice warn" style="margin-bottom:var(--s5)">
         <div><strong>Note.</strong> ${w}</div></div>`).join("");

    const sleeves = data.sleeves;
    const active = 0;                     // a run is one strategy

    /* What the equity chart is drawn against. Defaults to the market and
       resets to it on every load: the comparison is a lens, not a setting, and
       one that persisted would quietly change what a shared link shows. */
    let overlay = { label: data.benchmark.label,
                    points: data.benchmark.equity };
    const compare = $("#compare-with");
    const curves = new Map();             // run id → fetched equity

    /* How many rows of the trade log are in the DOM. A run can carry tens of
       thousands of fills — rendering all of them unasked would freeze the
       tab on load, so the page starts with a page of them and grows only on
       request. The data behind it is never truncated (see `_trade_rows` in
       `lab/core/hub.py`); this is a rendering limit, not a data limit. */
    let tradesShown = 500;

    if (compare) {
      compare.addEventListener("change", async () => {
        const choice = compare.value;
        if (choice === "market") {
          overlay = { label: data.benchmark.label, points: data.benchmark.equity };
        } else if (choice === "universe") {
          overlay = { label: (data.universe || {}).label || "Underlying assets",
                      points: (data.universe || {}).equity || [] };
        } else if (choice.startsWith("run:")) {
          const id = choice.slice(4);
          if (!curves.has(id)) {
            compare.disabled = true;
            try {
              const response = await fetch(`/api/curve/${id}`);
              curves.set(id, response.ok ? await response.json() : null);
            } catch (err) {
              curves.set(id, null);
            }
            compare.disabled = false;
          }
          const curve = curves.get(id);
          overlay = curve
            ? { label: curve.label, points: curve.equity }
            : { label: "unavailable", points: [] };
        }
        paint();
      });
    }

    function paint() {
      const s = sleeves[active];
      const p = s.performance;
      const b = data.benchmark.performance;

      /* The hero is the plain comparison. Alpha lives in a tile beside the
         beta that gives it meaning. */
      $("#hero-label").textContent =
        `Vs ${p.benchmark_label || "the benchmark"}, annualised`;
      $("#hero-active").textContent = signedPct(p.active_return, 2);
      $("#hero-active").className = "hero " + toneFor(p.active_return);
      $("#hero-verdict").innerHTML = alphaVerdict(p);

      $("#stat-return").textContent = signedPct(p.total_return);
      $("#stat-return").className = "value " + toneFor(p.total_return);
      $("#stat-return-sub").textContent =
        `${signedPct(p.cagr)} a year · benchmark ${signedPct(b.cagr)}`;

      /* Secondary, and never without its beta: alpha over a near-zero beta
         is large for anything that made money without market exposure. */
      $("#stat-alpha").textContent = signedPct(p.alpha, 2);
      $("#stat-alpha").className = "value " + toneFor(p.alpha);
      $("#stat-alpha-sub").textContent = p.alpha_t === null ? ""
        : `t = ${num(p.alpha_t, 1)}`;

      $("#stat-sharpe").textContent = num(p.sharpe);
      $("#stat-sharpe").className = "value " + toneFor(p.sharpe);
      /* The Sharpe never appears without its t and its n. It is the number
         most likely to be quoted out of context, and on its own it says
         nothing about whether the strategy beat owning the market. */
      /* Naming the risk-free rate here because a Sharpe computed over cash at
         0% is a different number from one computed over T-bills, and which of
         the two you are reading is not otherwise visible anywhere. */
      $("#stat-sharpe-sub").textContent =
        `t = ${num(p.sharpe_t, 1)} over ${(p.observations || 0).toLocaleString()} obs`
        + (p.risk_free_rate ? ` · excess of ${pct(p.risk_free_rate, 1)} cash` : "");

      $("#stat-beta").textContent = num(p.beta);
      $("#stat-beta-sub").textContent = p.information_ratio === null ? ""
        : `IR ${num(p.information_ratio, 2)}`;

      $("#stat-dd").textContent = pct(p.max_drawdown);
      $("#stat-dd-sub").textContent = p.max_drawdown_days
        ? `${Math.round(p.max_drawdown_days)} days underwater at worst` : "";

      /* equity: this strategy, plus whatever the dropdown is comparing it to */
      const equitySvg = $("#equity-chart");
      const geom = drawChart(equitySvg, [
        { label: s.title, points: s.equity },
        { label: overlay.label, points: overlay.points, dashed: true },
      ], { baseline: p.starting_equity });
      attachScrub(equitySvg, geom, $("#chart-readout"), [money, money]);

      const ends = overlay.points && overlay.points.length
        ? overlay.points[overlay.points.length - 1][1] / p.starting_equity - 1
        : null;
      $("#equity-legend").innerHTML = `
        <span class="item"><i class="swatch" style="background:${seriesColour(0)}"></i>
          ${s.title} — ${signedPct(p.total_return)}</span>
        <span class="item"><i class="swatch"
          style="background:var(--text-tertiary)"></i>
          ${overlay.label}${ends === null ? "" : ` — ${signedPct(ends)}`}</span>`;

      drawChart($("#dd-chart"), [
        { label: "Drawdown", points: s.drawdown, fill: true,
          colour: PALETTE().danger },
      ], { height: 160, baseline: 0, clampMax: 0, format: (v) => pct(v, 0) });

      /* trade log */
      const allRows = s.trades || [];
      const renderTrades = () => {
        const rows = allRows.slice(0, tradesShown);
        $("#trade-count").textContent = allRows.length
          ? (rows.length < allRows.length
              ? `${rows.length.toLocaleString()} of ${allRows.length.toLocaleString()} fills shown`
              : `${rows.length.toLocaleString()} fills shown`)
          : "no fills";
        $("#trade-table tbody").innerHTML = rows.map((t) => `
          <tr>
            <td class="secondary">${shortDate(t.timestamp)}</td>
            <td class="name">${t.symbol}</td>
            <td class="secondary">${t.action}</td>
            <td class="secondary">${t.side}</td>
            <td>${Number(t.quantity).toLocaleString()}</td>
            <td>${money(t.price)}</td>
            <td class="${toneFor(t.realised_pnl)}">${
              t.action === "close" ? money(t.realised_pnl) : "—"}</td>
            <td class="wrap">${t.reason || ""}</td>
          </tr>`).join("") ||
          `<tr><td colspan="8" class="empty">This strategy never traded.</td></tr>`;

        const moreBox = $("#trade-more-box");
        const moreBtn = $("#trade-more-btn");
        const remaining = allRows.length - rows.length;
        moreBox.hidden = remaining <= 0;
        if (remaining > 0) {
          moreBtn.textContent = `Load all ${allRows.length.toLocaleString()} fills`;
          moreBtn.onclick = () => { tradesShown = allRows.length; renderTrades(); };
        }
      };
      renderTrades();

      /* decisions */
      const lines = [
        ...(s.rejections || []).map(([t, m]) => ({ t, m, kind: "reject" })),
        ...(s.messages || []).map(([t, m]) => ({ t, m, kind: "note" })),
      ].sort((a, b) => a.t.localeCompare(b.t));
      $("#decision-log").innerHTML = lines.length
        ? lines.map((l) => `<div class="line">
             <span class="ts">${shortDate(l.t)}</span>
             <span class="${l.kind === "reject" ? "down" : ""}">${l.m}</span>
           </div>`).join("")
        : `<div class="empty">Nothing logged — this strategy took every
             signal it generated.</div>`;
    }

    /* comparison table always shows every sleeve */
    const rows = sleeves.map((s) => {
      const p = s.performance;
      const costs = (p.total_commission || 0) + (p.total_slippage || 0);
      return `<tr>
        <td class="name">${s.title}</td>
        <td class="${toneFor(p.alpha)}">${signedPct(p.alpha)}</td>
        <td class="secondary">${num(p.alpha_t, 1)}</td>
        <td class="secondary">${num(p.beta)}</td>
        <td class="${toneFor(p.total_return)}">${signedPct(p.total_return)}</td>
        <td>${signedPct(p.cagr)}</td>
        <td>${num(p.sharpe)} <span class="tertiary">(${num(p.sharpe_t, 1)})</span></td>
        <td>${pct(p.max_drawdown)}</td>
        <td>${(p.round_trips || 0).toLocaleString()}</td>
        <td>${p.hit_rate === null ? "—" : pct(p.hit_rate, 0)}</td>
        <td class="secondary">${money(costs)}</td>
      </tr>`;
    }).join("");
    const bench = data.benchmark.performance;
    $("#compare-table tbody").innerHTML = rows + `<tr>
      <td class="name secondary">${data.benchmark.label}, no costs</td>
      <td class="secondary">—</td>
      <td class="secondary">—</td>
      <td class="secondary">1.00</td>
      <td class="secondary">${signedPct(bench.total_return)}</td>
      <td class="secondary">${signedPct(bench.cagr)}</td>
      <td class="secondary">${num(bench.sharpe)} (${num(bench.sharpe_t, 1)})</td>
      <td class="secondary">${pct(bench.max_drawdown)}</td>
      <td class="secondary">—</td><td class="secondary">—</td>
      <td class="secondary">—</td></tr>`;

    /* configuration */
    $("#config-data").innerHTML = Object.entries(data.dataset)
      .map(([k, v]) => `<div class="line"><span class="ts">${k}</span>${v}</div>`)
      .join("");
    const costs = data.config.costs;
    $("#config-costs").innerHTML = [
      ["commission", `${costs.commission_bps} bps per side`],
      ["slippage", `${costs.slippage_bps} bps per side`],
      ["round trip", `${costs.round_trip_bps} bps per leg`],
      ["fills", costs.timing.replace("_", " ")],
      ["latency", costs.latency
        ? `${costs.latency.mean_ms}ms ± ${costs.latency.std_ms}ms` : "not modelled"],
      ["seed", costs.seed],
      ["starting cash", money(data.config.starting_cash)],
    ].map(([k, v]) => `<div class="line"><span class="ts">${k}</span>${v}</div>`)
      .join("");

    paint();
    let timer;
    const redraw = () => { clearTimeout(timer); timer = setTimeout(paint, 120); };
    window.addEventListener("resize", redraw);
    window.addEventListener("lab:theme", redraw);
  }

  /* ── live page ──────────────────────────────────────────────────── */

  function initLive(slots) {
    initTheme();
    slots.forEach((slot) => {
      const state = slot.state;
      const svg = $(`#chart-${slot.key}`);
      if (!state || !svg) return;

      const stratPoints = state.strategy.context_equity.concat(state.strategy.live_equity);
      const marketPoints = state.market.context_equity.concat(state.market.live_equity);
      if (stratPoints.length < 2) return;

      const series = [
        { label: slot.title, points: stratPoints },
        { label: "buy & hold (same universe)", points: marketPoints, dashed: true },
      ];
      const geom = drawChart(svg, series, { format: money });
      if (!geom) return;

      /* The marker is not a data series — it is where the tracked, live
         result begins. Left of it is backtest context fed so indicators are
         past warmup and the chart is not blank on day one; right of it is
         the only part any number on the page is computed from. */
      const inceptionMs = Date.parse(state.inception);
      if (inceptionMs >= geom.tMin) {
        // A slot that just went live has an inception date past the most
        // recent bar the market has posted — clamp to the right edge rather
        // than skip the marker, so "tracking starts here" still shows on
        // day zero instead of only appearing once a live bar exists.
        const ix = Math.min(geom.x(inceptionMs), geom.width - geom.pad.right);
        svg.appendChild(el("line", {
          x1: ix, x2: ix, y1: geom.pad.top, y2: geom.height - geom.pad.bottom,
          stroke: PALETTE().tertiary, "stroke-width": 1, "stroke-dasharray": "2 3",
        }));
        svg.appendChild(el("text", {
          x: ix - 4, y: geom.pad.top + 10, class: "axis-label",
          "text-anchor": "end",
        }, "live since " + state.inception));
      }

      const legend = $(`#legend-${slot.key}`);
      if (legend) {
        legend.innerHTML = series.map((s, i) => `<span class="item">
          <span class="swatch" style="background:${s.dashed ? PALETTE().tertiary : seriesColour(i)}"></span>
          ${s.label}</span>`).join("");
      }

      const readout = $(`#readout-${slot.key}`);
      if (readout) attachScrub(svg, geom, readout, series.map(() => money));
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Home — the strategy list, recent runs, and the scaffold form
     ═══════════════════════════════════════════════════════════════════ */

  function initHome() {
    initTheme();
    const form = $("#new-strategy-form");
    if (!form) return;
    const status = $("#new-strategy-status");
    const button = $("#new-strategy-btn");

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      button.disabled = true;
      status.textContent = "writing the file…";
      status.className = "caption secondary";
      const data = new FormData(form);
      try {
        const response = await fetch("/api/strategies/new", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            key: data.get("key"), title: data.get("title"),
            universe: data.get("universe"), summary: data.get("summary"),
          }),
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "could not scaffold that strategy");
        status.className = "caption up";
        status.innerHTML = `Written to <code class="mono">${body.path}</code> —
          restart the server to register it, then it appears everywhere.`;
        form.reset();
      } catch (err) {
        status.className = "caption down";
        status.textContent = err.message;
      } finally {
        button.disabled = false;
      }
    });
  }

  /* ═══════════════════════════════════════════════════════════════════
     Showcase — one card per registered strategy, its latest backtest
     ═══════════════════════════════════════════════════════════════════ */

  function initShowcase(keys) {
    initTheme();
    keys.forEach((key) => {
      const node = $(`#data-${key}`);
      if (!node) return;
      const featured = JSON.parse(node.textContent);
      const sleeve = featured.sleeve;
      const p = sleeve.performance;

      const stats = $(`#stats-${key}`);
      if (stats) {
        /* The t-statistics go in `.sub`, not inline in parentheses: at four
           tiles across a half-width card, an inline "(1.9)" wraps onto its
           own line and knocks the row out of alignment. */
        stats.innerHTML = `
          <div class="stat"><span class="overline">Vs market</span>
            <span class="value ${toneFor(p.active_return)}">${signedPct(p.active_return, 1)}</span>
            <span class="sub">a year</span></div>
          <div class="stat"><span class="overline">CAGR</span>
            <span class="value ${toneFor(p.cagr)}">${signedPct(p.cagr, 1)}</span>
            <span class="sub">alpha ${signedPct(p.alpha, 1)} · beta ${num(p.beta)}</span></div>
          <div class="stat"><span class="overline">Sharpe</span>
            <span class="value">${num(p.sharpe)}</span>
            <span class="sub">t = ${num(p.sharpe_t, 1)}</span></div>
          <div class="stat"><span class="overline">Max drawdown</span>
            <span class="value">${pct(p.max_drawdown, 1)}</span>
            <span class="sub">${(p.observations || 0).toLocaleString()} obs</span></div>`;
      }

      const svg = $(`#chart-${key}`);
      if (svg) {
        const series = [{ label: sleeve.title, points: sleeve.equity }];
        if (featured.benchmark) {
          series.push({ label: featured.benchmark.label || "Benchmark",
            points: featured.benchmark.equity, dashed: true });
        }
        drawChart(svg, series, { height: 130, baseline: p.starting_equity });
      }

      const verdict = $(`#verdict-${key}`);
      if (verdict) verdict.innerHTML = alphaVerdict(p, { badge: false });
    });
  }

  return {
    home: initHome, strategy: initStrategy, result: initResult,
    live: initLive, showcase: initShowcase,
    /* Static page — it needs the theme toggle wired and nothing else. */
    docs: initTheme,
  };
})();
