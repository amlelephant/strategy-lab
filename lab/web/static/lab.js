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
     Console
     ═══════════════════════════════════════════════════════════════════ */

  function initConsole() {
    initTheme();
    const form = $("#run-form");
    const runBtn = $("#run-btn");
    const sweepBtn = $("#sweep-btn");
    const status = $("#run-status");
    const picks = $$(".pick");

    const chosen = () => picks.filter((p) => $("input[name=strategy]", p).checked);

    function refresh() {
      const active = chosen();
      picks.forEach((p) => p.classList.toggle("on",
        $("input[name=strategy]", p).checked));
      runBtn.disabled = active.length === 0;
      sweepBtn.disabled = active.length !== 1;
      $("#picked-count").textContent = active.length === 0
        ? "none selected"
        : `${active.length} selected`;
      status.textContent = active.length === 0
        ? "Select at least one strategy."
        : active.length === 1
          ? "Ready. A sweep is available for a single strategy."
          : `Ready — ${active.length} strategies, one pass over the data.`;
    }

    picks.forEach((p) => {
      $("input[name=strategy]", p).addEventListener("change", refresh);
    });
    refresh();

    $$(".preset").forEach((button) => {
      button.addEventListener("click", () => {
        $("textarea[name=symbols]").value = button.dataset.symbols;
        $("#universe-mode").value = "symbols";
        $("#symbols-field").hidden = false;
      });
    });

    $("#universe-mode").addEventListener("change", (event) => {
      $("#symbols-field").hidden = event.target.value !== "symbols";
    });

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

    function collectParams(pick) {
      const params = {};
      $$("[data-param]", pick).forEach((input) => {
        params[input.dataset.param] = input.type === "checkbox"
          ? input.checked : input.value;
      });
      return params;
    }

    function payload() {
      const data = new FormData(form);
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
        strategies: chosen().map((p) => ({
          key: p.dataset.key, params: collectParams(p),
        })),
      };
    }

    async function submit(endpoint, extra = {}) {
      runBtn.disabled = sweepBtn.disabled = true;
      status.textContent = "starting…";
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...payload(), ...extra }),
        });
        const job = await response.json();
        if (!response.ok) throw new Error(job.error || "the run was rejected");
        window.location.href = `/run/${job.id}`;
      } catch (err) {
        status.textContent = err.message;
        refresh();
      }
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submit("/api/run");
    });

    sweepBtn.addEventListener("click", () => {
      const pick = chosen()[0];
      submit("/api/sweep", { key: pick.dataset.key });
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
    let active = 0;

    const tabs = $("#sleeve-tabs");
    tabs.innerHTML = "";
    if (sleeves.length > 1) {
      sleeves.forEach((s, i) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn-quiet" + (i === 0 ? " on" : "");
        button.textContent = s.title;
        button.addEventListener("click", () => {
          active = i;
          $$(".btn-quiet", tabs).forEach((b, k) => b.classList.toggle("on", k === i));
          paint();
        });
        tabs.appendChild(button);
      });
    }

    function paint() {
      const s = sleeves[active];
      const p = s.performance;

      $("#hero-sharpe").textContent = num(p.sharpe);
      $("#hero-sharpe").className = "hero " + toneFor(p.sharpe);

      const significant = p.sharpe_t !== null && Math.abs(p.sharpe_t) > 2;
      const n = p.observations ? p.observations.toLocaleString() : "0";
      $("#hero-verdict").innerHTML = p.sharpe_t === null
        ? "Not enough observations to say anything."
        : !significant
          ? `t = ${num(p.sharpe_t, 1)} over ${n} observations —
             <strong>not distinguishable from luck</strong> on this sample.
             <span class="badge warn">inconclusive</span>`
          : p.sharpe < 0
            ? `t = ${num(p.sharpe_t, 1)} over ${n} observations — reliably
               <strong>negative</strong>, which is a real finding about the
               strategy. <span class="badge bad">significantly negative</span>`
            : `t = ${num(p.sharpe_t, 1)} over ${n} observations — clears the
               conventional |t| &gt; 2 bar.
               <span class="badge good">significant</span>`;

      $("#stat-return").textContent = signedPct(p.total_return);
      $("#stat-return").className = "value " + toneFor(p.total_return);
      $("#stat-return-sub").textContent =
        `${signedPct(p.cagr)} a year over ${num(p.years, 1)} years`;

      $("#stat-dd").textContent = pct(p.max_drawdown);
      $("#stat-dd-sub").textContent = p.max_drawdown_days
        ? `${Math.round(p.max_drawdown_days)} days underwater at worst` : "";

      $("#stat-trades").textContent = (p.round_trips || 0).toLocaleString();
      $("#stat-trades-sub").textContent = p.hit_rate === null ? ""
        : `${pct(p.hit_rate, 0)} of them profitable`;

      const costs = (p.total_commission || 0) + (p.total_slippage || 0);
      $("#stat-costs").textContent = money(costs);
      $("#stat-costs-sub").textContent = p.starting_equity
        ? `${pct(costs / p.starting_equity, 1)} of starting capital` : "";

      /* equity: this sleeve, plus the frictionless benchmark */
      const equitySvg = $("#equity-chart");
      const geom = drawChart(equitySvg, [
        { label: s.title, points: s.equity },
        { label: "Equal-weight universe", points: data.benchmark.equity,
          dashed: true },
      ], { baseline: p.starting_equity });
      attachScrub(equitySvg, geom, $("#chart-readout"), [money, money]);

      $("#equity-legend").innerHTML = `
        <span class="item"><i class="swatch" style="background:${seriesColour(0)}"></i>
          ${s.title}</span>
        <span class="item"><i class="swatch"
          style="background:var(--text-tertiary)"></i>
          Equal-weight universe, no costs — ${signedPct(data.benchmark.performance.total_return)}</span>`;

      drawChart($("#dd-chart"), [
        { label: "Drawdown", points: s.drawdown, fill: true,
          colour: PALETTE().danger },
      ], { height: 160, baseline: 0, clampMax: 0, format: (v) => pct(v, 0) });

      /* trade log */
      const rows = s.trades || [];
      $("#trade-count").textContent = rows.length
        ? `${rows.length.toLocaleString()} fills shown` : "no fills";
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
      return `<tr>
        <td class="name">${s.title}</td>
        <td class="${toneFor(p.total_return)}">${signedPct(p.total_return)}</td>
        <td>${signedPct(p.cagr)}</td>
        <td class="${toneFor(p.sharpe)}">${num(p.sharpe)}</td>
        <td class="secondary">${num(p.sharpe_t, 1)}</td>
        <td>${pct(p.max_drawdown)}</td>
        <td>${(p.round_trips || 0).toLocaleString()}</td>
        <td>${p.hit_rate === null ? "—" : pct(p.hit_rate, 0)}</td>
        <td class="secondary">${pct(s.fill_rate, 0)}</td>
      </tr>`;
    }).join("");
    const b = data.benchmark.performance;
    $("#compare-table tbody").innerHTML = rows + `<tr>
      <td class="name secondary">Equal-weight universe, no costs</td>
      <td class="secondary">${signedPct(b.total_return)}</td>
      <td class="secondary">${signedPct(b.cagr)}</td>
      <td class="secondary">${num(b.sharpe)}</td>
      <td class="secondary">${num(b.sharpe_t, 1)}</td>
      <td class="secondary">${pct(b.max_drawdown)}</td>
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

  /* ═══════════════════════════════════════════════════════════════════
     Sweep
     ═══════════════════════════════════════════════════════════════════ */

  function initSweep(jobId) {
    initTheme();
    poll(jobId, showProgress, (job) => {
      const data = job.result;
      $("#running").hidden = true;
      $("#result").hidden = false;

      $("#hero-sharpe").textContent = num(data.best.sharpe);
      $("#hero-sharpe").className = "hero " +
        (data.probably_luck ? "" : toneFor(data.best.sharpe));
      $("#verdict").innerHTML = data.verdict +
        (data.probably_luck ? ' <span class="badge bad">nothing found</span>'
          : ' <span class="badge accent">worth a closer look</span>');

      $("#stat-trials").textContent = data.trials;
      $("#stat-noise").textContent = num(data.expected_max_noise_sharpe);
      $("#stat-deflated").textContent = num(data.deflated_sharpe);
      $("#stat-plateau").textContent = pct(data.plateau, 0);

      const columns = data.columns;
      $("#grid-table thead tr").innerHTML = columns
        .map((c, i) => `<th${i === 0 ? ' class="name"' : ""}>${
          String(c).replace(/_/g, " ")}</th>`).join("");
      $("#grid-table tbody").innerHTML = data.rows.map((row) => `<tr>${
        columns.map((c, i) => {
          const v = row[c];
          const text = typeof v === "number"
            ? (["total_return", "cagr", "max_drawdown", "hit_rate"].includes(c)
              ? signedPct(v) : num(v, 3))
            : String(v);
          return `<td${i === 0 ? ' class="name"' : ""}>${text}</td>`;
        }).join("")}</tr>`).join("");
    }, showFailure);
  }

  return { console: initConsole, result: initResult, sweep: initSweep };
})();
