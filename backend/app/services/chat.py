"""Intent router + reply composer for the trading chat bot."""
from __future__ import annotations

import re
from typing import Any

import httpx

from ..config import settings
from .broker import broker
from .market import analyze_cash, quote, signal_to_dict
from .news import extract_symbol_from_text, news_impact_summary
from .options import explain_greeks_for_user, suggest_option_trade
from .weekly import weekly_delivery_picks


DISCLAIMER = (
    "⚠️ Yeh educational signal hai, investment advice nahi. "
    "Market risk hai — apna research / SEBI-registered advisor use karein."
)


def _wants(text: str, *keys: str) -> bool:
    """Word-aware match so short keys like 'ce' don't hit 'chahiye'."""
    t = text.lower()
    for k in keys:
        k = k.lower()
        if len(k) <= 3:
            if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", t):
                return True
        elif k in t:
            return True
    return False


async def _optional_llm_polish(user_text: str, facts: dict[str, Any]) -> str | None:
    if not settings.openai_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{settings.openai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": settings.openai_model,
                    "temperature": 0.3,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are an Indian markets trading assistant for cash & options. "
                                "Use ONLY the provided facts. Reply in Hinglish, concise, with clear BUY/SELL/HOLD. "
                                "Always mention risk. Never promise returns."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"User: {user_text}\nFacts JSON: {facts}",
                        },
                    ],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


def _format_cash(sig: dict[str, Any], news: dict[str, Any] | None = None) -> str:
    lines = [
        f"**{sig['symbol']}** @ ₹{sig['price']} → **{sig['signal']}** "
        f"(confidence {sig['confidence']}%, TF: {sig['timeframe']})",
        "",
        "**Technicals:**",
    ]
    for r in sig["reasons"][:6]:
        lines.append(f"• {r}")
    ind = sig["indicators"]
    lines.append(
        f"• RSI {ind['rsi']} | MACD {ind['macd']} | ADX {ind['adx']} | ATR {ind['atr']}"
    )
    if sig.get("stop_loss") and sig.get("target"):
        lines.append(f"• SL ≈ ₹{sig['stop_loss']} | Target ≈ ₹{sig['target']}")
    if news:
        lines.append("")
        lines.append(f"**News bias:** {news['bias']} — {news['note']}")
        for n in news.get("items", [])[:3]:
            lines.append(f"  – [{n['sentiment']}] {n['title'][:110]}")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _format_option(opt: dict[str, Any]) -> str:
    o = opt["option"]
    g = opt["greeks"]
    lines = [
        f"**Options idea — {opt['symbol']}** spot ₹{opt['spot']}",
        f"Cash bias: **{opt['cash_signal']}** ({opt['cash_confidence']}%)",
        f"Suggested action: **{opt['action']}** → {o['type']} {o['strike']} exp {o['expiry']}",
        f"Est. IV: {o['estimated_iv']}%",
        "",
        "**Greeks:**",
        f"• Delta {g['delta']} | Gamma {g['gamma']} | Theta {g['theta']} | Vega {g['vega']} | Rho {g['rho']}",
        "",
        "**Why:**",
    ]
    for r in opt["rationale"][:6]:
        lines.append(f"• {r}")
    if opt.get("write_hint"):
        lines.append("")
        lines.append(f"**Writing note:** {opt['write_hint']['note']}")
    lines.append("")
    lines.append(opt["disclaimer"])
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _format_weekly(data: dict[str, Any]) -> str:
    lines = [
        f"**Weekly delivery picks** (target ~{data['target_return_pct']}% — aspirational)",
        data["warning"],
        "",
    ]
    for i, p in enumerate(data["picks"], 1):
        lines.append(
            f"{i}. **{p['symbol']}** ₹{p['price']} | score {p['score']} | "
            f"exp move ~{p['expected_weekly_move_pct']}% | "
            f"hist 8% hit {p['hist_hit_rate_8pct']}% | "
            f"SL ₹{p['stop_loss']} → Tgt ₹{p['target_8pct']}"
        )
        if p["reasons"]:
            lines.append(f"   – {p['reasons'][0]}")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _parse_order(text: str) -> dict[str, Any] | None:
    """
    Examples:
      buy 10 RELIANCE
      sell 50 INFY delivery
      buy 1 NIFTY CE 24500
      write / sell pe ...
    """
    t = text.upper()
    m = re.search(
        r"\b(BUY|SELL|WRITE)\s+(\d+)\s+([A-Z0-9]+)"
        r"(?:\s+(CE|PE|CALL|PUT))?(?:\s+(\d+(?:\.\d+)?))?"
        r"(?:\s+(MIS|CNC|NRML|DELIVERY))?",
        t,
    )
    if not m:
        return None
    side, qty, symbol, opt, strike, product = m.groups()
    if side == "WRITE":
        side = "SELL"
    if opt in ("CALL",):
        opt = "CE"
    if opt in ("PUT",):
        opt = "PE"
    if product == "DELIVERY":
        product = "CNC"
    segment = "OPT" if opt else "EQ"
    product = product or ("NRML" if opt else "MIS")
    return {
        "side": side,
        "qty": int(qty),
        "symbol": symbol,
        "option_type": opt,
        "strike": float(strike) if strike else None,
        "segment": segment,
        "product": product,
    }


async def handle_chat(message: str) -> dict[str, Any]:
    text = (message or "").strip()
    if not text:
        return {"reply": "Kuch poochiye — jaise: `RELIANCE buy karna chahiye?` ya `weekly stocks`.", "data": {}}

    symbol = extract_symbol_from_text(text) or "NIFTY"
    data: dict[str, Any] = {}
    suggestions: list[str] = []

    # Account / broker
    if _wants(text, "account", "balance", "position", "portfolio", "broker", "kite", "zerodha", "dhan", "map", "connect"):
        st = broker.status()
        data["broker_status"] = st

        # Switch active broker
        if _wants(text, "use zerodha", "switch zerodha", "active zerodha"):
            data["status"] = broker.set_active_broker("zerodha")
            return {
                "reply": "**Active broker → Zerodha.** Mode live karne ke liye pehle connect + token chahiye.\nChat: `mode live`",
                "data": data,
                "suggestions": ["connect zerodha", "mode live", "account"],
            }
        if _wants(text, "use dhan", "switch dhan", "active dhan"):
            data["status"] = broker.set_active_broker("dhan")
            return {
                "reply": "**Active broker → Dhan.** Token link ke baad `mode live` bolo.",
                "data": data,
                "suggestions": ["connect dhan", "mode live", "account"],
            }
        if _wants(text, "mode paper", "paper mode"):
            data["account"] = broker.set_mode("paper")
            return {"reply": "**Mode → paper** (safe simulation).", "data": data, "suggestions": ["account"]}
        if _wants(text, "mode live", "live mode"):
            try:
                data["account"] = broker.set_mode("live")
                return {
                    "reply": f"**Mode → live** via `{data['account']['active_broker']}`. Real orders jayenge — careful!",
                    "data": data,
                    "suggestions": ["account", "buy 1 SBIN"],
                }
            except Exception as exc:
                return {"reply": f"Live mode fail: {exc}", "data": data, "suggestions": ["connect zerodha", "connect dhan"]}

        # Zerodha connect help
        if _wants(text, "zerodha", "kite"):
            data["kite"] = broker.kite_login_url()
            z = st["brokers"]["zerodha"]
            lines = [
                "**Zerodha (Kite Connect)**",
                f"Keys configured: {z['configured_keys']} | Connected: {z['connected']}",
                f"User: {z.get('user_id') or '—'}",
                "",
                "Steps:",
                "1. developers.kite.trade pe app → API key/secret `.env` mein",
                "2. Redirect URL: `http://127.0.0.1:8001/api/broker/zerodha/callback`",
                "3. Neeche login URL kholo → login → auto token exchange",
                "4. Chat: `use zerodha` phir `mode live`",
            ]
            if data["kite"].get("login_url"):
                lines.append("")
                lines.append(f"Login URL:\n{data['kite']['login_url']}")
            else:
                lines.append("")
                lines.append(data["kite"].get("message") or "Pehle KITE_API_KEY / KITE_API_SECRET set karo.")
            return {
                "reply": "\n".join(lines),
                "data": data,
                "suggestions": ["use zerodha", "mode live", "connect dhan", "account"],
            }

        # Dhan connect help
        if _wants(text, "dhan"):
            data["dhan"] = broker.dhan_connect_info()
            d = st["brokers"]["dhan"]
            lines = [
                "**DhanHQ**",
                f"Configured: {d['configured_keys']} | Connected: {d['connected']}",
                f"Client: {d.get('client_id') or '—'}",
                "",
                "Steps:",
                "1. web.dhan.co → Profile → Access DhanHQ APIs → token",
                "2. `.env`: `DHAN_CLIENT_ID` + `DHAN_ACCESS_TOKEN`",
                "3. Ya API: POST /api/broker/dhan/link",
                "4. Chat: `use dhan` → `mode live`",
                "",
                "⚠️ Dhan order APIs ke liye static IP whitelist lag sakta hai.",
            ]
            return {
                "reply": "\n".join(lines),
                "data": data,
                "suggestions": ["use dhan", "mode live", "connect zerodha", "account"],
            }

        if _wants(text, "login", "connect", "link", "map"):
            z = st["brokers"]["zerodha"]
            d = st["brokers"]["dhan"]
            reply = (
                "**Broker mapping**\n"
                f"Mode: `{st['mode']}` | Active: `{st['active_broker']}` | Paper cash: ₹{st['paper_cash']}\n\n"
                f"• Zerodha: {'connected' if z['connected'] else 'not connected'} "
                f"(keys={'yes' if z['configured_keys'] else 'no'})\n"
                f"• Dhan: {'connected' if d['connected'] else 'not connected'} "
                f"(keys={'yes' if d['configured_keys'] else 'no'})\n\n"
                "Bol do: `connect zerodha` ya `connect dhan`"
            )
            return {
                "reply": reply,
                "data": data,
                "suggestions": ["connect zerodha", "connect dhan", "account", "mode paper"],
            }

        data["account"] = broker.account_summary()
        acc = data["account"]
        pos_lines = "\n".join(
            f"• {p['symbol']} qty {p['qty']} @ {p['avg_price']}" for p in acc["positions"]
        ) or "• No open positions"
        reply = (
            f"**Account ({acc['mode']} / {acc['active_broker']})**\n"
            f"Cash (paper ledger): ₹{acc['cash']}\n"
            f"Invested ≈ ₹{acc['invested_approx']}\n"
            f"Zerodha user: {acc['kite_user_id'] or '—'}\n"
            f"Dhan client: {acc['dhan_client_id'] or '—'}\n"
            f"Live ready: {acc['live_ready']}\n\n"
            f"**Positions**\n{pos_lines}"
        )
        return {
            "reply": reply,
            "data": data,
            "suggestions": ["connect zerodha", "connect dhan", "weekly stocks", "buy 10 SBIN"],
        }

    # Place order
    order_req = _parse_order(text)
    if order_req and _wants(text, "buy", "sell", "write", "order", "purchase"):
        # Distinguish analysis vs order: if "chahiye" / "should" → analysis only
        if _wants(text, "chahiye", "should", "suggest", "kya", "advice", "recommend"):
            order_req = None
        else:
            try:
                result = broker.place_order(**order_req)
                data["order"] = result
                reply = (
                    f"**Order {result['status']}** (`{result['mode']}`)\n"
                    f"{result['side']} {result['qty']} {result['symbol']} "
                    f"{result.get('option_type') or ''} "
                    f"{result.get('strike') or ''} @ ≈ ₹{result['price']}\n"
                    f"ID: `{result['order_id']}`\n{result['message']}"
                )
                return {"reply": reply, "data": data, "suggestions": ["account", "positions", "weekly stocks"]}
            except Exception as exc:
                return {"reply": f"Order failed: {exc}", "data": {}, "suggestions": ["account"]}

    # Weekly delivery
    if _wants(text, "weekly", "delivery", "8%", "8 percent", "hafta", "swing pick"):
        data["weekly"] = weekly_delivery_picks(top_n=5)
        reply = _format_weekly(data["weekly"])
        polished = await _optional_llm_polish(text, data)
        return {
            "reply": polished or reply,
            "data": data,
            "suggestions": ["buy 5 " + data["weekly"]["picks"][0]["symbol"] if data["weekly"]["picks"] else "account"],
        }

    # Options / Greeks
    if _wants(
        text,
        "option", "options", "call", "put", "ce", "pe", "delta", "theta",
        "gamma", "vega", "greeks", "writing", "writer",
    ):
        if _wants(text, "delta", "theta", "gamma", "vega", "greeks") and not _wants(
            text, "buy", "sell", "call", "put", "ce", "pe", "suggest"
        ):
            data["greeks"] = explain_greeks_for_user(symbol)
            g = data["greeks"]
            reply = (
                f"**Greeks — {g['symbol']} {g['type']} {g['strike']}** (exp {g['expiry']})\n"
                f"Spot ₹{g['spot']} | IV ≈ {g['iv_pct']}%\n"
                f"Delta {g['greeks']['delta']} | Gamma {g['greeks']['gamma']} | "
                f"Theta {g['greeks']['theta']} | Vega {g['greeks']['vega']}\n\n"
                f"• {g['plain_english']['delta']}\n"
                f"• {g['plain_english']['theta']}\n"
                f"• {g['plain_english']['vega']}\n\n"
                f"{DISCLAIMER}"
            )
            return {"reply": reply, "data": data, "suggestions": [f"{symbol} options", "weekly stocks"]}

        data["option"] = suggest_option_trade(symbol)
        data["news"] = news_impact_summary(symbol)
        reply = _format_option(data["option"])
        polished = await _optional_llm_polish(text, data)
        suggestions = [
            f"buy 1 {symbol} {data['option']['option']['type']} {int(data['option']['option']['strike'])}",
            f"{symbol} news",
            "account",
        ]
        return {"reply": polished or reply, "data": data, "suggestions": suggestions}

    # News
    if _wants(text, "news", "headline", "impact", "samachar"):
        data["news"] = news_impact_summary(symbol)
        n = data["news"]
        lines = [f"**News impact — {n['symbol']}** bias **{n['bias']}**", n["note"], ""]
        for item in n["items"][:6]:
            lines.append(f"• [{item['sentiment']}] {item['title']}")
        lines.append("")
        lines.append(DISCLAIMER)
        return {"reply": "\n".join(lines), "data": data, "suggestions": [f"{symbol} buy?", f"{symbol} options"]}

    # Default: cash buy/sell advice
    if _wants(text, "buy", "sell", "hold", "entry", "intraday", "chart", "technical", "chahiye", "kya", "signal"):
        sig = signal_to_dict(analyze_cash(symbol, timeframe="intraday"))
        news = news_impact_summary(symbol)
        # Blend news lightly into message
        if news["bias"] == "bearish" and sig["signal"] == "BUY":
            sig["reasons"].append("News caution: headlines lean bearish — reduce size / wait confirmation")
        if news["bias"] == "bullish" and sig["signal"] == "SELL":
            sig["reasons"].append("News caution: headlines lean bullish — short carefully")
        data["signal"] = sig
        data["news"] = news
        data["quote"] = quote(symbol)
        reply = _format_cash(sig, news)
        polished = await _optional_llm_polish(text, data)
        return {
            "reply": polished or reply,
            "data": data,
            "suggestions": [f"{symbol} options", f"{symbol} news", "weekly stocks"],
        }

    # Help / greeting
    if _wants(text, "help", "hello", "hi", "namaste", "start"):
        reply = (
            "**AI Trading Bot** — Cash + Options (India)\n\n"
            "Examples:\n"
            "• `RELIANCE buy karna chahiye?`\n"
            "• `NIFTY options` / `INFY delta theta`\n"
            "• `weekly stocks` (~8% delivery ideas)\n"
            "• `TCS news`\n"
            "• `buy 10 SBIN` (paper/live order)\n"
            "• `account` / `connect zerodha` / `connect dhan`\n\n"
            f"{DISCLAIMER}"
        )
        return {
            "reply": reply,
            "data": {},
            "suggestions": ["RELIANCE buy?", "NIFTY options", "weekly stocks", "account"],
        }

    # Fallback: treat as symbol analysis
    try:
        sig = signal_to_dict(analyze_cash(symbol, timeframe="intraday"))
        news = news_impact_summary(symbol)
        data = {"signal": sig, "news": news}
        return {
            "reply": _format_cash(sig, news),
            "data": data,
            "suggestions": [f"{symbol} options", "weekly stocks", "help"],
        }
    except Exception as exc:
        return {
            "reply": (
                f"Samajh nahi paya / data nahi mila ({exc}).\n"
                "Try: `RELIANCE buy?`, `weekly stocks`, `NIFTY options`, `help`"
            ),
            "data": {},
            "suggestions": ["help", "weekly stocks", "NIFTY options"],
        }
