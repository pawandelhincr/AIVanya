from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..deps import require_active_user
from ..services.chat import handle_chat
from ..services.broker import broker
from ..services.market import analyze_cash, quote, signal_to_dict, WATCHLIST
from ..services.news import news_impact_summary, fetch_news
from ..services.options import suggest_option_trade, explain_greeks_for_user
from ..services.weekly import weekly_delivery_picks

router = APIRouter(prefix="/api")


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class OrderIn(BaseModel):
    symbol: str
    side: str
    qty: int = Field(..., gt=0)
    segment: str = "EQ"
    product: str = "MIS"
    order_type: str = "MARKET"
    price: float | None = None
    option_type: str | None = None
    strike: float | None = None
    expiry: str | None = None
    security_id: str | None = None


class ModeIn(BaseModel):
    mode: str


class ActiveBrokerIn(BaseModel):
    broker: str  # zerodha | dhan | paper


class KiteLinkIn(BaseModel):
    user_id: str = ""
    access_token: str
    request_token: str | None = None


class KiteSessionIn(BaseModel):
    request_token: str


class DhanLinkIn(BaseModel):
    client_id: str
    access_token: str


@router.get("/health")
def health():
    return {"ok": True, "service": "ai-trading-bot"}


@router.post("/chat")
async def chat(body: ChatIn, user=Depends(require_active_user)):
    try:
        result = await handle_chat(body.message)
    except Exception as exc:
        return {
            "reply": f"Server error while handling chat: {exc}",
            "data": {"error": str(exc)},
            "suggestions": ["help", "RELIANCE buy?", "weekly stocks"],
            "user": {
                "name": user["name"],
                "status": user["status"],
                "days_left": user["days_left"],
            },
        }
    result["user"] = {
        "name": user["name"],
        "status": user["status"],
        "days_left": user["days_left"],
    }
    return result


@router.get("/quote/{symbol}")
def get_quote(symbol: str, user=Depends(require_active_user)):
    return quote(symbol)


@router.get("/signal/{symbol}")
def get_signal(symbol: str, timeframe: str = "intraday", user=Depends(require_active_user)):
    try:
        return signal_to_dict(analyze_cash(symbol, timeframe=timeframe))
    except Exception as exc:
        return {"symbol": symbol.upper(), "error": str(exc), "signal": "HOLD", "confidence": 0}


@router.get("/options/{symbol}")
def get_options(symbol: str, user=Depends(require_active_user)):
    try:
        return suggest_option_trade(symbol)
    except Exception as exc:
        return {"symbol": symbol.upper(), "error": str(exc), "action": "HOLD"}


@router.get("/greeks/{symbol}")
def get_greeks(
    symbol: str,
    option_type: str = "CE",
    strike: float | None = None,
    user=Depends(require_active_user),
):
    return explain_greeks_for_user(symbol, option_type=option_type, strike=strike)


@router.get("/news")
def get_news(symbol: str | None = None, user=Depends(require_active_user)):
    if symbol:
        return news_impact_summary(symbol)
    return {"items": fetch_news(limit=15)}


@router.get("/weekly")
def get_weekly(top_n: int = 5, user=Depends(require_active_user)):
    return weekly_delivery_picks(top_n=top_n)


@router.get("/watchlist")
def get_watchlist():
    return {"symbols": WATCHLIST}


@router.get("/account")
def get_account(user=Depends(require_active_user)):
    return broker.account_summary()


@router.get("/broker/status")
def broker_status(user=Depends(require_active_user)):
    return broker.status()


@router.get("/broker/live/{symbol}")
def broker_live_quote(symbol: str, user=Depends(require_active_user)):
    q = broker.live_quote(symbol)
    if not q:
        return {
            "ok": False,
            "symbol": symbol.upper(),
            "message": "Broker connected nahi hai ya quote fail. Pehle Zerodha/Dhan connect karo.",
            "fallback": quote(symbol),
        }
    return {"ok": True, **q}


@router.get("/broker/live")
def broker_live_quotes(symbols: str = "RELIANCE,TCS,SBIN", user=Depends(require_active_user)):
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    return broker.live_quotes(syms)


@router.post("/account/mode")
def set_mode(body: ModeIn, user=Depends(require_active_user)):
    return broker.set_mode(body.mode)


@router.post("/broker/active")
def set_active_broker(body: ActiveBrokerIn, user=Depends(require_active_user)):
    return broker.set_active_broker(body.broker)


# ── Zerodha ─────────────────────────────────────────────────
@router.get("/broker/zerodha/login")
@router.get("/broker/kite/login")
def kite_login(user=Depends(require_active_user)):
    return broker.kite_login_url()


@router.get("/broker/zerodha/callback")
def zerodha_callback(request_token: str = Query(...), status: str = "success"):
    """Kite redirect land here — exchange token and show simple HTML result."""
    if status != "success" or not request_token:
        return HTMLResponse("<h2>Zerodha login failed / cancelled</h2>", status_code=400)
    try:
        result = broker.zerodha_exchange_token(request_token)
        return HTMLResponse(
            f"""
            <html><body style="font-family:sans-serif;padding:2rem">
            <h2>Zerodha connected</h2>
            <p>User: <b>{result.get('user_id')}</b> ({result.get('user_name') or ''})</p>
            <p>Active broker set to <b>zerodha</b> · mode <b>live</b></p>
            <p><a href="/">Back to TradeMind</a></p>
            <p style="color:#666">Tip: restart ke baad .env mein KITE_ACCESS_TOKEN paste karo for persistence.</p>
            </body></html>
            """
        )
    except Exception as exc:
        return HTMLResponse(f"<h2>Token exchange failed</h2><pre>{exc}</pre>", status_code=400)


@router.post("/broker/zerodha/session")
def zerodha_session(body: KiteSessionIn, user=Depends(require_active_user)):
    return broker.zerodha_exchange_token(body.request_token)


@router.post("/broker/zerodha/link")
@router.post("/broker/kite/link")
def kite_link(body: KiteLinkIn, user=Depends(require_active_user)):
    if body.request_token:
        return broker.zerodha_exchange_token(body.request_token)
    if not body.access_token:
        raise ValueError("access_token or request_token required")
    return broker.link_kite_session(body.user_id or "UNKNOWN", body.access_token)


@router.get("/broker/zerodha/funds")
def zerodha_funds(user=Depends(require_active_user)):
    return broker.zerodha_funds()


@router.get("/broker/zerodha/profile")
def zerodha_profile(user=Depends(require_active_user)):
    return broker.zerodha_profile()


# ── Dhan ────────────────────────────────────────────────────
@router.get("/broker/dhan/info")
def dhan_info(user=Depends(require_active_user)):
    return broker.dhan_connect_info()


@router.post("/broker/dhan/link")
def dhan_link(body: DhanLinkIn, user=Depends(require_active_user)):
    return broker.link_dhan_session(body.client_id, body.access_token)


@router.get("/broker/dhan/funds")
def dhan_funds(user=Depends(require_active_user)):
    return broker.dhan_funds()


@router.get("/broker/dhan/positions")
def dhan_positions(user=Depends(require_active_user)):
    return broker.dhan_positions()


@router.post("/orders")
def place_order(body: OrderIn, user=Depends(require_active_user)):
    return broker.place_order(**body.model_dump())
