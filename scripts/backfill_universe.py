"""One-shot backfill for all scanner universe symbols + watchlist.

Run once (or whenever you add new symbols) to seed the DuckDB bar store so the
scanner has historical data to analyse.

Usage:
    python scripts/backfill_universe.py              # last 90 days
    python scripts/backfill_universe.py --days 365   # last year
    python scripts/backfill_universe.py --days 180 --symbols TSLA,COIN
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make sure the project root is on the path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from ea.brokers.models import AssetClass
from ea.config import get_config
from ea.data.backfill import backfill_symbols
from ea.data.store import BarStore
from ea.logging import setup_logging

# ── Default universe (mirrors server.py DEFAULT_SCAN_*) ──────────────────────
STOCKS = [
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "SMH", "ARKK",
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD", "AVGO", "ORCL",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA",
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "DIS",
    "UNH", "JNJ", "LLY", "PFE", "ABBV",
    "XOM", "CVX", "BA", "CAT", "GE",
    "COIN", "PLTR", "SHOP", "NFLX", "UBER", "SNOW",
]
CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD"]
FOREX  = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD"]


def progress(done: int, total: int, r) -> None:
    status = "ok" if r.ok else f"FAIL ({r.error})"
    bar = "#" * int(done / total * 30) + "." * (30 - int(done / total * 30))
    print(f"\r[{bar}] {done}/{total}  {r.symbol:12s} {status}", end="", flush=True)
    if done == total:
        print()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill scanner universe")
    parser.add_argument("--days", type=int, default=90, help="Lookback days for 1D bars (default 90)")
    parser.add_argument("--hours-days", type=int, default=60, help="Lookback days for 1H bars (default 60, yfinance max 730)")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated extra symbols (stocks only)")
    parser.add_argument("--no-forex", action="store_true", help="Skip forex (uses yfinance)")
    parser.add_argument("--no-daily", action="store_true", help="Skip 1D backfill (1H only)")
    parser.add_argument("--no-hourly", action="store_true", help="Skip 1H backfill (1D only)")
    args = parser.parse_args()

    setup_logging()
    config = get_config()
    store  = BarStore()

    end       = datetime.now(timezone.utc)
    start_day = end - timedelta(days=args.days)
    start_hr  = end - timedelta(days=args.hours_days)

    extra_stocks = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    all_stocks = list(dict.fromkeys(STOCKS + extra_stocks))  # dedupe, preserve order

    print(f"\nBackfilling 1D ({args.days}d) + 1H ({args.hours_days}d) bars")
    print(f"  Stocks : {len(all_stocks)} symbols")
    print(f"  Crypto : {len(CRYPTO)} symbols")
    print(f"  Forex  : {0 if args.no_forex else len(FOREX)} symbols  (yfinance fallback)")
    print()

    timeframes = []
    if not args.no_daily:  timeframes.append(("1Day",  start_day))
    if not args.no_hourly: timeframes.append(("1Hour", start_hr))

    for tf, tf_start in timeframes:
        print(f"\n==== Timeframe: {tf} ({(end - tf_start).days}d back) ====")

        print(f"-- Stocks ({tf}) --")
        await backfill_symbols(
            config, store, all_stocks,
            start=tf_start, end=end, timeframe=tf,
            asset_class=AssetClass.STOCK, concurrency=4, progress_cb=progress,
        )

        print(f"-- Crypto ({tf}) --")
        await backfill_symbols(
            config, store, CRYPTO,
            start=tf_start, end=end, timeframe=tf,
            asset_class=AssetClass.CRYPTO, concurrency=2, progress_cb=progress,
        )

        if not args.no_forex:
            print(f"-- Forex ({tf}) --")
            await backfill_symbols(
                config, store, FOREX,
                start=tf_start, end=end, timeframe=tf,
                asset_class=AssetClass.FOREX, concurrency=2, progress_cb=progress,
            )

    # -- Summary --------------------------------------------------------------
    total_rows = store.row_count()
    n_stocks   = len(store.list_symbols(AssetClass.STOCK))
    n_crypto   = len(store.list_symbols(AssetClass.CRYPTO))
    n_forex    = len(store.list_symbols(AssetClass.FOREX))
    print(f"\nStore now has {total_rows:,} rows across "
          f"{n_stocks} stocks / {n_crypto} crypto / {n_forex} forex symbols.")
    print("Restart the dashboard (or click Scan now) to see scanner results.\n")


if __name__ == "__main__":
    asyncio.run(main())
