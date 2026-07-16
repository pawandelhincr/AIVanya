"""News impact scanner (RSS) + simple sentiment for trading context."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx

FEEDS = [
    "https://news.google.com/rss/search?q={q}+stock+OR+shares+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=NSE+OR+Nifty+OR+Sensex+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
]

BULLISH = {
    "surge", "rally", "gain", "profit", "upgrade", "beat", "growth", "record",
    "bullish", "buy", "strong", "expansion", "order win", "deal", "approval",
}
BEARISH = {
    "fall", "drop", "loss", "downgrade", "miss", "fraud", "probe", "ban",
    "bearish", "sell", "weak", "layoff", "debt", "crash", "warning", "fine",
}


def _score_headline(text: str) -> tuple[float, str]:
    t = text.lower()
    b = sum(1 for w in BULLISH if w in t)
    s = sum(1 for w in BEARISH if w in t)
    if b > s:
        return min(1.0, 0.25 * b), "bullish"
    if s > b:
        return -min(1.0, 0.25 * s), "bearish"
    return 0.0, "neutral"


def fetch_news(symbol: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    query = symbol.upper() if symbol else "Indian stock market"
    urls = [FEEDS[0].format(q=quote_plus(query))]
    if not symbol:
        urls.append(FEEDS[1].format(q=""))

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for url in urls:
        try:
            # feedparser can fetch; httpx as fallback for reliability
            parsed = feedparser.parse(url)
            entries = parsed.entries
            if not entries:
                with httpx.Client(timeout=10) as client:
                    resp = client.get(url, follow_redirects=True)
                    parsed = feedparser.parse(resp.text)
                    entries = parsed.entries
        except Exception:
            continue

        for e in entries:
            title = getattr(e, "title", "") or ""
            if not title or title in seen:
                continue
            seen.add(title)
            score, label = _score_headline(title)
            items.append(
                {
                    "title": title,
                    "link": getattr(e, "link", ""),
                    "published": getattr(e, "published", "") or getattr(e, "updated", ""),
                    "sentiment": label,
                    "impact_score": round(score, 2),
                    "symbol": (symbol or "MARKET").upper(),
                }
            )
            if len(items) >= limit:
                return items
    return items


def news_impact_summary(symbol: str) -> dict[str, Any]:
    news = fetch_news(symbol, limit=10)
    if not news:
        return {
            "symbol": symbol.upper(),
            "bias": "neutral",
            "avg_impact": 0.0,
            "headline_count": 0,
            "note": "No recent headlines found — rely more on technicals.",
            "items": [],
        }
    avg = sum(n["impact_score"] for n in news) / len(news)
    if avg > 0.15:
        bias = "bullish"
        note = "Recent headlines lean positive — avoid shorting into news strength."
    elif avg < -0.15:
        bias = "bearish"
        note = "Recent headlines lean negative — be cautious on long entries."
    else:
        bias = "neutral"
        note = "Mixed / low news impact — technicals dominate."

    return {
        "symbol": symbol.upper(),
        "bias": bias,
        "avg_impact": round(avg, 3),
        "headline_count": len(news),
        "note": note,
        "items": news,
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }


def extract_symbol_from_text(text: str) -> str | None:
    """Heuristic: RELIANCE, TCS, NIFTY etc."""
    known = {
        "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC",
        "BHARTIARTL", "AXISBANK", "KOTAKBANK", "LT", "BAJFINANCE", "MARUTI",
        "TATAMOTORS", "SUNPHARMA", "WIPRO", "HCLTECH", "NIFTY", "BANKNIFTY",
        "ADANIENT", "TITAN", "ONGC", "NTPC",
    }
    upper = text.upper()
    for sym in known:
        if re.search(rf"\b{re.escape(sym)}\b", upper):
            return sym
    m = re.search(r"\b([A-Z]{2,12})\b", upper)
    if m and m.group(1) not in {"BUY", "SELL", "CALL", "PUT", "CE", "PE", "WHAT", "SHOULD", "WEEKLY", "STOCK", "OPTION", "SHARE", "THE", "FOR", "AND", "YES", "NOT"}:
        return m.group(1)
    return None
