"""Market data + technical analysis for NSE cash stocks."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import httpx
import numpy as np
import pandas as pd
import yfinance as yf
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import OnBalanceVolumeIndicator


NSE_SUFFIX = ".NS"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Liquid F&O / cash names commonly used for intraday
WATCHLIST = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC",
    "BHARTIARTL", "AXISBANK", "KOTAKBANK", "LT", "BAJFINANCE", "MARUTI",
    "TATAMOTORS", "SUNPHARMA", "WIPRO", "HCLTECH", "ASIANPAINT", "TITAN",
    "ULTRACEMCO", "NTPC", "POWERGRID", "ONGC", "COALINDIA", "ADANIENT",
    "NIFTY", "BANKNIFTY",
]


@dataclass
class SignalResult:
    symbol: str
    price: float
    signal: str  # BUY | SELL | HOLD
    confidence: float
    timeframe: str
    reasons: list[str]
    indicators: dict[str, Any]
    stop_loss: float | None = None
    target: float | None = None


def _yf_symbol(symbol: str) -> str:
    s = symbol.upper().replace(" ", "")
    if s in ("NIFTY", "NIFTY50"):
        return "^NSEI"
    if s in ("BANKNIFTY", "NIFTYBANK"):
        return "^NSEBANK"
    if s.endswith(".NS") or s.endswith(".BO") or s.startswith("^"):
        return s
    return f"{s}{NSE_SUFFIX}"


def _period_to_range(period: str) -> str:
    mapping = {
        "1d": "1d",
        "5d": "5d",
        "1mo": "1mo",
        "3mo": "3mo",
        "6mo": "6mo",
        "1y": "1y",
        "2y": "2y",
    }
    return mapping.get(period, period)


def _fetch_yahoo_chart(symbol: str, period: str = "3mo", interval: str = "1d") -> tuple[pd.DataFrame, dict[str, Any]]:
    """Direct Yahoo chart API — works when yfinance library is blocked."""
    ysym = _yf_symbol(symbol)
    params = {"interval": interval, "range": _period_to_range(period)}
    url = YAHOO_CHART_URL.format(symbol=ysym)
    with httpx.Client(timeout=20, headers=HTTP_HEADERS, follow_redirects=True) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        payload = resp.json()

    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise ValueError(f"No chart data for {symbol}")

    block = result[0]
    meta = block.get("meta") or {}
    timestamps = block.get("timestamp") or []
    quote = (block.get("indicators") or {}).get("quote") or [{}]
    q0 = quote[0] if quote else {}

    if not timestamps:
        raise ValueError(f"No timestamps for {symbol}")

    idx = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Kolkata")
    df = pd.DataFrame(
        {
            "Open": q0.get("open", []),
            "High": q0.get("high", []),
            "Low": q0.get("low", []),
            "Close": q0.get("close", []),
            "Volume": q0.get("volume", []),
        },
        index=idx,
    )
    df = df.dropna()
    if df.empty:
        raise ValueError(f"Empty chart rows for {symbol}")

    df.attrs["demo"] = False
    df.attrs["source"] = "yahoo_chart"
    return df, meta


def _live_price_from_meta(meta: dict[str, Any], df: pd.DataFrame) -> float:
    for key in ("regularMarketPrice", "previousClose", "chartPreviousClose"):
        val = meta.get(key)
        if val:
            return float(val)
    return float(df["Close"].iloc[-1])


def _synthetic_ohlcv(symbol: str, bars: int = 90, interval: str = "1d") -> pd.DataFrame:
    """Offline demo candles when Yahoo is blocked / unavailable."""
    seed = sum(ord(c) for c in symbol.upper())
    rng = np.random.default_rng(seed)
    base = 100 + (seed % 4000)
    rets = rng.normal(0.0008, 0.015, bars)
    close = base * np.cumprod(1 + rets)
    high = close * (1 + rng.uniform(0.002, 0.02, bars))
    low = close * (1 - rng.uniform(0.002, 0.02, bars))
    open_ = np.roll(close, 1)
    open_[0] = base
    vol = rng.integers(200_000, 2_000_000, bars)
    freq = "5min" if "m" in interval else "B"
    idx = pd.date_range(end=pd.Timestamp.now(), periods=bars, freq=freq)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


def fetch_ohlcv(symbol: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame:
    # 1) Direct Yahoo chart API (most reliable on Indian networks)
    try:
        df, _ = _fetch_yahoo_chart(symbol, period=period, interval=interval)
        return df
    except Exception:
        pass

    # 2) yfinance library fallback
    ysym = _yf_symbol(symbol)
    df = pd.DataFrame()
    try:
        ticker = yf.Ticker(ysym)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
    except Exception:
        df = pd.DataFrame()

    if df is None or df.empty:
        try:
            df = yf.download(
                ysym,
                period=period,
                interval=interval,
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
        except Exception:
            df = pd.DataFrame()

    if df is not None and not df.empty:
        df = df.rename(columns=str.title)
        needed = {"Open", "High", "Low", "Close", "Volume"}
        missing = needed - set(df.columns)
        if not missing:
            df = df.dropna()
            df.attrs["demo"] = False
            df.attrs["source"] = "yfinance"
            return df

    # 3) Synthetic only as last resort (not for production pricing)
    bars = 78 if "m" in interval else 90
    df = _synthetic_ohlcv(symbol, bars=bars, interval=interval)
    df.attrs["demo"] = True
    df.attrs["source"] = "synthetic"
    df = df.rename(columns=str.title)
    return df.dropna()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"]
    high = out["High"]
    low = out["Low"]
    volume = out["Volume"]

    out["ema9"] = EMAIndicator(close, 9).ema_indicator()
    out["ema21"] = EMAIndicator(close, 21).ema_indicator()
    out["ema50"] = EMAIndicator(close, 50).ema_indicator()
    macd = MACD(close)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()
    out["rsi"] = RSIIndicator(close, 14).rsi()
    stoch = StochasticOscillator(high, low, close)
    out["stoch_k"] = stoch.stoch()
    out["stoch_d"] = stoch.stoch_signal()
    bb = BollingerBands(close)
    out["bb_high"] = bb.bollinger_hband()
    out["bb_low"] = bb.bollinger_lband()
    out["bb_mid"] = bb.bollinger_mavg()
    out["atr"] = AverageTrueRange(high, low, close, 14).average_true_range()
    out["adx"] = ADXIndicator(high, low, close, 14).adx()
    out["obv"] = OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    out["vwap_approx"] = ((high + low + close) / 3 * volume).cumsum() / volume.replace(0, np.nan).cumsum()
    return out


def analyze_cash(symbol: str, timeframe: str = "intraday") -> SignalResult:
    """Score BUY/SELL/HOLD from stacked technicals."""
    if timeframe == "intraday":
        period, interval = "5d", "5m"
    elif timeframe == "swing":
        period, interval = "3mo", "1d"
    else:
        period, interval = "6mo", "1d"

    try:
        df = fetch_ohlcv(symbol, period=period, interval=interval)
    except Exception:
        # Fallback to daily if intraday bars unavailable
        df = fetch_ohlcv(symbol, period="3mo", interval="1d")
        timeframe = "daily-fallback"

    demo = bool(getattr(df, "attrs", {}).get("demo"))
    ind = compute_indicators(df).dropna()
    if ind.empty:
        raise ValueError(f"Insufficient data for {symbol}")

    row = ind.iloc[-1]
    prev = ind.iloc[-2]
    price = float(row["Close"])
    reasons: list[str] = []
    score = 0.0
    if demo:
        reasons.append("Demo/synthetic candles (live Yahoo feed unavailable)")

    # Trend: EMA stack
    if row["ema9"] > row["ema21"] > row["ema50"]:
        score += 2
        reasons.append("Bullish EMA stack (9 > 21 > 50)")
    elif row["ema9"] < row["ema21"] < row["ema50"]:
        score -= 2
        reasons.append("Bearish EMA stack (9 < 21 < 50)")

    if row["ema9"] > prev["ema9"] and price > row["ema21"]:
        score += 1
        reasons.append("Price above EMA21 with rising EMA9")
    elif price < row["ema21"]:
        score -= 1
        reasons.append("Price below EMA21")

    # MACD
    if row["macd"] > row["macd_signal"] and row["macd_hist"] > 0:
        score += 1.5
        reasons.append("MACD bullish crossover / positive histogram")
    elif row["macd"] < row["macd_signal"] and row["macd_hist"] < 0:
        score -= 1.5
        reasons.append("MACD bearish crossover / negative histogram")

    # RSI
    rsi = float(row["rsi"])
    if 45 <= rsi <= 65:
        score += 0.5
        reasons.append(f"RSI healthy momentum ({rsi:.1f})")
    elif rsi < 30:
        score += 1
        reasons.append(f"RSI oversold bounce zone ({rsi:.1f})")
    elif rsi > 70:
        score -= 1.5
        reasons.append(f"RSI overbought ({rsi:.1f})")

    # Stochastic
    if row["stoch_k"] < 20 and row["stoch_k"] > prev["stoch_k"]:
        score += 1
        reasons.append("Stochastic turning up from oversold")
    elif row["stoch_k"] > 80 and row["stoch_k"] < prev["stoch_k"]:
        score -= 1
        reasons.append("Stochastic turning down from overbought")

    # Bollinger
    if price <= float(row["bb_low"]):
        score += 1
        reasons.append("Near / below lower Bollinger band")
    elif price >= float(row["bb_high"]):
        score -= 1
        reasons.append("Near / above upper Bollinger band")

    # ADX trend strength
    adx = float(row["adx"]) if not np.isnan(row["adx"]) else 0
    if adx >= 25:
        reasons.append(f"Strong trend (ADX {adx:.1f})")
        score *= 1.15
    else:
        reasons.append(f"Weak / sideways trend (ADX {adx:.1f})")

    atr = float(row["atr"]) if not np.isnan(row["atr"]) else price * 0.015

    if score >= 2:
        signal = "BUY"
    elif score <= -2:
        signal = "SELL"
    else:
        signal = "HOLD"

    confidence = min(95.0, abs(score) / 8 * 100)
    if demo:
        confidence = min(confidence, 55.0)
    if signal == "BUY":
        stop = round(price - 1.5 * atr, 2)
        target = round(price + 2.5 * atr, 2)
    elif signal == "SELL":
        stop = round(price + 1.5 * atr, 2)
        target = round(price - 2.5 * atr, 2)
    else:
        stop = target = None

    indicators = {
        "rsi": round(rsi, 2),
        "macd": round(float(row["macd"]), 4),
        "macd_signal": round(float(row["macd_signal"]), 4),
        "ema9": round(float(row["ema9"]), 2),
        "ema21": round(float(row["ema21"]), 2),
        "ema50": round(float(row["ema50"]), 2),
        "adx": round(adx, 2),
        "atr": round(atr, 2),
        "stoch_k": round(float(row["stoch_k"]), 2),
        "bb_high": round(float(row["bb_high"]), 2),
        "bb_low": round(float(row["bb_low"]), 2),
        "demo_data": demo,
    }

    return SignalResult(
        symbol=symbol.upper(),
        price=round(price, 2),
        signal=signal,
        confidence=round(confidence, 1),
        timeframe=timeframe + ("-demo" if demo else ""),
        reasons=reasons,
        indicators=indicators,
        stop_loss=stop,
        target=target,
    )


def quote(symbol: str) -> dict[str, Any]:
    # 1) Prefer live LTP from connected trading account (Zerodha / Dhan)
    try:
        from .broker import broker as _broker

        live = _broker.live_quote(symbol)
        if live and live.get("price"):
            return live
    except Exception:
        pass

    demo = False
    source = "yahoo_chart"
    price = prev = 0.0
    currency = "INR"

    try:
        df, meta = _fetch_yahoo_chart(symbol, period="5d", interval="1d")
        price = _live_price_from_meta(meta, df)
        prev = float(meta.get("previousClose") or meta.get("chartPreviousClose") or 0)
        if not prev and len(df) > 1:
            prev = float(df["Close"].iloc[-2])
        currency = str(meta.get("currency") or "INR")
    except Exception:
        try:
            t = yf.Ticker(_yf_symbol(symbol))
            info = t.fast_info
            price = float(getattr(info, "last_price", None) or getattr(info, "lastPrice", 0) or 0)
            prev = float(getattr(info, "previous_close", None) or getattr(info, "previousClose", 0) or 0)
            currency = getattr(info, "currency", "INR")
            source = "yfinance"
        except Exception:
            price = prev = 0.0

    if not price:
        df = fetch_ohlcv(symbol, period="1mo", interval="1d")
        demo = bool(getattr(df, "attrs", {}).get("demo"))
        source = str(getattr(df, "attrs", {}).get("source") or "unknown")
        price = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-2]) if len(df) > 1 else price

    change_pct = ((price - prev) / prev * 100) if prev else 0.0
    return {
        "symbol": symbol.upper(),
        "price": round(price, 2),
        "previous_close": round(prev, 2),
        "change_pct": round(change_pct, 2),
        "currency": currency,
        "demo_data": demo,
        "source": source,
        "broker": None,
    }


def signal_to_dict(s: SignalResult) -> dict[str, Any]:
    return asdict(s)
