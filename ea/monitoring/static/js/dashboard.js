/* QuantTrader.AI dashboard — TradingView-style frontend */

(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const fmtUSD = (v, d = 2) =>
    v == null ? "$—" :
    "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
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
  const fmtTime = (iso) => {
    if (!iso) return "—";
    try { return new Date(iso).toISOString().substring(11, 19); } catch { return "—"; }
  };
  const escapeHtml = (s) =>
    (s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);

  const state = {
    selectedSymbol: "SPY",
    chart: null,
    candleSeries: null,
    watchlist: [],
    streams: {},
    timeframe: "1Day",
    tradeSide: "buy",
  };

  /* ---------- clock ---------- */
  function tickClock() {
    const d = new Date();
    const pad = n => String(n).padStart(2, "0");
    $("#clock-utc").textContent =
      `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  }
  setInterval(tickClock, 30000); tickClock();

  /* ---------- API ---------- */
  const api = {
    get: (url) => fetch(url).then(r => r.ok ? r.json() : Promise.reject(r.status)),
    post: (url, body) => fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : null,
    }).then(r => r.json().catch(() => ({}))),
    del: (url) => fetch(url, { method: "DELETE" }).then(r => r.json().catch(() => ({}))),
  };

  /* ---------- LEFT NAV (views) ---------- */
  $$(".nav-icon[data-view]").forEach(b => {
    b.addEventListener("click", () => {
      const target = b.dataset.view;
      $$(".nav-icon").forEach(x => x.classList.remove("active"));
      $$(".view").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      $(`.view[data-view-content="${target}"]`).classList.add("active");
      if (target === "chart" && state.chart) {
        const el = $("#chart");
        state.chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
      }
    });
  });

  /* ---------- BOTTOM TABS ---------- */
  $$(".bt").forEach(b => {
    b.addEventListener("click", () => {
      const target = b.dataset.bt;
      $$(".bt").forEach(x => x.classList.remove("active"));
      $$(".bottom-content").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      $(`.bottom-content[data-bt-content="${target}"]`).classList.add("active");
    });
  });

  /* ---------- TIMEFRAMES ---------- */
  $$(".tf-btn").forEach(b => {
    b.addEventListener("click", () => {
      $$(".tf-btn").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      state.timeframe = b.dataset.tf;
      // For now we always have 1Day in store; deeper TFs need stream/backfill on demand.
      // Future: fetch /api/chart/{sym}?tf= and switch.
      if (state.timeframe === "1Day") loadChart(state.selectedSymbol);
      else $("#chart-meta").textContent = `${b.dataset.tf} not yet wired (showing 1D)`;
    });
  });

  /* ---------- ACCOUNT ---------- */
  function renderAccount(a) {
    if (!a || a.error) {
      $("#m-equity").textContent = "$—";
      return;
    }
    $("#m-equity").textContent = fmtUSD(a.equity);
    $("#m-cash").textContent = fmtUSD(a.cash);
    const pnl = a.pnl_today, pct = a.pnl_today_pct;
    const cls = pnl > 0.01 ? "up" : pnl < -0.01 ? "down" : "";
    const el = $("#m-pnl-today");
    el.className = "acct-val " + cls;
    el.textContent = `${pnl >= 0 ? "+" : ""}${fmtUSD(pnl)} (${fmtPct(pct)})`;
  }

  /* ---------- POSITIONS ---------- */
  function renderPositions(positions, ordersOpen) {
    const n = positions.length;
    $("#positions-count").textContent = n;
    const body = $("#positions-body");
    const hint = $("#positions-hint");
    if (!n) {
      body.innerHTML = `<tr class="placeholder"><td colspan="8">No open positions</td></tr>`;
      const pendingCount = (ordersOpen || []).filter(o =>
        ["new", "accepted", "pending", "partially_filled"].includes(o.status)).length;
      hint.textContent = pendingCount > 0
        ? `${pendingCount} pending order(s) — see Orders tab. They'll show here once filled.`
        : "Submit a trade from the Scanner tab; the position will show here after the order fills.";
      return;
    }
    hint.textContent = "";
    body.innerHTML = positions.map(p => {
      const pl = p.unrealized_pl;
      const cls = pl == null ? "" : pl >= 0 ? "pos-pl-up" : "pos-pl-down";
      const plStr = pl == null ? "—" : (pl >= 0 ? "+" : "") + fmtUSD(pl);
      const pctStr = p.unrealized_pl_pct != null ? fmtPct(p.unrealized_pl_pct * 100) : "—";
      return `<tr>
        <td><strong>${p.symbol}</strong></td>
        <td class="num">${fmtNum(p.quantity, 4)}</td>
        <td class="num">${fmtUSD(p.avg_entry_price)}</td>
        <td class="num">${fmtUSD(p.current_price)}</td>
        <td class="num">${fmtUSD(p.market_value)}</td>
        <td class="num ${cls}">${plStr}</td>
        <td class="num ${cls}">${pctStr}</td>
        <td><button class="row-action pos-close-btn" data-sym="${p.symbol}">Close</button></td>
      </tr>`;
    }).join("");
    $$(".pos-close-btn").forEach(b => b.addEventListener("click", () => closePosition(b.dataset.sym)));

    // risk panel
    const pct = Math.min(100, (n / 15) * 100);
    if ($("#risk-pos-bar")) {
      $("#risk-pos-bar").style.width = pct + "%";
      $("#risk-pos-bar").classList.toggle("warn", pct >= 50 && pct < 80);
      $("#risk-pos-bar").classList.toggle("danger", pct >= 80);
      $("#risk-pos-meta").textContent = `${n} / 15`;
    }
  }

  async function closePosition(symbol) {
    if (!confirm(`Close ${symbol} via market order?`)) return;
    const positions = await api.get("/api/positions");
    const pos = positions.find(p => p.symbol === symbol);
    if (!pos) return;
    const r = await fetch("/api/order", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        symbol, side: pos.quantity > 0 ? "sell" : "buy",
        quantity: Math.abs(pos.quantity), order_type: "market",
      }),
    });
    if (r.ok) loadOrders();
    else alert("Close failed");
  }

  /* ---------- ORDERS ---------- */
  function renderOrders(orders) {
    $("#orders-count").textContent = orders.length;
    const body = $("#orders-body");
    if (!orders.length) {
      body.innerHTML = `<tr class="placeholder"><td colspan="8">No orders submitted yet</td></tr>`;
      return;
    }
    body.innerHTML = orders.map(o => {
      const ts = fmtTime(o.submitted_at);
      const sideCls = o.side === "buy" ? "up" : "down";
      const note = o.error ? `<span style="color:var(--sell)">${escapeHtml(o.error)}</span>`
        : (o.filled_qty > 0 ? `Filled ${fmtNum(o.filled_qty, 2)} @ ${fmtUSD(o.avg_fill_price)}`
        : (o.limit_price ? `Limit ${fmtUSD(o.limit_price)}` : ""));
      const canCancel = o.order_id && ["new", "accepted", "pending", "partially_filled"].includes(o.status);
      const action = canCancel
        ? `<button class="row-action order-cancel-btn" data-id="${o.order_id}">Cancel</button>`
        : "";
      return `<tr>
        <td class="num">${ts}</td>
        <td><strong>${o.symbol}</strong></td>
        <td class="${sideCls}">${o.side.toUpperCase()}</td>
        <td class="num">${fmtNum(o.quantity, 2)}</td>
        <td>${o.order_type}</td>
        <td><span class="status-badge ${o.status}">${o.status}</span></td>
        <td>${note}</td>
        <td>${action}</td>
      </tr>`;
    }).join("");
    $$(".order-cancel-btn").forEach(b => b.addEventListener("click", async () => {
      const r = await fetch(`/api/orders/${b.dataset.id}`, {method: "DELETE"});
      if (r.ok) loadOrders();
      else {
        const data = await r.json().catch(() => ({}));
        alert(`Cancel failed: ${data.detail || r.status}`);
        loadOrders();
      }
    }));
  }

  /* ---------- SYSTEM ---------- */
  function renderSystem(sys) {
    if ($("#sys-broker")) $("#sys-broker").textContent = (sys.broker || "—").toUpperCase();
    if ($("#sys-uptime")) $("#sys-uptime").textContent = sys.uptime_seconds != null ? fmtDur(sys.uptime_seconds) : "—";
    if ($("#sys-rows")) $("#sys-rows").textContent = (sys.store_rows ?? 0).toLocaleString();
    if ($("#sys-stocks")) $("#sys-stocks").textContent = sys.store_stock_symbols ?? 0;
    if ($("#sys-crypto")) $("#sys-crypto").textContent = sys.store_crypto_symbols ?? 0;
    if ($("#sys-kill")) {
      $("#sys-kill").textContent = sys.kill_switch_armed ? "ARMED" : "—";
      $("#sys-kill").style.color = sys.kill_switch_armed ? "var(--sell)" : "";
    }
    if (sys.scanner && $("#sys-scanner")) {
      $("#sys-scanner").textContent = `${sys.scanner.scans_completed || 0} scans · ${sys.scanner.in_zone || 0} in-zone (${sys.scanner.mode})`;
    }
    if (sys.news_poller && $("#sys-news")) {
      $("#sys-news").textContent = `${sys.news_poller.total_published || 0} published`;
    }
    state.streams = sys.streams || {};
    renderStreamButtons();
    if ($("#autosubmit-toggle")) $("#autosubmit-toggle").checked = !!sys.autosubmit;
  }

  function renderStreamButtons() {
    const cryptoOn = !!state.streams["crypto"]?.running;
    const stockOn = !!state.streams["stock"]?.running;
    $("#btn-stream-crypto").classList.toggle("on", cryptoOn);
    $("#btn-stream-crypto").textContent = cryptoOn ? "● Crypto" : "⊕ Crypto";
    $("#btn-stream-stock").classList.toggle("on", stockOn);
    $("#btn-stream-stock").textContent = stockOn ? "● Stock" : "⊕ Stock";
  }

  /* ---------- ALERTS / EVENT LOG ---------- */
  function renderAlerts(alerts) {
    $("#alerts-count").textContent = alerts.length;
    const html = !alerts.length
      ? `<li class="placeholder">Awaiting events</li>`
      : alerts.map(a => `
        <li>
          <span class="event-time">${fmtTime(a.timestamp)}</span>
          <span class="event-level ${a.level}">${a.level}</span>
          <span class="event-msg">${escapeHtml(a.message)}</span>
        </li>`).join("");
    $("#event-log").innerHTML = html;
    if ($("#event-log-monitor")) $("#event-log-monitor").innerHTML = html;
  }

  /* ---------- WATCHLIST ---------- */
  function renderWatchlist(wl) {
    state.watchlist = wl;
    const list = $("#watchlist-list");
    if (!wl.length) {
      list.innerHTML = `<li class="placeholder">Empty</li>`;
      return;
    }
    list.innerHTML = wl.map(w => {
      const chg = w.change_pct;
      const chgCls = chg == null ? "" : chg >= 0 ? "up" : "down";
      const chgStr = chg == null ? "—" : fmtPct(chg);
      const liveDot = w.live ? `<span class="live-dot"></span>` : "";
      const safe = w.symbol.replace("/", "_");
      return `<li data-sym="${w.symbol}" class="${state.selectedSymbol === w.symbol ? "active" : ""}">
        <div>
          <div class="wl-sym-row">${liveDot}<span class="wl-sym">${w.symbol}</span></div>
          <div class="wl-chg ${chgCls}" id="wl-chg-${safe}">${chgStr}</div>
        </div>
        <div>
          <div class="wl-px" id="wl-px-${safe}">${w.last == null ? "—" : fmtUSD(w.last)}</div>
        </div>
        <button class="wl-del" data-sym="${w.symbol}">×</button>
      </li>`;
    }).join("");

    $$(".watchlist li[data-sym]").forEach(li => {
      li.addEventListener("click", e => {
        if (e.target.classList.contains("wl-del")) return;
        state.selectedSymbol = li.dataset.sym;
        loadChart(state.selectedSymbol);
        renderWatchlist(state.watchlist);
        // Also switch to chart view if not already
        $$(".nav-icon").forEach(n => n.classList.remove("active"));
        $(`.nav-icon[data-view="chart"]`).classList.add("active");
        $$(".view").forEach(v => v.classList.remove("active"));
        $(`.view[data-view-content="chart"]`).classList.add("active");
      });
    });
    $$(".wl-del").forEach(btn => {
      btn.addEventListener("click", async e => {
        e.stopPropagation();
        await api.del(`/api/watchlist/${encodeURIComponent(btn.dataset.sym)}`);
        if (state.selectedSymbol === btn.dataset.sym) state.selectedSymbol = wl[0]?.symbol || "SPY";
        loadWatchlist();
      });
    });

    // Update header symbol bar from selected
    const sel = wl.find(w => w.symbol === state.selectedSymbol) || wl[0];
    if (sel) {
      state.selectedSymbol = sel.symbol;
      $("#sym-name").textContent = sel.symbol;
      $("#sym-price").textContent = sel.last == null ? "—" : fmtUSD(sel.last);
      const chg = sel.change_pct;
      $("#sym-change").textContent = chg == null ? "" : fmtPct(chg);
      $("#sym-change").className = "sym-change " + (chg == null ? "" : chg >= 0 ? "up" : "down");
    }

    // Auto-load chart on first paint
    if (!state.chart && sel?.last != null) loadChart(sel.symbol);
  }

  /* ---------- CHART ---------- */
  function ensureChart() {
    if (state.chart) return;
    const el = $("#chart");
    state.chart = LightweightCharts.createChart(el, {
      width: el.clientWidth, height: el.clientHeight,
      layout: { background: { type: "solid", color: "#131722" }, textColor: "#787b86", fontFamily: "Inter, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: "#363a45" },
      timeScale: { borderColor: "#363a45", timeVisible: true },
    });
    state.candleSeries = state.chart.addCandlestickSeries({
      upColor: "#26a69a", downColor: "#ef5350",
      borderUpColor: "#26a69a", borderDownColor: "#ef5350",
      wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });
    new ResizeObserver(() => {
      if (!state.chart) return;
      state.chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    }).observe(el);
  }

  async function loadChart(symbol) {
    if (!symbol) return;
    ensureChart();
    $("#chart-title").textContent = symbol;
    $("#chart-meta").textContent = "loading…";
    try {
      const data = await api.get(`/api/chart/${encodeURIComponent(symbol)}?limit=200`);
      state.candleSeries.setData(data.bars);
      state.chart.timeScale().fitContent();
      $("#chart-meta").textContent = `${data.bars.length} bars · 1D`;
    } catch (e) {
      $("#chart-meta").textContent = "no data — run backfill first";
      state.candleSeries.setData([]);
    }
  }

  /* ---------- NEWS / SIGNALS / RISK ---------- */
  function renderNews(items) {
    const list = $("#news-list");
    if (!items.length) {
      list.innerHTML = `<li class="placeholder">Awaiting news</li>`;
      return;
    }
    list.innerHTML = items.map(n => {
      const hm = fmtTime(n.published_at).substring(0, 5);
      const src = (n.source_name || n.source || "").toUpperCase();
      const tickers = (n.tickers || []).map(x => `<span class="news-ticker">${x}</span>`).join("");
      return `<li>
        <div class="news-time">${hm}</div>
        <div>
          <div class="news-title">${tickers}<a href="${n.url}" target="_blank" rel="noopener">${escapeHtml(n.title)}</a></div>
          <div class="news-meta">${src}</div>
        </div>
      </li>`;
    }).join("");
  }

  function renderSignals(items) {
    $("#signals-count").textContent = items.length;
    const list = $("#signals-list");
    if (!items.length) {
      list.innerHTML = `<li class="placeholder">No signals yet</li>`;
      return;
    }
    list.innerHTML = items.map(s => {
      const cls = s.submitted ? "submitted" : (s.accepted ? "accepted" : "rejected");
      const sideCls = s.side === "long" ? "long" : (s.side === "short" ? "short" : "");
      const conv = (s.conviction || 0).toFixed(2);
      const status = s.submitted ? `Sent · ${s.order_status || ''}` : (s.accepted ? "Accepted" : `Rejected · ${s.reason || ''}`);
      return `<li class="${cls}">
        <span class="sig-side ${sideCls}">${(s.side || '').toUpperCase()}</span>
        <span><strong>${s.symbol}</strong></span>
        <span class="sig-meta">${s.strategy} · c=${conv} · ${escapeHtml(s.rationale || '')}</span>
        <span class="sig-meta">${status}</span>
      </li>`;
    }).join("");
  }

  function renderStrategies(items) {
    const row = $("#strategies-row");
    row.innerHTML = items.map(s =>
      `<button class="strategy-chip ${s.enabled ? 'on' : ''}" data-name="${s.name}">${s.name}</button>`
    ).join("");
    $$(".strategy-chip").forEach(b => {
      b.addEventListener("click", async () => {
        const on = b.classList.contains("on");
        await fetch(`/api/strategies/${b.dataset.name}/${on ? 'pause' : 'resume'}`, { method: "POST" });
        loadStrategies();
      });
    });
  }

  function renderRisk(risk) {
    const dl = risk.daily_loss_pct || 0, wl = risk.weekly_loss_pct || 0;
    setRiskBar("risk-daily-bar", Math.min(100, (dl / 2.0) * 100), risk.daily_halted);
    setRiskBar("risk-weekly-bar", Math.min(100, (wl / 5.0) * 100), risk.weekly_halted);
    if ($("#risk-daily-meta")) $("#risk-daily-meta").textContent = `${dl.toFixed(2)}% / 2%`;
    if ($("#risk-weekly-meta")) $("#risk-weekly-meta").textContent = `${wl.toFixed(2)}% / 5%`;
    if ($("#risk-status")) {
      $("#risk-status").textContent = (risk.daily_halted || risk.weekly_halted) ? "HALTED" : "OK";
      $("#risk-status").style.color = (risk.daily_halted || risk.weekly_halted) ? "var(--sell)" : "";
    }
  }

  function setRiskBar(id, pct, halted) {
    const el = $(`#${id}`);
    if (!el) return;
    el.style.width = pct + "%";
    el.classList.remove("warn", "danger");
    if (halted || pct >= 90) el.classList.add("danger");
    else if (pct >= 50) el.classList.add("warn");
  }

  /* ---------- SCANNER ---------- */
  function renderScannerStats(s) {
    $("#ss-scans").textContent = s.scans_completed || 0;
    $("#ss-inzone").textContent = s.in_zone || 0;
    $("#ss-approach").textContent = s.approaching || 0;
    $("#ss-watching").textContent = s.watching || 0;
    $("#ss-universe").textContent = s.universe_size || 0;
    $("#ss-last").textContent = s.last_scan_at ? fmtTime(s.last_scan_at) : "—";
    if ($("#scanner-mode").value !== s.mode) $("#scanner-mode").value = s.mode;
  }

  function renderScanner(rows) {
    const body = $("#scanner-body");
    if (!rows || !rows.length) {
      body.innerHTML = `<tr class="placeholder"><td colspan="13">No setups match filter — try lowering filter or clicking Scan now</td></tr>`;
      return;
    }
    body.innerHTML = rows.map(r => {
      const sideCls = r.side === "bullish" ? "bullish" : "bearish";
      const conf = (r.confluence || []).join(" · ");
      const distStr = r.distance_pct >= 0 ? `+${r.distance_pct.toFixed(2)}%` : `${r.distance_pct.toFixed(2)}%`;
      const isForex = r.asset_class === "forex";
      const tradeBtn = isForex
        ? `<button class="scan-trade-btn" disabled title="OANDA required">Forex</button>`
        : `<button class="scan-trade-btn" data-payload='${JSON.stringify(r).replace(/'/g, "&apos;")}'>Trade</button>`;
      return `<tr>
        <td><strong>${r.symbol}</strong></td>
        <td>${r.asset_class}</td>
        <td class="scan-side ${sideCls}">${r.side === "bullish" ? "▲ Long" : "▼ Short"}</td>
        <td>${r.zone_kind}</td>
        <td class="num">${fmtUSD(r.current_price)}</td>
        <td class="num">${fmtUSD(r.entry_low)} – ${fmtUSD(r.entry_high).replace("$", "")}</td>
        <td class="num">${fmtUSD(r.stop)}</td>
        <td class="num">${fmtUSD(r.target)}</td>
        <td class="num">${r.risk_reward.toFixed(1)}x</td>
        <td class="num">${distStr}</td>
        <td><span class="scan-status ${r.status}">${r.status.replace("_", " ")}</span></td>
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
    $("#trade-modal-title").textContent = `Trade ${setup.symbol}`;
    $("#trade-modal-meta").innerHTML = `
      <div><strong>${setup.zone_kind}</strong> · ${setup.confluence.join(" · ")}</div>
      <div>Current: <strong>${fmtUSD(setup.current_price)}</strong> · Entry zone ${fmtUSD(setup.entry_low)}–${fmtUSD(setup.entry_high).replace("$","")}</div>
      <div>Stop ${fmtUSD(setup.stop)} · Target ${fmtUSD(setup.target)} · R:R <strong>${setup.risk_reward.toFixed(1)}x</strong></div>
    `;
    state.tradeSide = setup.side === "bullish" ? "buy" : "sell";
    setSideButtons(state.tradeSide);
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

  function setSideButtons(side) {
    $$(".side-btn").forEach(b => b.classList.toggle("active", b.dataset.side === side));
  }
  $$(".side-btn").forEach(b => b.addEventListener("click", () => {
    state.tradeSide = b.dataset.side;
    setSideButtons(state.tradeSide);
  }));

  /* ---------- LOADERS ---------- */
  let _ordersCache = [];
  async function loadAccount()    { renderAccount(await api.get("/api/account")); }
  async function loadPositions()  { renderPositions(await api.get("/api/positions"), _ordersCache); }
  async function loadOrders()     {
    const orders = await api.get("/api/orders");
    _ordersCache = orders;
    renderOrders(orders);
    renderPositions(await api.get("/api/positions"), orders);
  }
  async function loadAlerts()     { renderAlerts(await api.get("/api/alerts")); }
  async function loadWatchlist()  { renderWatchlist(await api.get("/api/watchlist")); }
  async function loadNews()       { renderNews(await api.get("/api/news")); }
  async function loadSignals()    { renderSignals(await api.get("/api/signals")); }
  async function loadStrategies() { renderStrategies(await api.get("/api/strategies")); }
  async function loadRisk()       { renderRisk(await api.get("/api/risk")); }
  async function loadScanner() {
    const f = $("#scanner-filter").value, c = $("#scanner-class").value;
    const qs = new URLSearchParams();
    if (f) qs.set("status_filter", f);
    if (c) qs.set("asset_class", c);
    const [rows, st] = await Promise.all([
      api.get("/api/scanner/results?" + qs.toString()),
      api.get("/api/scanner/status"),
    ]);
    renderScanner(rows);
    renderScannerStats(st);
  }

  function renderScalp(data) {
    if (!data) return;
    const sigs = data.signals || [];
    const pos = data.positions || [];
    $("#scalp-sigcount").textContent = data.signal_count ?? sigs.length;
    $("#scalp-poscount").textContent = pos.length;
    $("#scalp-tf").textContent = data.timeframe || "1Min";
    const tog = $("#scalp-toggle");
    if (tog) tog.checked = !!data.enabled;

    const pbody = $("#scalp-pos-body");
    if (!pos.length) {
      pbody.innerHTML = `<tr class="placeholder"><td colspan="6">No open scalp positions</td></tr>`;
    } else {
      pbody.innerHTML = pos.map(p => {
        const pl = Number(p.unrealized_pl ?? p.pl ?? 0);
        const plPct = Number(p.unrealized_pl_pct ?? p.pl_pct ?? 0);
        const cls = pl >= 0 ? "long" : "short";
        return `<tr>
          <td><strong>${escapeHtml(p.symbol || '')}</strong></td>
          <td class="num">${p.quantity ?? p.qty ?? ''}</td>
          <td class="num">${p.avg_entry_price ?? p.entry ?? ''}</td>
          <td class="num">${p.current_price ?? p.last ?? ''}</td>
          <td class="num ${cls}">${pl.toFixed(2)}</td>
          <td class="num ${cls}">${(plPct * (Math.abs(plPct) < 1 ? 100 : 1)).toFixed(2)}%</td>
        </tr>`;
      }).join("");
    }

    const list = $("#scalp-sig-list");
    if (!sigs.length) {
      list.innerHTML = `<li class="placeholder">No scalp signals yet</li>`;
    } else {
      list.innerHTML = sigs.map(s => {
        const cls = s.submitted ? "submitted" : (s.accepted ? "accepted" : "rejected");
        const sideCls = s.side === "long" ? "long" : (s.side === "short" ? "short" : "");
        const conv = (s.conviction || 0).toFixed(2);
        const status = s.submitted ? `Sent · ${s.order_status || ''}` : (s.accepted ? "Accepted" : `Rejected · ${s.reason || ''}`);
        return `<li class="${cls}">
          <span class="sig-side ${sideCls}">${(s.side || '').toUpperCase()}</span>
          <span><strong>${escapeHtml(s.symbol || '')}</strong></span>
          <span class="sig-meta">c=${conv} · ${escapeHtml(s.rationale || '')}</span>
          <span class="sig-meta">${escapeHtml(status)}</span>
        </li>`;
      }).join("");
    }
  }

  async function loadScalp() { renderScalp(await api.get("/api/scalp")); }

  const scalpTog = $("#scalp-toggle");
  if (scalpTog) {
    scalpTog.addEventListener("change", async () => {
      await fetch(`/api/strategies/smc_scalp/${scalpTog.checked ? 'resume' : 'pause'}`, { method: "POST" });
      loadScalp(); loadStrategies();
    });
  }

  async function refreshAll() {
    await Promise.allSettled([
      loadAccount(), loadOrders(), loadAlerts(), loadWatchlist(), loadNews(),
      loadSignals(), loadStrategies(), loadRisk(), loadScanner(), loadScalp(),
    ]);
  }

  /* ---------- SSE ---------- */
  function applyLiveBar(b) {
    const safe = b.symbol.replace("/", "_");
    const px = $(`#wl-px-${safe}`);
    if (px) px.textContent = fmtUSD(b.close);
    if (state.selectedSymbol === b.symbol) {
      $("#sym-price").textContent = fmtUSD(b.close);
      if (state.candleSeries) {
        state.candleSeries.update({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close });
      }
    }
  }

  function startSSE() {
    const ev = new EventSource("/api/stream");
    ev.addEventListener("snapshot", e => {
      try {
        const p = JSON.parse(e.data);
        if (p.system) renderSystem(p.system);
        if (p.account) renderAccount(p.account);
      } catch (err) { console.warn("bad sse", err); }
    });
    ev.addEventListener("bar", e => {
      try { applyLiveBar(JSON.parse(e.data)); } catch (_) {}
    });
    ev.addEventListener("news", () => loadNews());
    ev.onerror = () => $("#connection-dot").style.background = "var(--warn)";
    ev.onopen  = () => $("#connection-dot").style.background = "var(--buy)";
  }

  async function toggleStream(assetClass) {
    const on = !!state.streams[assetClass]?.running;
    if (on) await fetch(`/api/stream/stop?asset_class=${assetClass}`, { method: "POST" });
    else await api.post("/api/stream/start", { asset_class: assetClass });
    setTimeout(refreshAll, 400);
  }

  /* ---------- CONTROL HANDLERS ---------- */
  $("#btn-stream-crypto").addEventListener("click", () => toggleStream("crypto"));
  $("#btn-stream-stock").addEventListener("click", () => toggleStream("stock"));

  $("#wl-add").addEventListener("click", async () => {
    const input = $("#wl-input");
    const sym = (input.value || "").trim().toUpperCase();
    if (!sym) return;
    await api.post("/api/watchlist", { symbol: sym });
    input.value = "";
    loadWatchlist();
  });
  $("#wl-input").addEventListener("keydown", e => { if (e.key === "Enter") $("#wl-add").click(); });

  $("#btn-kill").addEventListener("click", () => $("#kill-modal").classList.remove("hidden"));
  $("#kill-cancel").addEventListener("click", () => $("#kill-modal").classList.add("hidden"));
  $("#kill-cancel-2").addEventListener("click", () => $("#kill-modal").classList.add("hidden"));
  $("#kill-confirm").addEventListener("click", async () => {
    $("#kill-modal").classList.add("hidden");
    await api.post("/api/kill");
    loadAlerts(); loadOrders();
  });

  $("#btn-news-poll").addEventListener("click", async () => {
    await fetch("/api/news/poll", { method: "POST" });
    setTimeout(loadNews, 400);
  });
  if ($("#btn-news-poll-2")) $("#btn-news-poll-2").addEventListener("click", async () => {
    await fetch("/api/news/poll", { method: "POST" });
    setTimeout(loadNews, 400);
  });

  $("#autosubmit-toggle").addEventListener("change", async e => {
    await fetch(`/api/autosubmit?enabled=${e.target.checked}`, { method: "POST" });
  });

  $("#btn-scan-now").addEventListener("click", async () => {
    const btn = $("#btn-scan-now"); const orig = btn.textContent;
    btn.textContent = "Scanning…"; btn.disabled = true;
    try {
      await fetch("/api/scanner/scan-now", { method: "POST" });
      await loadScanner();
    } finally { btn.textContent = orig; btn.disabled = false; }
  });
  $("#scanner-filter").addEventListener("change", loadScanner);
  $("#scanner-class").addEventListener("change", loadScanner);
  $("#scanner-mode").addEventListener("change", async e => {
    await fetch(`/api/scanner/mode?mode=${e.target.value}`, { method: "POST" });
    await loadScanner();
  });

  if ($("#btn-trade-current")) $("#btn-trade-current").addEventListener("click", () => {
    // Open trade modal pre-filled for the currently selected symbol with no scanner setup
    const sym = state.selectedSymbol;
    const sel = state.watchlist.find(w => w.symbol === sym);
    const px = sel?.last || 0;
    openTradeModal({
      symbol: sym, asset_class: sym.includes("/") ? "crypto" : "stock",
      side: "bullish", zone_kind: "Manual",
      confluence: ["Manual entry"],
      current_price: px, entry_low: px * 0.99, entry_high: px,
      stop: px * 0.97, target: px * 1.03, risk_reward: 2.0,
    });
  });

  $("#trade-cancel").addEventListener("click", () => $("#trade-modal").classList.add("hidden"));
  $("#trade-cancel-2").addEventListener("click", () => $("#trade-modal").classList.add("hidden"));
  $("#trade-submit").addEventListener("click", async () => {
    const m = $("#trade-modal");
    if (m.dataset.assetClass === "forex") {
      alert("Forex execution requires OANDA configuration (Phase C).");
      return;
    }
    const body = {
      symbol: m.dataset.symbol,
      side: state.tradeSide,
      quantity: parseFloat($("#trade-qty").value),
      order_type: $("#trade-type").value,
    };
    if (body.order_type === "limit") body.limit_price = parseFloat($("#trade-limit").value);
    try {
      const r = await fetch("/api/order", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (r.ok) {
        m.classList.add("hidden");
        loadOrders(); loadAlerts();
        // Auto-switch to Orders tab to show what just happened
        $$(".bt").forEach(b => b.classList.remove("active"));
        $$(".bottom-content").forEach(b => b.classList.remove("active"));
        $(`.bt[data-bt="orders"]`).classList.add("active");
        $(`.bottom-content[data-bt-content="orders"]`).classList.add("active");
      } else {
        alert(`Order rejected: ${data.detail || data.error || r.status}`);
      }
    } catch (e) { alert(`Network error: ${e.message}`); }
  });

  /* ---------- BOOT ---------- */
  refreshAll();
  startSSE();
  setInterval(loadAlerts, 5000);
  setInterval(loadOrders, 7000);
  setInterval(loadWatchlist, 15000);
  setInterval(loadNews, 30000);
  setInterval(loadSignals, 5000);
  setInterval(loadRisk, 10000);
  setInterval(loadStrategies, 15000);
  setInterval(loadScanner, 20000);
  setInterval(loadScalp, 8000);
})();
