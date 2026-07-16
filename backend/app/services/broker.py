"""
Multi-broker layer: Paper + Zerodha (Kite) + DhanHQ.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from ..config import settings
from .market import quote

DB_PATH = settings.data_dir / "trading.db"

# Common NSE equity → Dhan security_id (expand as needed)
DHAN_SECURITY_IDS: dict[str, str] = {
    "RELIANCE": "2885",
    "TCS": "11536",
    "INFY": "1594",
    "HDFCBANK": "1333",
    "ICICIBANK": "4963",
    "SBIN": "3045",
    "ITC": "1660",
    "BHARTIARTL": "10604",
    "AXISBANK": "5900",
    "KOTAKBANK": "1922",
    "LT": "11483",
    "BAJFINANCE": "317",
    "MARUTI": "10999",
    "TATAMOTORS": "3456",
    "SUNPHARMA": "3351",
    "WIPRO": "3787",
    "HCLTECH": "7229",
    "ASIANPAINT": "236",
    "TITAN": "3506",
    "ULTRACEMCO": "11543",
    "NTPC": "11630",
    "POWERGRID": "14977",
    "ONGC": "2475",
    "COALINDIA": "5215",
    "ADANIENT": "25",
}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash REAL NOT NULL,
                mode TEXT NOT NULL,
                active_broker TEXT,
                kite_user_id TEXT,
                dhan_client_id TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                segment TEXT NOT NULL,
                side TEXT NOT NULL,
                product TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price REAL,
                order_type TEXT NOT NULL,
                status TEXT NOT NULL,
                option_type TEXT,
                strike REAL,
                expiry TEXT,
                mode TEXT NOT NULL,
                broker TEXT,
                created_at TEXT NOT NULL,
                raw_json TEXT
            );
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                segment TEXT NOT NULL,
                qty INTEGER NOT NULL,
                avg_price REAL NOT NULL,
                option_type TEXT,
                strike REAL,
                expiry TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS broker_tokens (
                broker TEXT PRIMARY KEY,
                user_id TEXT,
                access_token TEXT,
                meta_json TEXT,
                updated_at TEXT
            );
            """
        )
        # Soft migrations for older DBs
        cols = {r[1] for r in conn.execute("PRAGMA table_info(account)").fetchall()}
        if "active_broker" not in cols:
            conn.execute("ALTER TABLE account ADD COLUMN active_broker TEXT")
        if "dhan_client_id" not in cols:
            conn.execute("ALTER TABLE account ADD COLUMN dhan_client_id TEXT")
        ocols = {r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
        if "broker" not in ocols:
            conn.execute("ALTER TABLE orders ADD COLUMN broker TEXT")

        row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO account (id, cash, mode, active_broker, updated_at) VALUES (1, ?, ?, ?, ?)",
                (
                    settings.paper_starting_cash,
                    settings.trading_mode,
                    settings.active_broker,
                    datetime.now().isoformat(),
                ),
            )


def _save_token(broker: str, user_id: str, access_token: str, meta: dict | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO broker_tokens (broker, user_id, access_token, meta_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(broker) DO UPDATE SET
              user_id=excluded.user_id,
              access_token=excluded.access_token,
              meta_json=excluded.meta_json,
              updated_at=excluded.updated_at
            """,
            (
                broker,
                user_id,
                access_token,
                json.dumps(meta or {}),
                datetime.now().isoformat(),
            ),
        )


def _load_token(broker: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM broker_tokens WHERE broker = ?", (broker,)
        ).fetchone()
    return dict(row) if row else None


class BrokerService:
    def __init__(self) -> None:
        init_db()

    # ── status ──────────────────────────────────────────────
    def _broker_connected(self, broker: str) -> bool:
        b = broker.lower()
        if b == "zerodha":
            kite_tok = _load_token("zerodha")
            return bool(
                settings.kite_api_key
                and (settings.kite_access_token or (kite_tok and kite_tok.get("access_token")))
            )
        if b == "dhan":
            dhan_tok = _load_token("dhan")
            return bool(
                (settings.dhan_client_id or (dhan_tok and dhan_tok.get("user_id")))
                and (settings.dhan_access_token or (dhan_tok and dhan_tok.get("access_token")))
            )
        return False

    def _live_ready(self, broker: str | None = None) -> bool:
        return self._broker_connected((broker or settings.active_broker).lower())

    def status(self) -> dict[str, Any]:
        kite_tok = _load_token("zerodha")
        dhan_tok = _load_token("dhan")
        with _conn() as conn:
            acc = conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
        mode = acc["mode"] if acc else settings.trading_mode
        active = (acc["active_broker"] if acc and acc["active_broker"] else None) or settings.active_broker
        cash = round(float(acc["cash"]), 2) if acc else settings.paper_starting_cash
        kite_ready = self._broker_connected("zerodha")
        dhan_ready = self._broker_connected("dhan")
        return {
            "mode": mode,
            "active_broker": active,
            "brokers": {
                "zerodha": {
                    "name": "Zerodha Kite",
                    "configured_keys": bool(settings.kite_api_key and settings.kite_api_secret),
                    "connected": kite_ready,
                    "user_id": settings.kite_user_id
                    or (kite_tok or {}).get("user_id")
                    or (acc["kite_user_id"] if acc else None),
                    "docs": "https://kite.trade/docs/connect/v3/",
                    "console": "https://developers.kite.trade/",
                },
                "dhan": {
                    "name": "DhanHQ",
                    "configured_keys": bool(settings.dhan_client_id or settings.dhan_app_id),
                    "connected": dhan_ready,
                    "client_id": settings.dhan_client_id
                    or (dhan_tok or {}).get("user_id")
                    or (acc["dhan_client_id"] if acc else None),
                    "docs": "https://dhanhq.co/docs/v2/",
                    "console": "https://web.dhan.co/",
                    "note": "Order APIs often need static IP whitelisting on Dhan.",
                },
            },
            "paper_cash": cash,
        }

    def account_summary(self) -> dict[str, Any]:
        with _conn() as conn:
            acc = conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
            positions = [dict(r) for r in conn.execute("SELECT * FROM positions").fetchall()]
            orders = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM orders ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
            ]
        invested = sum(p["qty"] * p["avg_price"] for p in positions)
        mode = acc["mode"] if acc else settings.trading_mode
        active = (acc["active_broker"] if acc and acc["active_broker"] else None) or settings.active_broker
        return {
            "mode": mode,
            "active_broker": active,
            "cash": round(float(acc["cash"]), 2) if acc else settings.paper_starting_cash,
            "kite_user_id": acc["kite_user_id"] if acc else None,
            "dhan_client_id": acc["dhan_client_id"] if acc else None,
            "invested_approx": round(invested, 2),
            "positions": positions,
            "recent_orders": orders,
            "live_ready": self._live_ready(active),
        }

    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in ("paper", "live"):
            raise ValueError("mode must be paper or live")
        if mode == "live":
            active = self.account_summary()["active_broker"]
            if not self._live_ready(active):
                raise ValueError(
                    f"Connect {active} first (credentials + access token), then switch to live"
                )
        with _conn() as conn:
            conn.execute(
                "UPDATE account SET mode = ?, updated_at = ? WHERE id = 1",
                (mode, datetime.now().isoformat()),
            )
        return self.account_summary()

    def set_active_broker(self, broker: str) -> dict[str, Any]:
        broker = broker.lower().strip()
        if broker not in ("zerodha", "dhan", "paper"):
            raise ValueError("broker must be zerodha, dhan, or paper")
        if broker == "paper":
            return self.set_mode("paper")
        with _conn() as conn:
            conn.execute(
                "UPDATE account SET active_broker = ?, updated_at = ? WHERE id = 1",
                (broker, datetime.now().isoformat()),
            )
        settings.active_broker = broker
        return self.status()

    # ── Zerodha ─────────────────────────────────────────────
    def kite_login_url(self) -> dict[str, Any]:
        if not settings.kite_api_key:
            return {
                "broker": "zerodha",
                "configured": False,
                "steps": [
                    "https://developers.kite.trade/ pe app banao",
                    ".env mein KITE_API_KEY aur KITE_API_SECRET set karo",
                    "Redirect URL set karo (e.g. http://127.0.0.1:8001/api/broker/zerodha/callback)",
                    "Phir /api/broker/zerodha/login open karo",
                ],
                "docs": "https://kite.trade/docs/connect/v3/",
            }
        url = f"https://kite.zerodha.com/connect/login?v=3&api_key={settings.kite_api_key}"
        return {
            "broker": "zerodha",
            "configured": True,
            "login_url": url,
            "next": "Login ke baad redirect URL se request_token lo, POST /api/broker/zerodha/session",
        }

    def zerodha_exchange_token(self, request_token: str) -> dict[str, Any]:
        if not settings.kite_api_key or not settings.kite_api_secret:
            raise ValueError("KITE_API_KEY / KITE_API_SECRET missing in .env")
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise RuntimeError("kiteconnect not installed") from exc

        kite = KiteConnect(api_key=settings.kite_api_key)
        data = kite.generate_session(request_token, api_secret=settings.kite_api_secret)
        access_token = data["access_token"]
        user_id = data.get("user_id", "")
        settings.kite_access_token = access_token
        settings.kite_user_id = user_id
        _save_token("zerodha", user_id, access_token, {"login_time": data.get("login_time")})
        with _conn() as conn:
            conn.execute(
                """
                UPDATE account SET kite_user_id = ?, active_broker = ?, mode = ?, updated_at = ?
                WHERE id = 1
                """,
                (user_id, "zerodha", "live", datetime.now().isoformat()),
            )
        return {
            "linked": True,
            "broker": "zerodha",
            "user_id": user_id,
            "user_name": data.get("user_name"),
            "access_token_set": True,
            "note": "Token process memory + DB mein save. Restart ke baad .env KITE_ACCESS_TOKEN bhi set karo.",
            "status": self.status(),
        }

    def link_kite_session(self, user_id: str, access_token: str) -> dict[str, Any]:
        settings.kite_access_token = access_token
        settings.kite_user_id = user_id
        _save_token("zerodha", user_id, access_token)
        with _conn() as conn:
            conn.execute(
                "UPDATE account SET kite_user_id = ?, active_broker = ?, mode = ?, updated_at = ? WHERE id = 1",
                (user_id, "zerodha", "live", datetime.now().isoformat()),
            )
        return {"linked": True, "broker": "zerodha", "user_id": user_id, "status": self.status()}

    def _kite_client(self):
        from kiteconnect import KiteConnect

        tok = settings.kite_access_token or (_load_token("zerodha") or {}).get("access_token")
        if not settings.kite_api_key or not tok:
            raise RuntimeError("Zerodha not connected — set keys + access token")
        kite = KiteConnect(api_key=settings.kite_api_key)
        kite.set_access_token(tok)
        return kite

    def zerodha_funds(self) -> dict[str, Any]:
        kite = self._kite_client()
        return {"broker": "zerodha", "margins": kite.margins()}

    def zerodha_profile(self) -> dict[str, Any]:
        kite = self._kite_client()
        return {"broker": "zerodha", "profile": kite.profile()}

    # ── Dhan ────────────────────────────────────────────────
    def dhan_connect_info(self) -> dict[str, Any]:
        return {
            "broker": "dhan",
            "configured": bool(settings.dhan_client_id),
            "steps": [
                "web.dhan.co → My Profile → Access DhanHQ APIs → Access Token generate karo",
                ".env mein DHAN_CLIENT_ID aur DHAN_ACCESS_TOKEN set karo",
                "Ya POST /api/broker/dhan/link with client_id + access_token",
                "Active broker `dhan` set karo, mode `live`",
                "Note: Order APIs ke liye Dhan pe static IP whitelist zaroori ho sakta hai",
            ],
            "docs": "https://dhanhq.co/docs/v2/",
            "console": "https://web.dhan.co/",
        }

    def link_dhan_session(self, client_id: str, access_token: str) -> dict[str, Any]:
        settings.dhan_client_id = client_id
        settings.dhan_access_token = access_token
        _save_token("dhan", client_id, access_token)
        with _conn() as conn:
            conn.execute(
                """
                UPDATE account SET dhan_client_id = ?, active_broker = ?, mode = ?, updated_at = ?
                WHERE id = 1
                """,
                (client_id, "dhan", "live", datetime.now().isoformat()),
            )
        # Verify by fetching funds if possible
        verify: dict[str, Any] = {}
        try:
            verify = self.dhan_funds()
        except Exception as exc:
            verify = {"warning": str(exc)}
        return {
            "linked": True,
            "broker": "dhan",
            "client_id": client_id,
            "verify": verify,
            "status": self.status(),
        }

    def _dhan_client(self):
        try:
            from dhanhq import DhanContext, dhanhq
        except ImportError as exc:
            raise RuntimeError("dhanhq package not installed — pip install dhanhq") from exc

        tok_row = _load_token("dhan") or {}
        client_id = settings.dhan_client_id or tok_row.get("user_id")
        token = settings.dhan_access_token or tok_row.get("access_token")
        if not client_id or not token:
            raise RuntimeError("Dhan not connected — set DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN")
        ctx = DhanContext(str(client_id), str(token))
        return dhanhq(ctx)

    def dhan_funds(self) -> dict[str, Any]:
        dhan = self._dhan_client()
        data = dhan.get_fund_limits()
        return {"broker": "dhan", "funds": data}

    def dhan_positions(self) -> dict[str, Any]:
        dhan = self._dhan_client()
        return {"broker": "dhan", "positions": dhan.get_positions()}

    def resolve_dhan_security_id(self, symbol: str) -> str:
        sym = symbol.upper().replace(".NS", "").replace(" ", "")
        if sym in DHAN_SECURITY_IDS:
            return DHAN_SECURITY_IDS[sym]
        raise ValueError(
            f"Dhan security_id map mein `{sym}` nahi mila. "
            f"Known: {', '.join(sorted(DHAN_SECURITY_IDS)[:12])}... "
            "Map update karo ya security_id manually pass karo."
        )

    def _normalize_eq_symbol(self, symbol: str) -> str:
        return (
            symbol.upper()
            .replace(".NS", "")
            .replace(".BO", "")
            .replace(" ", "")
            .strip()
        )

    def live_quote(self, symbol: str, prefer: str | None = None) -> dict[str, Any] | None:
        """
        Live LTP from connected trading account (Zerodha / Dhan).
        Returns None if no broker connected or quote fails.
        """
        sym = self._normalize_eq_symbol(symbol)
        if sym in ("NIFTY", "NIFTY50"):
            kite_key = "NSE:NIFTY 50"
        elif sym in ("BANKNIFTY", "NIFTYBANK"):
            kite_key = "NSE:NIFTY BANK"
        else:
            kite_key = f"NSE:{sym}"

        order: list[str] = []
        active = (prefer or self.account_summary().get("active_broker") or "").lower()
        if active in ("zerodha", "dhan"):
            order.append(active)
        for b in ("zerodha", "dhan"):
            if b not in order and self._broker_connected(b):
                order.append(b)

        errors: list[str] = []
        for b in order:
            try:
                if b == "zerodha":
                    return self._zerodha_live_quote(sym, kite_key)
                if b == "dhan":
                    return self._dhan_live_quote(sym)
            except Exception as exc:
                errors.append(f"{b}: {exc}")
                continue
        return None

    def _zerodha_live_quote(self, symbol: str, kite_key: str) -> dict[str, Any]:
        kite = self._kite_client()
        # Prefer full quote (LTP + OHLC), fall back to LTP
        try:
            data = kite.quote(kite_key)
            block = data.get(kite_key) or {}
            if not block:
                raise ValueError(f"No Zerodha quote for {kite_key}")
            price = float(block.get("last_price") or 0)
            ohlc = block.get("ohlc") or {}
            prev = float(ohlc.get("close") or 0)
            if not price:
                raise ValueError("Zerodha last_price empty")
            change_pct = ((price - prev) / prev * 100) if prev else 0.0
            return {
                "symbol": symbol,
                "price": round(price, 2),
                "previous_close": round(prev, 2),
                "change_pct": round(change_pct, 2),
                "currency": "INR",
                "demo_data": False,
                "source": "zerodha",
                "broker": "zerodha",
                "instrument": kite_key,
                "volume": block.get("volume"),
                "ohlc": ohlc,
            }
        except Exception:
            ltp_map = kite.ltp(kite_key)
            block = ltp_map.get(kite_key) or {}
            price = float(block.get("last_price") or 0)
            if not price:
                raise ValueError(f"Zerodha LTP empty for {kite_key}")
            return {
                "symbol": symbol,
                "price": round(price, 2),
                "previous_close": 0.0,
                "change_pct": 0.0,
                "currency": "INR",
                "demo_data": False,
                "source": "zerodha",
                "broker": "zerodha",
                "instrument": kite_key,
            }

    def _dhan_live_quote(self, symbol: str) -> dict[str, Any]:
        sec_id = self.resolve_dhan_security_id(symbol)
        dhan = self._dhan_client()
        securities = {"NSE_EQ": [int(sec_id) if str(sec_id).isdigit() else sec_id]}

        # Prefer OHLC snapshot (includes LTP + close), else ticker
        block: dict[str, Any] = {}
        try:
            resp = dhan.ohlc_data(securities)
            data = (resp.get("data") if isinstance(resp, dict) else None) or resp or {}
            nse = data.get("NSE_EQ") or data.get("data", {}).get("NSE_EQ") or {}
            block = nse.get(str(sec_id)) or nse.get(int(sec_id)) or {}
        except Exception:
            block = {}

        if not block:
            resp = dhan.ticker_data(securities)
            data = (resp.get("data") if isinstance(resp, dict) else None) or resp or {}
            nse = data.get("NSE_EQ") or {}
            block = nse.get(str(sec_id)) or nse.get(int(sec_id)) or {}

        if not isinstance(block, dict) or not block:
            raise ValueError(f"Dhan quote empty for {symbol} ({sec_id})")

        price = float(
            block.get("last_price")
            or block.get("LTP")
            or block.get("ltp")
            or 0
        )
        ohlc = block.get("ohlc") or {}
        prev = float(
            ohlc.get("close")
            or block.get("close")
            or block.get("previous_close")
            or 0
        )
        if not price:
            raise ValueError(f"Dhan LTP empty for {symbol}")
        change_pct = ((price - prev) / prev * 100) if prev else 0.0
        return {
            "symbol": symbol,
            "price": round(price, 2),
            "previous_close": round(prev, 2),
            "change_pct": round(change_pct, 2),
            "currency": "INR",
            "demo_data": False,
            "source": "dhan",
            "broker": "dhan",
            "security_id": sec_id,
            "ohlc": ohlc or None,
        }

    def live_quotes(self, symbols: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {"broker_connected": False, "quotes": {}, "errors": {}}
        st = self.status()
        out["active_broker"] = st.get("active_broker")
        out["broker_connected"] = bool(
            st["brokers"]["zerodha"]["connected"] or st["brokers"]["dhan"]["connected"]
        )
        for sym in symbols:
            q = self.live_quote(sym)
            if q:
                out["quotes"][sym.upper()] = q
            else:
                out["errors"][sym.upper()] = "Broker not connected or quote unavailable"
        return out

    # ── Orders ──────────────────────────────────────────────
    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        segment: str = "EQ",
        product: str = "MIS",
        order_type: str = "MARKET",
        price: float | None = None,
        option_type: str | None = None,
        strike: float | None = None,
        expiry: str | None = None,
        security_id: str | None = None,
    ) -> dict[str, Any]:
        side = side.upper()
        segment = segment.upper()
        product = product.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        if qty <= 0:
            raise ValueError("qty must be > 0")

        summary = self.account_summary()
        mode = summary["mode"]
        active = summary["active_broker"]

        fill_price = price
        if fill_price is None:
            try:
                live = self.live_quote(symbol)
                if live and live.get("price"):
                    fill_price = live["price"]
                else:
                    fill_price = quote(symbol)["price"]
            except Exception:
                fill_price = 0.0
            if segment in ("OPT", "NFO") and option_type:
                fill_price = max(1.0, float(fill_price) * 0.008)

        order_id = f"{mode[:1].upper()}-{uuid.uuid4().hex[:10].upper()}"
        status = "COMPLETE"
        raw: dict[str, Any] = {"engine": "paper"}
        broker_name = "paper"

        if mode == "live":
            broker_name = active
            if active == "zerodha":
                raw = self._place_kite_order(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    product=product,
                    order_type=order_type,
                    price=price,
                    segment=segment,
                )
            elif active == "dhan":
                raw = self._place_dhan_order(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    product=product,
                    order_type=order_type,
                    price=price,
                    segment=segment,
                    security_id=security_id,
                )
            else:
                raise RuntimeError(f"Unknown active broker: {active}")
            order_id = str(raw.get("order_id") or order_id)
            status = str(raw.get("status") or "SUBMITTED")

        created = datetime.now().isoformat(timespec="seconds")
        with _conn() as conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, symbol, segment, side, product, qty, price, order_type,
                    status, option_type, strike, expiry, mode, broker, created_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    symbol.upper(),
                    segment,
                    side,
                    product,
                    qty,
                    fill_price,
                    order_type,
                    status,
                    option_type,
                    strike,
                    expiry,
                    mode,
                    broker_name,
                    created,
                    json.dumps(raw, default=str),
                ),
            )
            if status in ("COMPLETE", "SUBMITTED") and mode == "paper":
                self._update_paper_position(
                    conn,
                    symbol=symbol.upper(),
                    segment=segment,
                    side=side,
                    qty=qty,
                    price=float(fill_price or 0),
                    option_type=option_type,
                    strike=strike,
                    expiry=expiry,
                )

        return {
            "order_id": order_id,
            "status": status,
            "mode": mode,
            "broker": broker_name,
            "symbol": symbol.upper(),
            "side": side,
            "qty": qty,
            "price": fill_price,
            "segment": segment,
            "product": product,
            "option_type": option_type,
            "strike": strike,
            "expiry": expiry,
            "message": (
                "Paper order filled (simulated)."
                if mode == "paper"
                else f"Live order sent to {broker_name}."
            ),
            "raw": raw,
        }

    def _update_paper_position(
        self,
        conn: sqlite3.Connection,
        symbol: str,
        segment: str,
        side: str,
        qty: int,
        price: float,
        option_type: str | None,
        strike: float | None,
        expiry: str | None,
    ) -> None:
        signed = qty if side == "BUY" else -qty
        cost = signed * price
        acc = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
        cash = float(acc["cash"]) - cost
        conn.execute(
            "UPDATE account SET cash = ?, updated_at = ? WHERE id = 1",
            (cash, datetime.now().isoformat()),
        )

        row = conn.execute(
            """
            SELECT * FROM positions
            WHERE symbol = ? AND segment = ?
              AND IFNULL(option_type,'') = IFNULL(?,'')
              AND IFNULL(strike,0) = IFNULL(?,0)
              AND IFNULL(expiry,'') = IFNULL(?,'')
            """,
            (symbol, segment, option_type, strike, expiry),
        ).fetchone()

        if not row:
            if signed == 0:
                return
            conn.execute(
                """
                INSERT INTO positions (symbol, segment, qty, avg_price, option_type, strike, expiry, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, segment, signed, price, option_type, strike, expiry, datetime.now().isoformat()),
            )
            return

        new_qty = int(row["qty"]) + signed
        if new_qty == 0:
            conn.execute("DELETE FROM positions WHERE id = ?", (row["id"],))
            return
        if (row["qty"] > 0 and signed > 0) or (row["qty"] < 0 and signed < 0):
            avg = (abs(row["qty"]) * row["avg_price"] + abs(signed) * price) / abs(new_qty)
        else:
            avg = row["avg_price"]
        conn.execute(
            "UPDATE positions SET qty = ?, avg_price = ?, updated_at = ? WHERE id = ?",
            (new_qty, avg, datetime.now().isoformat(), row["id"]),
        )

    def _place_kite_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        product: str,
        order_type: str,
        price: float | None,
        segment: str,
    ) -> dict[str, Any]:
        kite = self._kite_client()
        exchange = "NSE" if segment == "EQ" else "NFO"
        tx = kite.TRANSACTION_TYPE_BUY if side == "BUY" else kite.TRANSACTION_TYPE_SELL
        ot = kite.ORDER_TYPE_MARKET if order_type.upper() == "MARKET" else kite.ORDER_TYPE_LIMIT
        if product in ("MIS", "INTRADAY", "INTRA"):
            prod = kite.PRODUCT_MIS
        elif product in ("NRML", "MARGIN"):
            prod = kite.PRODUCT_NRML
        else:
            prod = kite.PRODUCT_CNC

        params: dict[str, Any] = {
            "variety": kite.VARIETY_REGULAR,
            "exchange": exchange,
            "tradingsymbol": symbol.upper(),
            "transaction_type": tx,
            "quantity": qty,
            "product": prod,
            "order_type": ot,
        }
        if ot == kite.ORDER_TYPE_LIMIT and price is not None:
            params["price"] = price

        oid = kite.place_order(**params)
        return {"order_id": oid, "status": "SUBMITTED", "engine": "zerodha"}

    def _place_dhan_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        product: str,
        order_type: str,
        price: float | None,
        segment: str,
        security_id: str | None = None,
    ) -> dict[str, Any]:
        dhan = self._dhan_client()
        sec_id = security_id or self.resolve_dhan_security_id(symbol)

        if segment in ("OPT", "NFO"):
            exchange_segment = getattr(dhan, "NSE_FNO", "NSE_FNO")
        else:
            exchange_segment = getattr(dhan, "NSE", "NSE_EQ")

        tx = getattr(dhan, "BUY", "BUY") if side == "BUY" else getattr(dhan, "SELL", "SELL")
        ot = getattr(dhan, "MARKET", "MARKET") if order_type.upper() == "MARKET" else getattr(dhan, "LIMIT", "LIMIT")

        if product in ("MIS", "INTRADAY", "INTRA"):
            prod = getattr(dhan, "INTRA", "INTRADAY")
        elif product in ("NRML", "MARGIN"):
            prod = getattr(dhan, "MARGIN", "MARGIN")
        else:
            prod = getattr(dhan, "CNC", "CNC")

        kwargs: dict[str, Any] = {
            "security_id": str(sec_id),
            "exchange_segment": exchange_segment,
            "transaction_type": tx,
            "quantity": qty,
            "order_type": ot,
            "product_type": prod,
            "price": float(price or 0),
        }
        resp = dhan.place_order(**kwargs)

        # SDK may return dict with status/data
        order_id = None
        status = "SUBMITTED"
        if isinstance(resp, dict):
            data = resp.get("data") or resp
            if isinstance(data, dict):
                order_id = data.get("orderId") or data.get("order_id")
                status = data.get("orderStatus") or resp.get("status") or status
            elif resp.get("status") == "failure":
                raise RuntimeError(f"Dhan order failed: {resp}")
        return {
            "order_id": order_id or str(resp),
            "status": status,
            "engine": "dhan",
            "security_id": sec_id,
            "response": resp,
        }


broker = BrokerService()
