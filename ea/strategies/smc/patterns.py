"""Smart Money Concept pattern detectors — pure pandas, no side effects.

Implements:
- Fair Value Gap (FVG): 3-candle imbalance where the middle candle is impulsive
- Order Block (OB): last opposing candle before a strong impulsive move
- Liquidity sweep: wick that breaks a recent swing high/low and closes back inside
- Break of Structure (BOS): price closes beyond the most recent swing high/low

Each detector returns a list of dicts with `kind`, `side`, `low`, `high`, `ts`,
`mitigated` (bool — has price already returned through the zone).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd


Side = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class Zone:
    kind: str           # "FVG" | "OB" | "LIQ" | "BOS"
    side: Side
    low: float
    high: float
    ts: datetime
    mitigated: bool = False
    extra: dict | None = None

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2.0

    @property
    def height(self) -> float:
        return self.high - self.low


def _ts(idx) -> datetime:
    if hasattr(idx, "to_pydatetime"):
        return idx.to_pydatetime()
    return idx


def detect_fvgs(df: pd.DataFrame, lookback_bars: int = 60, min_size_pct: float = 0.001) -> list[Zone]:
    """Bullish FVG: c1.high < c3.low; bearish: c1.low > c3.high.

    `min_size_pct` filters out tiny gaps (relative to c2 price) to reduce noise.
    """
    if df is None or len(df) < 3:
        return []
    work = df.tail(lookback_bars + 2).reset_index()
    out: list[Zone] = []
    for i in range(len(work) - 2):
        c1 = work.iloc[i]
        c2 = work.iloc[i + 1]
        c3 = work.iloc[i + 2]
        c2_price = float(c2["close"])
        if c2_price <= 0:
            continue
        if c1["high"] < c3["low"]:
            size = float(c3["low"] - c1["high"])
            if size / c2_price < min_size_pct:
                continue
            zone = Zone(
                kind="FVG", side="bullish",
                low=float(c1["high"]), high=float(c3["low"]),
                ts=_ts(work.iloc[i + 1, 0]),
                mitigated=_zone_mitigated_after(df, _ts(work.iloc[i + 1, 0]),
                                                float(c1["high"]), float(c3["low"]), "bullish"),
            )
            out.append(zone)
        elif c1["low"] > c3["high"]:
            size = float(c1["low"] - c3["high"])
            if size / c2_price < min_size_pct:
                continue
            zone = Zone(
                kind="FVG", side="bearish",
                low=float(c3["high"]), high=float(c1["low"]),
                ts=_ts(work.iloc[i + 1, 0]),
                mitigated=_zone_mitigated_after(df, _ts(work.iloc[i + 1, 0]),
                                                float(c3["high"]), float(c1["low"]), "bearish"),
            )
            out.append(zone)
    return out


def detect_order_blocks(
    df: pd.DataFrame,
    lookback_bars: int = 60,
    impulse_n: int = 3,
    impulse_pct: float = 0.015,
) -> list[Zone]:
    """Last opposing candle before an impulsive move of `impulse_n` bars whose
    cumulative return exceeds `impulse_pct`.

    Bullish OB = last bearish candle before strong rally.
    Bearish OB = last bullish candle before strong drop.
    """
    if df is None or len(df) < impulse_n + 1:
        return []
    work = df.tail(lookback_bars + impulse_n + 2).reset_index()
    out: list[Zone] = []
    for i in range(len(work) - impulse_n):
        cur = work.iloc[i]
        impulse = work.iloc[i + 1: i + 1 + impulse_n]
        open_p = float(impulse["open"].iloc[0])
        close_p = float(impulse["close"].iloc[-1])
        if open_p <= 0:
            continue
        ret = (close_p - open_p) / open_p
        is_bear_candle = cur["close"] < cur["open"]
        is_bull_candle = cur["close"] > cur["open"]
        ts = _ts(work.iloc[i, 0])
        if ret > impulse_pct and is_bear_candle:
            out.append(Zone(
                kind="OB", side="bullish",
                low=float(cur["low"]), high=float(cur["high"]), ts=ts,
                mitigated=_zone_mitigated_after(df, ts, float(cur["low"]), float(cur["high"]), "bullish"),
                extra={"impulse_pct": ret},
            ))
        elif ret < -impulse_pct and is_bull_candle:
            out.append(Zone(
                kind="OB", side="bearish",
                low=float(cur["low"]), high=float(cur["high"]), ts=ts,
                mitigated=_zone_mitigated_after(df, ts, float(cur["low"]), float(cur["high"]), "bearish"),
                extra={"impulse_pct": ret},
            ))
    return out


def _swing_points(df: pd.DataFrame, window: int = 5) -> tuple[list[tuple[datetime, float]], list[tuple[datetime, float]]]:
    """Identify swing highs and lows using fractal-style window comparison."""
    highs: list[tuple[datetime, float]] = []
    lows: list[tuple[datetime, float]] = []
    if len(df) < 2 * window + 1:
        return highs, lows
    for i in range(window, len(df) - window):
        win = df.iloc[i - window: i + window + 1]
        center = df.iloc[i]
        if float(center["high"]) >= float(win["high"].max()):
            highs.append((_ts(df.index[i]), float(center["high"])))
        if float(center["low"]) <= float(win["low"].min()):
            lows.append((_ts(df.index[i]), float(center["low"])))
    return highs, lows


def detect_liquidity_sweeps(df: pd.DataFrame, lookback_bars: int = 60, swing_window: int = 5) -> list[Zone]:
    """Wick beyond a recent swing high/low that closes back inside.

    Bullish sweep: low pierces a recent swing low, close > swing low (stop hunt below).
    Bearish sweep: high pierces a recent swing high, close < swing high.
    """
    if df is None or len(df) < swing_window * 2 + 5:
        return []
    work_full = df.tail(lookback_bars + swing_window * 2 + 5)
    highs, lows = _swing_points(work_full, window=swing_window)
    out: list[Zone] = []
    work = work_full.tail(lookback_bars).reset_index()
    for i, row in work.iterrows():
        ts = _ts(row.iloc[0])
        # Bearish sweep: pierces a prior swing high above
        prior_highs = [h for h in highs if h[0] < ts]
        if prior_highs:
            recent_high = max(h[1] for h in prior_highs[-5:])
            if float(row["high"]) > recent_high and float(row["close"]) < recent_high:
                out.append(Zone(
                    kind="LIQ", side="bearish",
                    low=float(recent_high), high=float(row["high"]), ts=ts,
                    extra={"swept_level": recent_high},
                ))
        prior_lows = [l for l in lows if l[0] < ts]
        if prior_lows:
            recent_low = min(l[1] for l in prior_lows[-5:])
            if float(row["low"]) < recent_low and float(row["close"]) > recent_low:
                out.append(Zone(
                    kind="LIQ", side="bullish",
                    low=float(row["low"]), high=float(recent_low), ts=ts,
                    extra={"swept_level": recent_low},
                ))
    return out


def detect_bos(df: pd.DataFrame, swing_window: int = 5) -> Zone | None:
    """Most recent break of structure: latest close beyond the most recent prior swing.

    Returns one Zone (the broken level) with side = direction of break.
    """
    if df is None or len(df) < swing_window * 2 + 5:
        return None
    highs, lows = _swing_points(df.tail(120), window=swing_window)
    if not highs and not lows:
        return None
    last_close = float(df["close"].iloc[-1])
    last_ts = _ts(df.index[-1])

    last_high = max((h for h in highs if h[0] < last_ts), key=lambda x: x[0], default=None)
    last_low = min((l for l in lows if l[0] < last_ts), key=lambda x: x[0], default=None)

    candidates: list[Zone] = []
    if last_high is not None and last_close > last_high[1]:
        candidates.append(Zone(
            kind="BOS", side="bullish", low=last_high[1], high=last_close, ts=last_ts,
            extra={"broken_level": last_high[1]},
        ))
    if last_low is not None and last_close < last_low[1]:
        candidates.append(Zone(
            kind="BOS", side="bearish", low=last_close, high=last_low[1], ts=last_ts,
            extra={"broken_level": last_low[1]},
        ))
    if not candidates:
        return None
    return candidates[-1]


def _zone_mitigated_after(df: pd.DataFrame, ts: datetime, low: float, high: float, side: str) -> bool:
    """Has price returned to traverse the zone since `ts`?

    Bullish zone: mitigated if any subsequent low has dipped below or into the zone.
    Bearish zone: mitigated if any subsequent high has risen into the zone.
    """
    after = df.loc[df.index > ts] if hasattr(df.index, "tz") or True else df  # tolerant
    try:
        after = df[df.index > pd.Timestamp(ts)]
    except Exception:
        return False
    if after.empty:
        return False
    if side == "bullish":
        return bool((after["low"] <= high).any())
    return bool((after["high"] >= low).any())
