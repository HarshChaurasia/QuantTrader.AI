# Architecture

## Design principles

1. **Single process, local first.** No microservices, no Docker, no Kubernetes until paper-trading proves out an edge worth scaling. A single Python process on the user's machine can handle 2000 tickers + crypto + FX at swing horizons.
2. **Broker-agnostic core.** Strategies depend on a `Broker` interface, not on Alpaca or OANDA directly. Asset-class-specific adapters live behind that interface.
3. **Same code path, paper and live.** Paper vs live is a config flag. No "test mode" branches in strategy code — those rot and diverge.
4. **Event-driven backtest matches live.** The backtest engine replays the same events (bars, news, fills) into the same strategy code. If backtest and live behave differently, that's a bug, not a feature.
5. **State is durable.** Positions, orders, news-analysis cache, and equity curve persist to disk. A crash mid-session must not lose state or produce duplicate orders.
6. **Risk is centralized and pre-trade.** No strategy can place an order that bypasses the risk manager. Risk manager has veto.

## Component diagram (text)

```
                 +---------------------+
                 |   News Sources       |   RSS/EDGAR/Reddit/CB feeds
                 +----------+----------+
                            |
                            v
                 +---------------------+
                 |   News Pipeline      |   dedupe -> ticker-tag -> LLM score -> cache
                 +----------+----------+
                            |
                            v   (NewsEvent stream)
+--------------+   +---------------------+   +----------------+
| Market Data  +-> |   Event Bus          | <-+ Broker Stream  |   bars, quotes, fills
+------^-------+   +----------+----------+   +-------^--------+
       |                      |                      |
       |              (BarEvent, NewsEvent,          |
       |               FillEvent, ClockEvent)        |
       |                      v                      |
       |           +---------------------+           |
       |           |   Strategies         |  generate Signals (ticker, side, conviction, horizon)
       |           +----------+----------+           |
       |                      |                      |
       |                      v                      |
       |           +---------------------+           |
       |           |   Risk Manager       |  size, validate, veto
       |           +----------+----------+           |
       |                      |                      |
       |                      v                      |
       |           +---------------------+           |
       |           |   Order Manager      +-----------+
       |           +----------+----------+
       |                      |
       |                      v
       |           +---------------------+
       +-----------+   Broker Adapter     |  Alpaca / OANDA
                   +---------------------+
```

## Components

### Broker abstraction (`ea/brokers/`)

```python
class Broker(ABC):
    async def get_account(self) -> Account: ...
    async def get_positions(self) -> list[Position]: ...
    async def submit_order(self, order: OrderRequest) -> Order: ...
    async def cancel_order(self, order_id: str) -> None: ...
    async def stream_events(self) -> AsyncIterator[BrokerEvent]: ...  # bars, fills, account updates
    async def get_bars(self, symbol: str, timeframe: TimeFrame, start, end) -> pd.DataFrame: ...
```

Concrete adapters: `AlpacaBroker` (stocks + crypto), `OandaBroker` (forex). Each handles its own auth, rate limits, retry, reconnect.

### Market data layer (`ea/data/`)

- **Historical store:** DuckDB tables partitioned by `(asset_class, symbol, date)`, populated via broker bar APIs and yfinance backfill.
- **Real-time:** broker WebSocket streams normalized into `BarEvent`/`QuoteEvent`.
- **Universe:** `UniverseManager` produces the daily list of tradable symbols per asset class — stock liquidity scan, crypto top-N by volume, FX major pairs.

### News pipeline (`ea/news/`)

```
fetchers/         # one per source: sec_edgar.py, yahoo_news.py, coindesk.py, fed.py, ...
dedupe.py         # content-hash dedup with TTL
ticker_tagger.py  # NER + symbol resolver (stocks: SEC mapping; crypto: token symbol; FX: pair from country)
analyzer.py       # Claude API: relevance, sentiment, catalyst type, materiality score
cache.py          # SQLite cache of (article_hash -> LLM result) — never re-score same article
```

LLM cost control: Haiku scores everything cheap; only items above relevance/materiality threshold get promoted to Sonnet for a deeper structured pass.

### Strategy framework (`ea/strategies/`)

```python
class Strategy(ABC):
    asset_classes: set[AssetClass]   # which markets this strategy applies to
    
    def on_bar(self, event: BarEvent, ctx: Context) -> list[Signal]: ...
    def on_news(self, event: NewsEvent, ctx: Context) -> list[Signal]: ...
    def on_fill(self, event: FillEvent, ctx: Context) -> None: ...   # for state updates
```

Signals are advisory — the risk manager decides final size and whether to send.

### Risk manager (`ea/risk/`)

Pre-trade checks (all must pass):
1. Position cap per symbol, sector, asset class.
2. Portfolio VaR / vol budget under daily/weekly limits.
3. Correlation check — reject signals that would overweight an existing risk cluster.
4. Circuit breaker state — daily loss limit hit = halt new entries (closes still allowed).
5. Regime filter — VIX/BTC-vol thresholds.
6. Account-level: enough buying power, not in PDT-violation territory (defensive even though we're swing).

Post-trade:
- Update vol estimates, correlation matrix, equity curve.
- Trip circuit breakers if thresholds crossed.

### Execution (`ea/execution/`)

- `OrderManager`: idempotent submission (client_order_id), order state machine, retry on transient errors, no retry on logic errors.
- `PositionTracker`: source of truth for current positions, reconciled against broker every N seconds.
- Smart routing: limit orders with mid+offset by default; market only on stops.

### Backtest engine (`ea/backtest/`)

- Event-driven (not vectorized) — same code path as live.
- Replays bars + historical news (from cache) + simulated fills with cost model (commission + slippage).
- Cost model: stock slippage = 0.5 × spread + sqrt(volume) impact; crypto fees per venue; FX spread by pair.
- Outputs: equity curve, drawdown series, trade log, per-strategy attribution, walk-forward metrics.

Vectorbt is also used for fast strategy *research* (sweep parameters across thousands of configs), but final validation happens in the event-driven engine.

### Monitoring (`ea/monitoring/`) — sci-fi dashboard + reports

**Dashboard.** FastAPI server + single-page HTML/CSS/JS app served at `http://127.0.0.1:8765`. Futuristic aesthetic — deep-space dark theme, neon cyan/magenta accents, monospace typography (JetBrains Mono), glow effects, animated status indicators, terminal-style log stream. Built with no JS framework (vanilla + HTMX where it simplifies) so there's no build pipeline and the UI is fast and inspectable.

Layout (CSS Grid, ~5 panels):
- **Header bar:** profile badge (PAPER/LIVE), broker connection light, system clock, kill-switch button.
- **Account panel:** equity (large), cash, buying power, today's P&L (delta + %), PDT flag, blocked-trading indicator.
- **Positions panel:** sortable table — symbol, qty, entry, current, market value, unrealized P/L (colored).
- **Watchlist panel:** mini-charts (TradingView Lightweight Charts) for tracked symbols, click-to-expand.
- **News ticker:** scrolling list of recent LLM-scored news (Phase A.4+).
- **Signals panel:** strategy emissions with conviction/horizon (Phase A.5+).
- **Risk panel:** circuit breaker states, daily loss vs limit progress bar (Phase A.6+).
- **System monitor:** store stats, log stream tail, event bus heartbeat, LLM spend today (Phase A.4+).

Controls:
- **Kill switch** — cancel all open orders, close all positions, halt all strategies. Two-step confirm.
- **Manual order entry** — broker-agnostic order ticket (symbol, side, qty, type, limit). Risk manager validates before send (no bypass).
- **Strategy toggles** — pause/resume individual strategies (Phase A.5+).
- **Watchlist edit** — subscribe/unsubscribe symbols at runtime (Phase A.2+).
- **Refresh / auto-refresh toggle** — for snapshot data.

Live updates via Server-Sent Events (SSE) — simpler than WebSocket for one-way push, works through proxies, easy to reconnect.

**Alerts.** Console + optional webhook (Discord/Slack) on: circuit breaker trip, broker disconnect, large single-name loss, unfilled order timeout. Dashboard surfaces the same alerts in a banner.

**Reports.** Daily EOD report (markdown to disk + optional webhook): P&L, trades, attribution, comparison to backtest expectation, anomaly flags.

## Data flow examples

**Catalyst news entry (stocks):**
1. SEC EDGAR fetcher pulls new 8-K → ticker-tagged → LLM scores: catalyst=guidance_raise, materiality=0.8, sentiment=+0.7.
2. Above threshold → emitted as `NewsEvent` on bus.
3. `NewsMomentumStrategy.on_news` → returns `Signal(symbol=XYZ, side=long, horizon=5d, conviction=0.7)`.
4. Risk manager: sector cap OK, vol budget OK, correlation OK → sized at 0.8% portfolio risk.
5. Order manager: limit buy at mid → fills → position tracked → exit logic armed (time stop 5d, profit target 2×ATR, hard stop 1×ATR).

**End-of-day rebalance (cross-sectional momentum):**
1. Daily clock event at 15:45 ET.
2. `XSectionMomentumStrategy.on_bar` (daily bar) computes 6-month returns universe-wide, picks top decile / bottom decile, emits Signals to rebalance into target weights.
3. Risk manager scales weights to fit vol budget.
4. Order manager nets against current positions, emits delta orders.

## Tech choices and rationale

| Choice | Why |
|--------|-----|
| Python 3.11+ | Quant ecosystem, pandas/polars, async, LLM SDK. |
| DuckDB | Single-file analytical DB, fast on Parquet, no server. |
| `alpaca-py` | Official, modern, supports stocks+crypto. |
| `oandapyV20` | Stable OANDA REST/stream client. |
| `anthropic` SDK | LLM analyzer. Use prompt caching to keep costs down. |
| `pydantic` | Config + event validation. |
| `pytest` | Tests. |
| `loguru` | Structured logging without ceremony. |
| `vectorbt` (research only) | Fast parameter sweeps. |
| FastAPI | Monitoring dashboard. |

No Redis, no Postgres, no Kafka, no Docker — until paper-validation justifies the operational overhead.
