/* EA dashboard — vanilla JS frontend.
   Live updates via SSE (/api/stream). REST polls for non-tick data. */

(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const fmtUSD = (v, decimals = 2) =>
    v == null ? "$ —" :
    "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  const fmtNum = (v, d = 4) =>
    v == null ? "—" :
    Number(v).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: d });
  const fmtPct = (v) =>
    v == null ? "—" :
    (v >= 0 ? "+" : "") + Number(v).toFixed(2) + "%";
  const fmtDur = (secs) => {
    secs = Math.floor(secs);
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
    return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  };

  const state = {
    selectedSymbol: null,
    chart: null,
    candleSeries: null,
    watchlist: [],
    streams: {},            // asset_class -> status
    latestBars: new Map(),  // symbol -> latest bar payload
  };

  /* ---------- clock ---------- */
  function tickClock() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    $("#clock-utc").textContent =
      `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
  }
  setInterval(tickClock, 1000); tickClock();

  /* ---------- API ---------- */
  const api = {
    get: (url) => fetch(url).then(r => r.ok ? r.json() : Promise.reject(r.status)),
    post: (url, body) => fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : null,
    }).then(r => r.json().catch(() => ({}))),
    del: (url) => fetch(url, { method: "DELETE" }).then(r => r.json()),
  };

  /* ---------- panels ---------- */
  function renderAccount(a) {
    if (!a || a.error) {
      $("#m-equity").textContent = "$ —";
      $("#account-id").textContent = "broker offline";
      return;
    }
    $("#account-id").textContent = a.account_id.slice(0, 8) + "…";
    $("#m-equity").textContent = fmtUSD(a.equity);
    $("#m-cash").textContent = fmtUSD(a.cash);
    $("#m-bp").textContent = fmtUSD(a.buying_power);
    $("#m-pv").textContent = fmtUSD(a.portfolio_value);

    const pnl = a.pnl_today, pct = a.pnl_today_pct;
    const cls = pnl > 0.01 ? "up" : pnl < -0.01 ? "down" : "";
    const el = $("#m-pnl-today");
    el.className = "metric-delta " + cls;
    el.textContent = `P/L · ${pnl >= 0 ? "+" : ""}${fmtUSD(pnl, 2)} · ${fmtPct(pct)}`;

    $("#f-pdt").classList.toggle("active", !!a.pattern_day_trader);
    $("#f-blocked").classList.toggle("active", !!a.trading_blocked);
  }

  function renderPositions(positions) {
    $("#positions-count").textContent = `${positions.length} OPEN`;
    const body = $("#positions-body");
    if (!positions.length) {
      body.innerHTML = `<tr class="placeholder"><td colspan="7">// no open positions //</td></tr>`;
      $("#risk-pos-bar").style.width = "0%";
      $("#risk-pos-meta").textContent = "0 / 15 max";
      return;
    }
    body.innerHTML = positions.map(p => {
      const pl = p.unrealized_pl;
      const cls = pl == null ? "" : pl >= 0 ? "pos-pl-up" : "pos-pl-down";
      const plStr = pl == null ? "—" :
        (pl >= 0 ? "+" : "") + fmtUSD(pl, 2) + " · " + fmtPct((p.unrealized_pl_pct || 0) * 100);
      return `<tr>
        <td>${p.symbol}</td>
        <td>${p.asset_class}</td>
        <td class="num">${fmtNum(p.quantity)}</td>
        <td class="num">${fmtUSD(p.avg_entry_price)}</td>
        <td class="num">${fmtUSD(p.current_price)}</td>
        <td class="num">${fmtUSD(p.market_value)}</td>
        <td class="num ${cls}">${plStr}</td>
      </tr>`;
    }).join("");

    const pct = Math.min(100, (positions.length / 15) * 100);
    $("#risk-pos-bar").style.width = pct + "%";
    $("#risk-pos-bar").classList.toggle("warn", pct >= 50 && pct < 80);
    $("#risk-pos-bar").classList.toggle("danger", pct >= 80);
    $("#risk-pos-meta").textContent = `${positions.length} / 15 max`;
  }

  function renderSystem(sys) {
    $("#sys-tick").textContent = sys.tick != null ? `tick ${sys.tick}` : "tick —";
    $("#sys-broker").textContent = (sys.broker || "—").toUpperCase();
    $("#sys-profile").textContent = (sys.profile || "—").toUpperCase();
    $("#sys-uptime").textContent = sys.uptime_seconds != null ? fmtDur(sys.uptime_seconds) : "—";
    $("#sys-rows").textContent = (sys.store_rows ?? 0).toLocaleString();
    $("#sys-stocks").textContent = sys.store_stock_symbols ?? 0;
    $("#sys-crypto").textContent = sys.store_crypto_symbols ?? 0;
    $("#sys-kill").textContent = sys.kill_switch_armed ? "ARMED" : "—";
    $("#sys-kill").style.color = sys.kill_switch_armed ? "var(--red)" : "";

    state.streams = sys.streams || {};
    renderStreamButtons();

    $("#connection-label").textContent = "LINK · OK";
  }

  function renderStreamButtons() {
    const cryptoOn = !!state.streams["crypto"]?.running;
    const stockOn = !!state.streams["stock"]?.running;
    const cb = $("#btn-stream-crypto");
    const sb = $("#btn-stream-stock");
    cb.textContent = cryptoOn ? "● LIVE · CRYPTO" : "◉ STREAM · CRYPTO";
    cb.style.color = cryptoOn ? "var(--lime)" : "";
    cb.style.borderColor = cryptoOn ? "var(--lime)" : "";
    sb.textContent = stockOn ? "● LIVE · STOCK" : "◉ STREAM · STOCK";
    sb.style.color = stockOn ? "var(--lime)" : "";
    sb.style.borderColor = stockOn ? "var(--lime)" : "";
  }

  function renderAlerts(alerts) {
    $("#alerts-count").textContent = alerts.length;
    const log = $("#event-log");
    if (!alerts.length) {
      log.innerHTML = `<li class="placeholder">// awaiting events //</li>`;
      return;
    }
    log.innerHTML = alerts.map(a => {
      const t = new Date(a.timestamp);
      const ts = t.toISOString().substring(11, 19);
      return `<li>
        <span class="event-time">${ts}</span>
        <span class="event-level ${a.level}">${a.level.toUpperCase()}</span>
        <span class="event-msg">${a.message}</span>
      </li>`;
    }).join("");
  }

  function renderWatchlist(wl) {
    state.watchlist = wl;
    const list = $("#watchlist-list");
    if (!wl.length) {
      list.innerHTML = `<li class="placeholder">// empty //</li>`;
      return;
    }
    list.innerHTML = wl.map(w => {
      const chg = w.change_pct;
      const chgCls = chg == null ? "" : chg >= 0 ? "up" : "down";
      const chgStr = chg == null ? "—" : fmtPct(chg);
      const liveDot = w.live ? `<span class="live-dot" title="live"></span>` : "";
      return `<li data-sym="${w.symbol}" class="${state.selectedSymbol === w.symbol ? "active" : ""}">
        <div>
          <div class="wl-sym">${liveDot}${w.symbol}</div>
          <div class="wl-px" id="wl-px-${w.symbol.replace("/","_")}">${w.last == null ? "—" : fmtUSD(w.last)}</div>
        </div>
        <div style="text-align:right">
          <div class="wl-chg ${chgCls}" id="wl-chg-${w.symbol.replace("/","_")}">${chgStr}</div>
          <button class="wl-del" data-sym="${w.symbol}" title="Remove">×</button>
        </div>
      </li>`;
    }).join("");

    // wire clicks
    $$(".watchlist li[data-sym]").forEach(li => {
      li.addEventListener("click", (e) => {
        if (e.target.classList.contains("wl-del")) return;
        const sym = li.dataset.sym;
        state.selectedSymbol = sym;
        loadChart(sym);
        renderWatchlist(state.watchlist); // re-render to update active
      });
    });
    $$(".wl-del").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const sym = btn.dataset.sym;
        const r = await api.del(`/api/watchlist/${sym}`);
        if (state.selectedSymbol === sym) state.selectedSymbol = null;
        loadWatchlist();
      });
    });

    // Auto-select first if nothing chosen yet
    if (!state.selectedSymbol && wl[0]?.last != null) {
      state.selectedSymbol = wl[0].symbol;
      loadChart(wl[0].symbol);
    }
  }

  /* ---------- chart ---------- */
  function ensureChart() {
    if (state.chart) return;
    const el = $("#chart");
    state.chart = LightweightCharts.createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight,
      layout: {
        background: { type: "solid", color: "transparent" },
        textColor: "#8995c4",
        fontFamily: "JetBrains Mono",
      },
      grid: {
        vertLines: { color: "#1a234744" },
        horzLines: { color: "#1a234744" },
      },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: "#1a2347" },
      timeScale: { borderColor: "#1a2347", timeVisible: true },
    });
    state.candleSeries = state.chart.addCandlestickSeries({
      upColor: "#00ff88", downColor: "#ff3b30",
      borderUpColor: "#00ff88", borderDownColor: "#ff3b30",
      wickUpColor: "#00ff88", wickDownColor: "#ff3b30",
    });
    window.addEventListener("resize", () => {
      if (state.chart) state.chart.applyOptions({ width: el.clientWidth });
    });
  }

  async function loadChart(symbol) {
    ensureChart();
    $("#chart-title").textContent = symbol;
    $("#chart-meta").textContent = "loading…";
    try {
      const data = await api.get(`/api/chart/${symbol}?limit=200`);
      state.candleSeries.setData(data.bars);
      state.chart.timeScale().fitContent();
      $("#chart-meta").textContent = `${data.bars.length} bars · 1D · stock`;
    } catch (e) {
      $("#chart-meta").textContent = "no data · backfill required";
      state.candleSeries.setData([]);
    }
  }

  /* ---------- loads ---------- */
  async function loadAccount()    { renderAccount(await api.get("/api/account")); }
  async function loadPositions()  { renderPositions(await api.get("/api/positions")); }
  async function loadAlerts()     { renderAlerts(await api.get("/api/alerts")); }
  async function loadWatchlist()  { renderWatchlist(await api.get("/api/watchlist")); }
  async function loadNews()       { renderNews(await api.get("/api/news")); }
  async function loadSignals()    { renderSignals(await api.get("/api/signals")); }
  async function loadStrategies() { renderStrategies(await api.get("/api/strategies")); }
  async function loadRisk()       { renderRisk(await api.get("/api/risk"), await api.get("/api/orders")); }
  async function loadScanner() {
    const f = $("#scanner-filter").value;
    const c = $("#scanner-class").value;
    const qs = new URLSearchParams();
    if (f) qs.set("status_filter", f);
    if (c) qs.set("asset_class", c);
    const rows = await api.get("/api/scanner/results?" + qs.toString());
    renderScanner(rows);
    try {
      const st = await api.get("/api/scanner/status");
      $("#scanner-sub").textContent = `${st.scans_completed || 0} scans · ${st.in_zone || 0} in-zone · ${st.setups_found || 0} total`;
    } catch (_) {}
  }

  function renderScanner(rows) {
    const body = $("#scanner-body");
    if (!rows || !rows.length) {
      body.innerHTML = `<tr class="placeholder"><td colspan="13">// no setups match filter //</td></tr>`;
      return;
    }
    body.innerHTML = rows.map(r => {
      const sideCls = r.side === "bullish" ? "bullish" : "bearish";
      const sideArrow = r.side === "bullish" ? "▲" : "▼";
      const conf = (r.confluence || []).join(" · ");
      const distStr = r.distance_pct >= 0 ? `+${r.distance_pct.toFixed(2)}%` : `${r.distance_pct.toFixed(2)}%`;
      const isForex = r.asset_class === "forex";
      const tradeBtn = isForex
        ? `<button class="scan-trade-btn" disabled title="OANDA required (Phase C)">FOREX</button>`
        : `<button class="scan-trade-btn" data-payload='${JSON.stringify(r).replace(/'/g, "&apos;")}'>TRADE</button>`;
      return `<tr>
        <td><strong>${r.symbol}</strong></td>
        <td>${r.asset_class}</td>
        <td class="scan-side ${sideCls}">${sideArrow} ${r.side.toUpperCase()}</td>
        <td>${r.zone_kind}</td>
        <td class="num">${fmtUSD(r.current_price)}</td>
        <td class="num">${fmtUSD(r.entry_low)}–${fmtUSD(r.entry_high).replace("$", "")}</td>
        <td class="num">${fmtUSD(r.stop)}</td>
        <td class="num">${fmtUSD(r.target)}</td>
        <td class="num">${r.risk_reward.toFixed(1)}x</td>
        <td class="num">${distStr}</td>
        <td><span class="scan-status ${r.status}">${r.status.replace("_", " ").toUpperCase()}</span></td>
        <td class="scan-confluence">${conf}</td>
        <td>${tradeBtn}</td>
      </tr>`;
    }).join("");
    $$(".scan-trade-btn[data-payload]").forEach(b => {
      b.addEventListener("click", () => openTradeModal(JSON.parse(b.dataset.payload.replace(/&apos;/g, "'"))));
    });
  }

  function openTradeModal(setup) {
    const m = $("#trade-modal");
    $("#trade-modal-title").textContent = `${setup.symbol} · ${setup.side.toUpperCase()}`;
    $("#trade-modal-meta").innerHTML = `
      <strong>${setup.zone_kind}</strong> ${setup.confluence.join(" · ")}<br>
      Current ${fmtUSD(setup.current_price)} · Entry ${fmtUSD(setup.entry_low)}–${fmtUSD(setup.entry_high).replace("$","")}<br>
      Stop ${fmtUSD(setup.stop)} · Target ${fmtUSD(setup.target)} · R:R ${setup.risk_reward.toFixed(1)}x
    `;
    $("#trade-side").value = setup.side === "bullish" ? "buy" : "sell";
    // Default qty: $500 risk-equivalent at entry mid
    const entryMid = (setup.entry_low + setup.entry_high) / 2;
    const risk = Math.abs(entryMid - setup.stop);
    const dollarRisk = 50;
    const defaultQty = risk > 0 ? Math.max(1, Math.round((dollarRisk / risk) * 100) / 100) : 1;
    $("#trade-qty").value = setup.asset_class === "crypto" ? defaultQty : Math.max(1, Math.floor(defaultQty));
    $("#trade-type").value = "limit";
    $("#trade-limit").value = entryMid.toFixed(setup.asset_class === "crypto" ? 4 : 2);
    $("#trade-modal-warn").style.display = setup.asset_class === "forex" ? "block" : "none";
    m.dataset.symbol = setup.symbol;
    m.dataset.assetClass = setup.asset_class;
    m.classList.remove("hidden");
  }

  function renderSignals(items) {
    $("#signals-count").textContent = items.length;
    const list = $("#signals-list");
    if (!items.length) {
      list.innerHTML = `<li class="placeholder">// no signals yet //</li>`;
      return;
    }
    list.innerHTML = items.map(s => {
      const cls = s.submitted ? "submitted" : (s.accepted ? "accepted" : "rejected");
      const sideCls = s.side === "long" ? "long" : (s.side === "short" ? "short" : "");
      const conv = (s.conviction || 0).toFixed(2);
      const status = s.submitted ? `SENT · ${s.order_status || ''}` : (s.accepted ? "ACCEPT" : `REJECT · ${s.reason || ''}`);
      return `<li class="${cls}">
        <span class="sig-side ${sideCls}">${(s.side || '').toUpperCase()}</span>
        <span><strong>${s.symbol}</strong></span>
        <span class="sig-meta">${s.strategy} · c=${conv} · ${s.rationale || ''}</span>
        <span class="sig-meta">${status}</span>
      </li>`;
    }).join("");
  }

  function renderStrategies(items) {
    const row = $("#strategies-row");
    row.innerHTML = items.map(s =>
      `<button class="strategy-chip ${s.enabled ? 'on' : 'off'}" data-name="${s.name}">${s.name.toUpperCase()}</button>`
    ).join("");
    $$(".strategy-chip").forEach(b => {
      b.addEventListener("click", async () => {
        const name = b.dataset.name;
        const on = b.classList.contains("on");
        await fetch(`/api/strategies/${name}/${on ? 'pause' : 'resume'}`, { method: "POST" });
        loadStrategies();
      });
    });
  }

  function renderRisk(risk, orders) {
    const profile = state.streams; // reused
    const dl = risk.daily_loss_pct || 0, wl = risk.weekly_loss_pct || 0;
    const dailyLimit = 2.0, weeklyLimit = 5.0;
    const dPct = Math.min(100, (dl / dailyLimit) * 100);
    const wPct = Math.min(100, (wl / weeklyLimit) * 100);
    setRiskBar("risk-daily-bar", dPct, risk.daily_halted);
    setRiskBar("risk-weekly-bar", wPct, risk.weekly_halted);
    $("#risk-daily-meta").textContent = `${dl.toFixed(2)}% of ${dailyLimit}% limit`;
    $("#risk-weekly-meta").textContent = `${wl.toFixed(2)}% of ${weeklyLimit}% limit`;
    $("#risk-status").textContent = (risk.daily_halted || risk.weekly_halted) ? "HALTED" : "OK";
    $("#risk-status").style.color = (risk.daily_halted || risk.weekly_halted) ? "var(--red)" : "";

    const submitted = (orders || []).filter(o => o.status !== "failed").length;
    const failed = (orders || []).filter(o => o.status === "failed").length;
    const total = (orders || []).length || 1;
    setRiskBar("risk-orders-bar", (submitted / total) * 100, false);
    $("#risk-orders-meta").textContent = `${submitted} submitted · ${failed} failed`;
  }

  function setRiskBar(id, pct, halted) {
    const el = $(`#${id}`);
    if (!el) return;
    el.style.width = pct + "%";
    el.classList.remove("warn", "danger");
    if (halted || pct >= 90) el.classList.add("danger");
    else if (pct >= 50) el.classList.add("warn");
  }

  function renderNews(items) {
    $("#news-count").textContent = items.length;
    const list = $("#news-list");
    if (!items.length) {
      list.innerHTML = `<li class="placeholder">// awaiting news //</li>`;
      return;
    }
    list.innerHTML = items.map(n => {
      const t = new Date(n.published_at);
      const hm = t.toISOString().substring(11, 16);
      const src = (n.source_name || n.source || "").toUpperCase();
      const tickers = (n.tickers || []).map(x => `<span class="news-ticker">${x}</span>`).join("");
      return `<li>
        <div class="news-time">${hm}</div>
        <div class="news-body">
          <div class="news-title">${tickers}<a href="${n.url}" target="_blank" rel="noopener">${escapeHtml(n.title)}</a></div>
          <div class="news-meta">${src}</div>
        </div>
      </li>`;
    }).join("");
  }

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  }

  async function refreshAll() {
    await Promise.allSettled([
      loadAccount(), loadPositions(), loadAlerts(), loadWatchlist(), loadNews(),
      loadSignals(), loadStrategies(), loadRisk(), loadScanner(),
    ]);
    // sync autosubmit toggle from system snapshot
    try {
      const sys = await api.get("/api/system");
      $("#autosubmit-toggle").checked = !!sys.autosubmit;
    } catch (_) {}
  }

  /* ---------- SSE ---------- */
  function applyLiveBar(b) {
    state.latestBars.set(b.symbol, b);
    const safe = b.symbol.replace("/", "_");
    const px = $(`#wl-px-${safe}`);
    if (px) px.textContent = fmtUSD(b.close);

    // If this bar belongs to the active chart symbol, update the candle
    if (state.selectedSymbol === b.symbol && state.candleSeries) {
      state.candleSeries.update({
        time: b.time,
        open: b.open, high: b.high, low: b.low, close: b.close,
      });
    }
  }

  function startSSE() {
    const ev = new EventSource("/api/stream");
    ev.addEventListener("snapshot", (e) => {
      try {
        const p = JSON.parse(e.data);
        if (p.system) renderSystem(p.system);
        if (p.account) renderAccount(p.account);
      } catch (err) { console.warn("bad sse payload", err); }
    });
    ev.addEventListener("bar", (e) => {
      try {
        const b = JSON.parse(e.data);
        applyLiveBar(b);
      } catch (err) { console.warn("bad bar payload", err); }
    });
    ev.addEventListener("news", () => { loadNews(); });
    ev.onerror = () => {
      $("#connection-label").textContent = "LINK · RETRY";
      $("#connection-pill .dot").style.background = "var(--amber)";
    };
    ev.onopen = () => {
      $("#connection-label").textContent = "LINK · OK";
      $("#connection-pill .dot").style.background = "var(--cyan)";
    };
  }

  async function toggleStream(assetClass) {
    const on = !!state.streams[assetClass]?.running;
    if (on) {
      await fetch(`/api/stream/stop?asset_class=${assetClass}`, { method: "POST" });
    } else {
      await api.post("/api/stream/start", { asset_class: assetClass });
    }
    setTimeout(refreshAll, 400);
  }

  /* ---------- controls ---------- */
  $("#btn-refresh").addEventListener("click", refreshAll);
  $("#btn-stream-crypto").addEventListener("click", () => toggleStream("crypto"));
  $("#btn-stream-stock").addEventListener("click", () => toggleStream("stock"));
  $("#btn-news-poll").addEventListener("click", async () => {
    await fetch("/api/news/poll", { method: "POST" });
    setTimeout(loadNews, 400);
  });

  $("#autosubmit-toggle").addEventListener("change", async (e) => {
    const on = e.target.checked;
    await fetch(`/api/autosubmit?enabled=${on}`, { method: "POST" });
  });

  $("#btn-scan-now").addEventListener("click", async () => {
    $("#btn-scan-now").textContent = "...SCANNING";
    try {
      await fetch("/api/scanner/scan-now", { method: "POST" });
      await loadScanner();
    } finally {
      $("#btn-scan-now").textContent = "⟳ SCAN";
    }
  });
  $("#scanner-filter").addEventListener("change", loadScanner);
  $("#scanner-class").addEventListener("change", loadScanner);

  $("#trade-cancel").addEventListener("click", () => $("#trade-modal").classList.add("hidden"));
  $("#trade-submit").addEventListener("click", async () => {
    const m = $("#trade-modal");
    const symbol = m.dataset.symbol;
    if (m.dataset.assetClass === "forex") {
      alert("Forex execution requires OANDA configuration (Phase C).");
      return;
    }
    const body = {
      symbol,
      side: $("#trade-side").value,
      quantity: parseFloat($("#trade-qty").value),
      order_type: $("#trade-type").value,
    };
    if (body.order_type === "limit") {
      body.limit_price = parseFloat($("#trade-limit").value);
    }
    try {
      const r = await fetch("/api/order", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (r.ok) {
        alert(`Order submitted: ${data.order_id || ""} (${data.status || ""})`);
        m.classList.add("hidden");
        loadPositions(); loadAlerts();
      } else {
        alert(`Order rejected: ${data.detail || data.error || r.status}`);
      }
    } catch (e) {
      alert(`Network error: ${e.message}`);
    }
  });

  $("#wl-add").addEventListener("click", async () => {
    const input = $("#wl-input");
    const sym = (input.value || "").trim().toUpperCase();
    if (!sym) return;
    await api.post("/api/watchlist", { symbol: sym });
    input.value = "";
    loadWatchlist();
  });
  $("#wl-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#wl-add").click(); });

  $("#btn-kill").addEventListener("click", () => $("#kill-modal").classList.remove("hidden"));
  $("#kill-cancel").addEventListener("click", () => $("#kill-modal").classList.add("hidden"));
  $("#kill-confirm").addEventListener("click", async () => {
    $("#kill-modal").classList.add("hidden");
    await api.post("/api/kill");
    loadAlerts();
  });

  /* ---------- boot ---------- */
  refreshAll();
  startSSE();
  setInterval(loadAlerts, 5000);
  setInterval(loadPositions, 10000);
  setInterval(loadWatchlist, 15000);
  setInterval(loadNews, 30000);
  setInterval(loadSignals, 5000);
  setInterval(loadRisk, 10000);
  setInterval(loadStrategies, 15000);
  setInterval(loadScanner, 20000);
})();
