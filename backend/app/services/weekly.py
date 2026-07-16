"""Weekly delivery stock screener targeting ~8% weekly move potential."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..config import settings
from .market import WATCHLIST, compute_indicators, fetch_ohlcv


def _weekly_features(symbol: str) -> dict[str, Any] | None:
    try:
        df = fetch_ohlcv(symbol, period="6mo", interval="1d")
    except Exception:
        return None
    if len(df) < 40:
        return None

    ind = compute_indicators(df).dropna()
    if len(ind) < 30:
        return None

    close = ind["Close"]
    # Rolling 5-session returns distribution
    weekly_ret = close.pct_change(5).dropna()
    hit_rate = float((weekly_ret >= settings.weekly_target_return_pct / 100).mean())
    avg_weekly = float(weekly_ret.mean() * 100)
    vol = float(weekly_ret.std() * 100)
    last = ind.iloc[-1]
    price = float(last["Close"])

    # Momentum + mean-reversion blend score for next-week potential
    rsi = float(last["rsi"])
    macd_hist = float(last["macd_hist"])
    ema_bull = float(last["ema9"] > last["ema21"] > last["ema50"])
    distance_from_low = (price - float(ind["Close"].tail(20).min())) / price
    bb_pos = (price - float(last["bb_low"])) / max(float(last["bb_high"] - last["bb_low"]), 1e-6)

    # Higher score = more interesting for delivery swing toward target
    score = 0.0
    reasons: list[str] = []

    if hit_rate >= 0.15:
        score += 2 + hit_rate * 5
        reasons.append(f"Historically hit ≥{settings.weekly_target_return_pct}% in {hit_rate*100:.0f}% of weeks")
    if avg_weekly > 0.5:
        score += 1
        reasons.append(f"Avg 5-day return {avg_weekly:.2f}%")
    if ema_bull:
        score += 2
        reasons.append("Bullish EMA alignment")
    if 40 <= rsi <= 62:
        score += 1.5
        reasons.append(f"RSI room to run ({rsi:.1f})")
    elif rsi < 35:
        score += 1
        reasons.append(f"Oversold bounce candidate (RSI {rsi:.1f})")
    if macd_hist > 0:
        score += 1
        reasons.append("MACD histogram positive")
    if 0.2 <= bb_pos <= 0.7:
        score += 0.5
        reasons.append("Not extended at upper band")
    if vol > 12:
        score += 0.5
        reasons.append(f"Enough weekly volatility ({vol:.1f}%) to reach ~8%")
    elif vol < 4:
        score -= 1
        reasons.append("Low weekly volatility — 8% less realistic")

    # Expected move estimate (not a guarantee)
    expected = avg_weekly + 0.5 * vol * (1 if ema_bull else 0.3)
    expected = float(np.clip(expected, -5, 15))

    atr = float(last["atr"])
    return {
        "symbol": symbol,
        "price": round(price, 2),
        "score": round(score, 2),
        "expected_weekly_move_pct": round(expected, 2),
        "hist_hit_rate_8pct": round(hit_rate * 100, 1),
        "avg_weekly_return_pct": round(avg_weekly, 2),
        "weekly_vol_pct": round(vol, 2),
        "rsi": round(rsi, 2),
        "stop_loss": round(price - 1.8 * atr, 2),
        "target_8pct": round(price * (1 + settings.weekly_target_return_pct / 100), 2),
        "reasons": reasons,
        "horizon": "5 trading sessions (delivery)",
        "disclaimer": f"~{settings.weekly_target_return_pct}% is aspirational probability, not guaranteed.",
    }


def weekly_delivery_picks(top_n: int = 5, universe: list[str] | None = None) -> dict[str, Any]:
    symbols = [s for s in (universe or WATCHLIST) if s not in ("NIFTY", "BANKNIFTY")]
    scored: list[dict[str, Any]] = []
    for sym in symbols:
        feat = _weekly_features(sym)
        if feat:
            scored.append(feat)
    scored.sort(key=lambda x: (x["score"], x["expected_weekly_move_pct"]), reverse=True)
    top = scored[:top_n]
    return {
        "target_return_pct": settings.weekly_target_return_pct,
        "count": len(top),
        "picks": top,
        "method": "Momentum + historical 5-day hit-rate + RSI/MACD/EMA filter",
        "warning": (
            "8% in one week is aggressive. Most weeks you will not hit it. "
            "Use stop-loss, position sizing, and never risk money you cannot afford to lose."
        ),
    }
