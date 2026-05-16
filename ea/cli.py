"""Top-level CLI. Sub-commands fill in across phases."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pandas as pd

from ea.config import get_config

import typer
from rich.console import Console
from rich.table import Table

from ea import __version__
from ea.config import get_config
from ea.logging import logger, setup_logging

app = typer.Typer(help="EA — multi-asset swing trading system", no_args_is_help=True)
data_app = typer.Typer(help="Market data: backfill, universe, asset listing", no_args_is_help=True)
app.add_typer(data_app, name="data")
console = Console()


def _require_alpaca_creds():
    cfg = get_config()
    if cfg.env.alpaca_key_id is None or cfg.env.alpaca_secret_key is None:
        console.print(
            "[red]Missing Alpaca credentials.[/red] "
            "Copy [cyan].env.example[/cyan] to [cyan].env[/cyan] and set "
            "[cyan]ALPACA_KEY_ID[/cyan] and [cyan]ALPACA_SECRET_KEY[/cyan]."
        )
        raise typer.Exit(code=1)
    return cfg


@app.command()
def version() -> None:
    """Print version."""
    console.print(f"ea {__version__}")


@app.command()
def status() -> None:
    """Connect to Alpaca and print account + positions."""
    setup_logging()
    cfg = _require_alpaca_creds()

    from ea.brokers.alpaca.client import AlpacaBroker

    broker = AlpacaBroker(cfg)

    async def _run():
        return await asyncio.gather(broker.get_account(), broker.get_positions())

    account, positions = asyncio.run(_run())

    profile_label = "[bold red]LIVE[/bold red]" if cfg.is_live else "[bold green]paper[/bold green]"
    console.print(f"\n[bold]Alpaca account[/bold] ({profile_label})")

    acct_table = Table(show_header=False, box=None)
    acct_table.add_row("Account ID:", account.account_id)
    acct_table.add_row("Equity:", f"${account.equity:,.2f}")
    acct_table.add_row("Cash:", f"${account.cash:,.2f}")
    acct_table.add_row("Buying power:", f"${account.buying_power:,.2f}")
    acct_table.add_row("Portfolio value:", f"${account.portfolio_value:,.2f}")
    acct_table.add_row("PDT:", str(account.pattern_day_trader))
    acct_table.add_row(
        "Trading blocked:",
        f"[red]{account.trading_blocked}[/red]" if account.trading_blocked else "False",
    )
    console.print(acct_table)

    console.print(f"\n[bold]Positions[/bold] ({len(positions)})")
    if not positions:
        console.print("  [dim]none[/dim]")
    else:
        pos_table = Table()
        pos_table.add_column("Symbol")
        pos_table.add_column("Class")
        pos_table.add_column("Qty", justify="right")
        pos_table.add_column("Entry", justify="right")
        pos_table.add_column("Current", justify="right")
        pos_table.add_column("Mkt Value", justify="right")
        pos_table.add_column("Unrealized P/L", justify="right")
        for p in positions:
            pl = p.unrealized_pl or 0
            pl_str = f"[green]${pl:,.2f}[/green]" if pl >= 0 else f"[red]${pl:,.2f}[/red]"
            pos_table.add_row(
                p.symbol, p.asset_class.value,
                f"{p.quantity:,.4f}",
                f"${p.avg_entry_price:,.2f}",
                f"${p.current_price:,.2f}" if p.current_price else "-",
                f"${p.market_value:,.2f}" if p.market_value else "-",
                pl_str,
            )
        console.print(pos_table)

    logger.info("status command completed: equity={}, positions={}", account.equity, len(positions))


# --- `ea data` sub-commands ---

@data_app.command("assets")
def data_assets(
    limit: int = typer.Option(50, help="Max rows to print"),
    exclude_otc: bool = typer.Option(True, help="Filter out OTC/pink"),
) -> None:
    """List Alpaca-tradable US equity assets (post-hygiene filter)."""
    setup_logging()
    cfg = _require_alpaca_creds()
    from ea.data.universe import list_tradable_assets

    assets = asyncio.run(list_tradable_assets(cfg, exclude_otc=exclude_otc))
    console.print(f"\n[bold]Tradable assets: {len(assets)}[/bold] (showing first {limit})")
    t = Table()
    t.add_column("Symbol"); t.add_column("Name"); t.add_column("Exchange")
    t.add_column("Shortable"); t.add_column("Fractionable")
    for a in assets[:limit]:
        t.add_row(a.symbol, (a.name or "")[:50], a.exchange,
                  str(a.shortable), str(a.fractionable))
    console.print(t)


@data_app.command("backfill")
def data_backfill(
    symbols: str = typer.Option(..., help="Comma-separated symbols, e.g. SPY,QQQ,AAPL"),
    start: str = typer.Option("2020-01-01", help="Start date YYYY-MM-DD"),
    end: str | None = typer.Option(None, help="End date YYYY-MM-DD (default: today)"),
    timeframe: str = typer.Option("1Day", help="1Min|5Min|15Min|1Hour|1Day"),
    asset_class: str = typer.Option("stock", help="stock|crypto"),
    concurrency: int = typer.Option(4, help="Parallel symbol fetches"),
) -> None:
    """Backfill historical OHLCV bars from Alpaca (yfinance fallback for daily stocks)."""
    setup_logging()
    cfg = _require_alpaca_creds()
    from ea.brokers.models import AssetClass
    from ea.data.backfill import backfill_symbols
    from ea.data.store import BarStore

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = (
        datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        if end else datetime.now(timezone.utc)
    )
    ac = AssetClass(asset_class.lower())
    store = BarStore()

    results = asyncio.run(backfill_symbols(
        cfg, store, sym_list,
        start=start_dt, end=end_dt,
        timeframe=timeframe, asset_class=ac,
        concurrency=concurrency,
    ))

    t = Table(title="Backfill results")
    t.add_column("Symbol"); t.add_column("Rows", justify="right"); t.add_column("Source"); t.add_column("Status")
    for r in results:
        status = "[green]ok[/green]" if r.ok else f"[red]{r.error}[/red]"
        t.add_row(r.symbol, str(r.rows_written), r.source, status)
    console.print(t)
    total = sum(r.rows_written for r in results)
    console.print(f"\nTotal rows written: [bold]{total:,}[/bold] (store: {store.path})")


@data_app.command("backfill-universe")
def data_backfill_universe(
    top_n: int = typer.Option(500, help="Backfill top-N most liquid US stocks (set 0 for all tradable; expect ~10k symbols)"),
    start: str = typer.Option("2023-01-01", help="Start date YYYY-MM-DD"),
    end: str | None = typer.Option(None, help="End date (default: today)"),
    concurrency: int = typer.Option(8, help="Parallel symbol fetches (Alpaca rate-limit safe at 8)"),
    bootstrap_days: int = typer.Option(30, help="Days of recent bars to pull for liquidity ranking"),
) -> None:
    """Bulk-backfill the universe of US-equity Alpaca-tradable assets.

    Two-pass:
      1. Fetch all tradable assets from Alpaca; pull recent bars for ranking.
      2. Rank by 20d ADV; backfill top_n symbols' full history.

    With top_n=0 every active US stock gets backfilled (~10k symbols, ~10-20 min,
    several MB of data).
    """
    setup_logging()
    cfg = _require_alpaca_creds()
    from ea.brokers.models import AssetClass
    from ea.data.backfill import backfill_symbols
    from ea.data.store import BarStore
    from ea.data.universe import list_tradable_assets, scan_liquid_universe

    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = (datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
              if end else datetime.now(timezone.utc))
    store = BarStore()

    console.print(f"[bold]Step 1[/bold] · Listing Alpaca tradable assets...")
    assets = asyncio.run(list_tradable_assets(cfg))
    all_syms = [a.symbol for a in assets]
    console.print(f"  Found [cyan]{len(all_syms)}[/cyan] tradable US-equity assets")

    if top_n == 0:
        target_syms = all_syms
        console.print(f"\n[bold]Step 2[/bold] · Backfilling ALL {len(target_syms)} symbols (this may take a while)...")
    else:
        # Bootstrap: get recent bars across all assets so we can rank by liquidity
        bootstrap_start = datetime.now(timezone.utc) - pd.Timedelta(days=bootstrap_days * 2)
        console.print(
            f"\n[bold]Step 2a[/bold] · Bootstrap-fetching {bootstrap_days}d of bars "
            f"for {len(all_syms)} symbols to rank by liquidity (concurrency={concurrency})..."
        )
        progress_every = max(50, len(all_syms) // 20)
        def _prog(done, total, r):
            if done % progress_every == 0 or done == total:
                console.print(f"  ... {done}/{total} ({r.symbol}: {r.rows_written} rows)")
        boot = asyncio.run(backfill_symbols(
            cfg, store, all_syms, start=bootstrap_start, end=end_dt,
            timeframe="1Day", asset_class=AssetClass.STOCK,
            concurrency=concurrency, progress_cb=_prog,
        ))
        ok_boot = sum(1 for r in boot if r.ok)
        console.print(f"  Bootstrap done: {ok_boot}/{len(boot)} symbols have recent bars")

        console.print(f"\n[bold]Step 2b[/bold] · Ranking by 20d ADV (price >= ${cfg.profile.universe.stocks.min_price}, ADV >= ${cfg.profile.universe.stocks.min_avg_dollar_volume:,.0f})...")
        ranked = scan_liquid_universe(cfg, store, lookback_days=20)
        console.print(f"  [cyan]{len(ranked)}[/cyan] passed liquidity filter")
        target_syms = [e.symbol for e in ranked[:top_n]]
        console.print(f"  Selected top [bold]{len(target_syms)}[/bold] for full backfill")

    console.print(f"\n[bold]Step 3[/bold] · Full-history backfill from {start_dt.date()} for {len(target_syms)} symbols...")
    progress_every = max(20, len(target_syms) // 25)
    def _prog2(done, total, r):
        if done % progress_every == 0 or done == total:
            console.print(f"  ... {done}/{total} ({r.symbol}: {r.rows_written} rows)")
    final = asyncio.run(backfill_symbols(
        cfg, store, target_syms, start=start_dt, end=end_dt,
        timeframe="1Day", asset_class=AssetClass.STOCK,
        concurrency=concurrency, progress_cb=_prog2,
    ))
    ok = sum(1 for r in final if r.ok)
    fail = [r.symbol for r in final if not r.ok][:10]
    total_rows = sum(r.rows_written for r in final)
    console.print(f"\n[bold green]Done.[/bold green] {ok}/{len(final)} symbols backfilled, {total_rows:,} rows written.")
    if fail:
        console.print(f"  First failures: {fail}")


@data_app.command("universe")
def data_universe(
    lookback_days: int = typer.Option(20, help="Days of history for ADV calc"),
    limit: int = typer.Option(50, help="Max rows to print"),
) -> None:
    """Apply liquidity filter to symbols in the local store; print top N by ADV."""
    setup_logging()
    cfg = get_config()
    from ea.data.store import BarStore
    from ea.data.universe import scan_liquid_universe

    store = BarStore()
    entries = scan_liquid_universe(cfg, store, lookback_days=lookback_days)
    console.print(
        f"\n[bold]Liquid universe: {len(entries)} symbols[/bold] "
        f"(price>=${cfg.profile.universe.stocks.min_price:.2f}, "
        f"ADV>=${cfg.profile.universe.stocks.min_avg_dollar_volume:,.0f})"
    )
    t = Table()
    t.add_column("Symbol"); t.add_column("Last", justify="right")
    t.add_column("Avg $ Vol (20d)", justify="right"); t.add_column("Days", justify="right")
    for e in entries[:limit]:
        t.add_row(e.symbol, f"${e.last_price:,.2f}", f"${e.avg_dollar_volume:,.0f}", str(e.days_of_data))
    console.print(t)


@data_app.command("stream")
def data_stream(
    symbols: str = typer.Option(..., help="Comma-separated symbols, e.g. BTC/USD,ETH/USD or SPY,QQQ"),
    asset_class: str = typer.Option("crypto", help="stock|crypto (crypto ticks 24/7)"),
    duration: int = typer.Option(0, help="Seconds to run; 0 = until Ctrl+C"),
) -> None:
    """Tail the Alpaca live stream to stdout. Useful for verifying connectivity."""
    setup_logging()
    cfg = _require_alpaca_creds()
    from ea.brokers.alpaca.stream import AlpacaStreamRunner
    from ea.brokers.models import AssetClass, BarEvent
    from ea.eventbus import get_bus

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    ac = AssetClass(asset_class.lower())

    async def _run():
        bus = get_bus()
        bus.bind_loop()
        runner = AlpacaStreamRunner(cfg, sym_list, asset_class=ac, bus=bus)
        runner.start()
        console.print(f"\n[cyan]Stream started:[/cyan] {ac.value} · {sym_list}")
        console.print("[dim]Ctrl+C to stop[/dim]\n")

        q = bus.subscribe(maxsize=500)
        deadline = None if duration <= 0 else asyncio.get_event_loop().time() + duration
        count = 0
        try:
            while True:
                if deadline is not None and asyncio.get_event_loop().time() >= deadline:
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(event, BarEvent):
                    count += 1
                    console.print(
                        f"[dim]{event.timestamp.strftime('%H:%M:%S')}[/dim] "
                        f"[cyan]{event.symbol:<10}[/cyan] "
                        f"O={event.open} H={event.high} L={event.low} "
                        f"[bold]C={event.close}[/bold] V={event.volume}"
                    )
        finally:
            bus.unsubscribe(q)
            runner.stop()
            console.print(f"\nStream stopped. [bold]{count}[/bold] bars received.")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


news_app = typer.Typer(help="News pipeline: poll and inspect", no_args_is_help=True)
app.add_typer(news_app, name="news")


@news_app.command("poll")
def news_poll(
    sources: str = typer.Option("edgar,rss,yahoo", help="Comma-separated: edgar|rss|yahoo"),
    watchlist: str = typer.Option("SPY,QQQ,AAPL,MSFT,NVDA", help="Symbols for Yahoo per-ticker"),
    limit: int = typer.Option(20, help="Max items to print"),
) -> None:
    """One-shot poll from selected sources; prints items + ticker tags."""
    setup_logging()
    from ea.eventbus import EventBus
    from ea.news.dedupe import DedupeCache
    from ea.news.poller import NewsPoller

    src_set = {s.strip().lower() for s in sources.split(",") if s.strip()}
    wl = [s.strip().upper() for s in watchlist.split(",") if s.strip()]

    async def _run():
        bus = EventBus()
        bus.bind_loop()
        q = bus.subscribe(maxsize=500)
        poller = NewsPoller(bus=bus, cache=DedupeCache(), watchlist_provider=lambda: wl)
        totals = {}
        if "edgar" in src_set: totals["edgar"] = await poller.poll_edgar_once()
        if "rss" in src_set:   totals["rss"] = await poller.poll_rss_once()
        if "yahoo" in src_set: totals["yahoo"] = await poller.poll_yahoo_once()

        items = []
        while not q.empty():
            items.append(q.get_nowait())

        t = Table(title=f"News (new={sum(totals.values())}; per-source={totals})")
        t.add_column("Time"); t.add_column("Src"); t.add_column("Tickers")
        t.add_column("Title", overflow="fold")
        for ev in items[:limit]:
            t.add_row(
                ev.item.published_at.strftime("%Y-%m-%d %H:%M"),
                ev.item.source.value,
                ",".join(ev.tickers) or "-",
                ev.item.title[:120],
            )
        console.print(t)

    asyncio.run(_run())


@data_app.command("info")
def data_info() -> None:
    """Show stats about the local bar store."""
    setup_logging()
    from ea.brokers.models import AssetClass
    from ea.data.store import BarStore

    store = BarStore()
    total = store.row_count()
    stocks = store.list_symbols(AssetClass.STOCK)
    crypto = store.list_symbols(AssetClass.CRYPTO)
    console.print(f"Store: [cyan]{store.path}[/cyan]")
    console.print(f"Total rows: [bold]{total:,}[/bold]")
    console.print(f"Stock symbols: {len(stocks)}")
    console.print(f"Crypto symbols: {len(crypto)}")


# --- Dashboard ---

@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8765, help="Bind port"),
) -> None:
    """Launch the sci-fi web dashboard."""
    _require_alpaca_creds()
    from ea.monitoring.server import serve

    console.print(f"\n[bold cyan]EA dashboard[/bold cyan] -> http://{host}:{port}")
    console.print("[dim]Ctrl+C to stop[/dim]\n")
    serve(host=host, port=port)


# --- Phase stubs ---

@app.command()
def backtest(
    symbols: str = typer.Option(..., help="Comma-separated symbols, e.g. SPY,QQQ,AAPL"),
    start: str = typer.Option("2024-01-01"),
    end: str | None = typer.Option(None, help="default: today"),
    starting_equity: float = typer.Option(10_000.0),
    strategies: str = typer.Option(
        "xsection_momentum",
        help="Comma-separated. Available: xsection_momentum, news_momentum, smc",
    ),
    timeframe: str = typer.Option("1Day", help="Bar timeframe: 1Min, 5Min, 15Min, 1Hour, 1Day"),
    asset_class: str = typer.Option("stock", help="stock | crypto | forex"),
    walk_forward: bool = typer.Option(False, help="Segmented out-of-sample windows, compounded"),
    window_days: int = typer.Option(90, help="Walk-forward window length (days)"),
    report: bool = typer.Option(False, help="Also write markdown+JSON report to ./reports"),
) -> None:
    """Run a daily-bar backtest using bars in the local store."""
    setup_logging()
    cfg = get_config()
    from datetime import datetime as DT
    from ea.backtest.engine import BacktestEngine
    from ea.backtest.report import write_report
    from ea.backtest.walkforward import run_walk_forward
    from ea.brokers.models import AssetClass
    from ea.data.store import BarStore
    from ea.strategies.news_momentum import NewsMomentumStrategy
    from ea.strategies.smc.strategy import SMCStrategy
    from ea.strategies.smc.scalp import SMCScalpStrategy
    from ea.strategies.xsection_momentum import CrossSectionalMomentumStrategy

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    start_dt = DT.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = (DT.fromisoformat(end).replace(tzinfo=timezone.utc) if end else datetime.now(timezone.utc))

    # SMC strategies filter on bar timeframe, so they must be constructed for
    # the same timeframe the engine replays or they never fire.
    strat_map = {
        "xsection_momentum": CrossSectionalMomentumStrategy,
        "news_momentum": NewsMomentumStrategy,
        "smc": lambda: SMCStrategy(timeframe=timeframe),
        "smc_scalp": lambda: SMCScalpStrategy(timeframe=timeframe),
    }
    names = []
    for s in strategies.split(","):
        s = s.strip()
        if s not in strat_map:
            console.print(f"[red]unknown strategy: {s}[/red]")
            raise typer.Exit(1)
        names.append(s)

    def make_strategies():
        return [strat_map[n]() for n in names]

    ac_map = {"stock": AssetClass.STOCK, "crypto": AssetClass.CRYPTO, "forex": AssetClass.FOREX}
    ac = ac_map.get(asset_class.strip().lower())
    if ac is None:
        console.print(f"[red]unknown asset class: {asset_class}[/red]")
        raise typer.Exit(1)

    store = BarStore()
    if walk_forward:
        result = run_walk_forward(
            cfg, store, make_strategies, sym_list, start_dt, end_dt,
            window_days=window_days, starting_equity=starting_equity,
            asset_class=ac, timeframe=timeframe,
        )
        console.print(f"\n[bold cyan]Walk-forward result[/bold cyan]")
        for line in result.summary_lines():
            console.print(line)
    else:
        engine = BacktestEngine(cfg, store, make_strategies(), starting_equity=starting_equity)
        result = engine.run(sym_list, start_dt, end_dt, ac, timeframe)
        console.print(f"\n[bold cyan]Backtest result[/bold cyan]")
        for line in result.summary_lines():
            console.print(line)
        if result.by_strategy:
            console.print("\n[bold]By strategy:[/bold]")
            for name, st in result.by_strategy.items():
                wr = (st["wins"] / st["trades"] * 100) if st["trades"] else 0
                console.print(f"  {name}: {st['trades']} trades, ${st['pnl']:+,.2f} pnl, {wr:.1f}% wins")

    if report:
        path = write_report(result, label="walkforward" if walk_forward else "backtest")
        console.print(f"\n[dim]report: {path}[/dim]")


@app.command()
def report() -> None:
    """Write today's EOD report (account/positions/risk) to ./reports."""
    setup_logging()
    _require_alpaca_creds()
    import asyncio as _aio
    from ea.brokers.alpaca.client import AlpacaBroker
    from ea.data.store import BarStore
    from ea.monitoring import state as state_mod
    from ea.monitoring.reports import write_eod_report

    cfg = get_config()
    state_mod._state = state_mod.DashboardState(
        config=cfg, broker=AlpacaBroker(cfg), store=BarStore(),
    )

    async def _go():
        return await write_eod_report(order_mgr=None, signal_consumer=None, risk=None)

    path = _aio.run(_go())
    console.print(f"[green]EOD report written:[/green] {path}")


@app.command()
def paper(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8765),
    autosubmit: bool = typer.Option(False, help="Auto-submit signals to broker (paper)"),
) -> None:
    """Run paper trading via the dashboard server (full stack: news, strategies, risk, orders)."""
    _require_alpaca_creds()
    from ea.monitoring.server import serve

    console.print(f"\n[bold cyan]EA paper trading[/bold cyan] -> http://{host}:{port}")
    console.print(f"[dim]autosubmit={autosubmit} · Ctrl+C to stop[/dim]\n")
    serve(host=host, port=port, autosubmit=autosubmit)


@app.command()
def live() -> None:
    """Run live trading. (Stub — gated behind Phase D.)"""
    console.print("[red]live: not yet implemented (Phase D — paper validation gates required first)[/red]")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
