"""Options helpers: Greeks, call/put suggestion for NSE-style underlyings."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from scipy.stats import norm

from .market import analyze_cash, fetch_ohlcv, quote


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


def _bs_greeks(
    spot: float,
    strike: float,
    t_years: float,
    rate: float,
    iv: float,
    option_type: str,
) -> Greeks:
    if t_years <= 0 or iv <= 0 or spot <= 0:
        return Greeks(0, 0, 0, 0, 0)

    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t

    if option_type == "CE":
        delta = float(norm.cdf(d1))
        theta = float(
            -(spot * norm.pdf(d1) * iv) / (2 * sqrt_t)
            - rate * strike * math.exp(-rate * t_years) * norm.cdf(d2)
        ) / 365
        rho = float(strike * t_years * math.exp(-rate * t_years) * norm.cdf(d2)) / 100
    else:
        delta = float(norm.cdf(d1) - 1)
        theta = float(
            -(spot * norm.pdf(d1) * iv) / (2 * sqrt_t)
            + rate * strike * math.exp(-rate * t_years) * norm.cdf(-d2)
        ) / 365
        rho = float(-strike * t_years * math.exp(-rate * t_years) * norm.cdf(-d2)) / 100

    gamma = float(norm.pdf(d1) / (spot * iv * sqrt_t))
    vega = float(spot * norm.pdf(d1) * sqrt_t) / 100
    return Greeks(
        delta=round(delta, 4),
        gamma=round(gamma, 6),
        theta=round(theta, 4),
        vega=round(vega, 4),
        rho=round(rho, 4),
    )


def estimate_iv(symbol: str) -> float:
    """Historical vol proxy when live IV chain is unavailable."""
    df = fetch_ohlcv(symbol, period="1mo", interval="1d")
    rets = np.log(df["Close"] / df["Close"].shift(1)).dropna()
    hv = float(rets.std() * math.sqrt(252))
    return max(0.12, min(0.80, hv))


def nearest_expiry(days_ahead: int = 7) -> datetime:
    """Next Thursday-style weekly expiry approximation (NSE)."""
    today = datetime.now().date()
    # NSE equity/index weekly often Thursday
    days_until_thu = (3 - today.weekday()) % 7
    if days_until_thu == 0 and datetime.now().hour >= 15:
        days_until_thu = 7
    exp = today + timedelta(days=days_until_thu if days_until_thu else 7)
    if (exp - today).days < days_ahead // 2:
        exp = exp + timedelta(days=7)
    return datetime.combine(exp, datetime.min.time())


def round_strike(spot: float, step: float = 50) -> float:
    return round(spot / step) * step


def suggest_option_trade(symbol: str, bias: str | None = None) -> dict[str, Any]:
    """
    Suggest CE buy / PE buy / writing based on cash signal + Greeks.
    bias: BUY | SELL | None (auto from technicals)
    """
    cash = analyze_cash(symbol, timeframe="intraday")
    q = quote(symbol)
    spot = q["price"] or cash.price
    signal = bias or cash.signal

    iv = estimate_iv(symbol)
    expiry = nearest_expiry()
    t_years = max((expiry - datetime.now()).total_seconds() / (365 * 24 * 3600), 1 / 365)
    rate = 0.065

    # Strike selection by intent
    step = 100 if spot > 5000 else (50 if spot > 500 else 10)
    atm = round_strike(spot, step)

    if signal == "BUY":
        action = "BUY_CE"
        option_type = "CE"
        strike = atm  # ATM / slightly ITM for directional
        rationale = [
            f"Cash technical bias: {cash.signal} ({cash.confidence}% confidence)",
            "Buy Call (CE) for bullish directional exposure with defined premium risk",
            *cash.reasons[:3],
        ]
    elif signal == "SELL":
        action = "BUY_PE"
        option_type = "PE"
        strike = atm
        rationale = [
            f"Cash technical bias: {cash.signal} ({cash.confidence}% confidence)",
            "Buy Put (PE) for bearish directional exposure",
            *cash.reasons[:3],
        ]
    else:
        # Sideways → suggest writing OTM for theta
        action = "WRITE_CE_PE_IRON" if cash.indicators.get("adx", 0) < 20 else "HOLD"
        option_type = "CE"
        strike = atm + step
        rationale = [
            "Market looks sideways / low conviction — prefer premium selling only with hedges",
            "Theta works for writers in range-bound markets; avoid naked writing",
            f"ADX={cash.indicators.get('adx')} suggests limited trend strength",
        ]

    greeks = _bs_greeks(spot, strike, t_years, rate, iv, option_type if action != "BUY_PE" else "PE")
    if action == "BUY_PE":
        greeks = _bs_greeks(spot, strike, t_years, rate, iv, "PE")

    # Writing suggestion when theta is attractive & range-bound
    write_hint = None
    if cash.indicators.get("adx", 25) < 22 and abs(greeks.delta) < 0.35:
        write_hint = {
            "style": "OTM credit (prefer spreads, not naked)",
            "note": "Positive theta decay helps writers; use defined-risk spreads.",
            "suggested": "SELL OTM CE + PE (short strangle) only if hedged / experienced",
        }

    return {
        "symbol": symbol.upper(),
        "spot": spot,
        "cash_signal": cash.signal,
        "cash_confidence": cash.confidence,
        "action": action,
        "option": {
            "type": "PE" if action == "BUY_PE" else "CE",
            "strike": strike,
            "expiry": expiry.strftime("%Y-%m-%d"),
            "estimated_iv": round(iv * 100, 2),
            "lot_note": "Use exchange lot size for the underlying (check NSE F&O).",
        },
        "greeks": asdict(greeks),
        "greeks_guide": {
            "delta": "Direction sensitivity. ~0.4–0.6 for directional buys.",
            "gamma": "How fast delta changes. High near expiry / ATM.",
            "theta": "Daily time decay. Negative for buyers, positive for writers.",
            "vega": "IV sensitivity. Avoid buying before IV crush (post-event).",
            "rho": "Interest-rate sensitivity (usually secondary for weekly).",
        },
        "rationale": rationale,
        "risk": {
            "max_loss_buyer": "Limited to premium paid",
            "max_loss_writer": "High / unlimited for naked CE — use spreads",
            "stop_idea": cash.stop_loss,
            "target_idea": cash.target,
        },
        "write_hint": write_hint,
        "disclaimer": "Educational signal only — not investment advice. Options can expire worthless.",
    }


def explain_greeks_for_user(symbol: str, option_type: str = "CE", strike: float | None = None) -> dict[str, Any]:
    q = quote(symbol)
    spot = q["price"]
    strike = strike or round_strike(spot)
    iv = estimate_iv(symbol)
    expiry = nearest_expiry()
    t_years = max((expiry - datetime.now()).total_seconds() / (365 * 24 * 3600), 1 / 365)
    g = _bs_greeks(spot, strike, t_years, 0.065, iv, option_type.upper())
    return {
        "symbol": symbol.upper(),
        "spot": spot,
        "strike": strike,
        "type": option_type.upper(),
        "expiry": expiry.strftime("%Y-%m-%d"),
        "iv_pct": round(iv * 100, 2),
        "greeks": asdict(g),
        "plain_english": {
            "delta": f"Approx ₹{abs(g.delta):.2f} move in premium for ₹1 move in {symbol}",
            "theta": f"Approx ₹{abs(g.theta):.2f}/day time decay (hurts buyers)",
            "vega": f"Approx ₹{abs(g.vega):.2f} premium change for 1% IV change",
            "gamma": "Delta will change faster near ATM as expiry nears",
        },
    }
