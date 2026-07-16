"""User accounts, 7-day trial, ₹999 / 3-month subscription."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import settings

DB_PATH = settings.data_dir / "users.db"

PLAN_CODE = "pro_3m"
PLAN_NAME = "AIVanya Pro — 3 Months"
PLAN_PRICE_INR = 999
PLAN_DAYS = 90
TRIAL_DAYS = 7


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                trial_ends_at TEXT NOT NULL,
                plan_code TEXT,
                plan_expires_at TEXT,
                status TEXT NOT NULL DEFAULT 'trial'
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS payments (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_order_id TEXT,
                provider_payment_id TEXT,
                amount_inr INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'INR',
                status TEXT NOT NULL,
                plan_code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                raw_json TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
    )
    return f"{salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(_hash_password(password, salt), stored)


def _user_public(row: sqlite3.Row | dict) -> dict[str, Any]:
    d = dict(row)
    now = _utcnow()
    trial_ends = _parse(d.get("trial_ends_at"))
    plan_ends = _parse(d.get("plan_expires_at"))

    on_trial = bool(trial_ends and now <= trial_ends and not (plan_ends and now <= plan_ends))
    paid_active = bool(plan_ends and now <= plan_ends)
    active = paid_active or on_trial

    if paid_active:
        status = "active"
        access_until = plan_ends
    elif on_trial:
        status = "trial"
        access_until = trial_ends
    else:
        status = "expired"
        access_until = plan_ends or trial_ends

    days_left = max(0, (access_until - now).days) if access_until else 0
    return {
        "id": d["id"],
        "name": d["name"],
        "email": d["email"],
        "status": status,
        "active": active,
        "trial_ends_at": d.get("trial_ends_at"),
        "plan_code": d.get("plan_code"),
        "plan_expires_at": d.get("plan_expires_at"),
        "access_until": _iso(access_until) if access_until else None,
        "days_left": days_left,
        "plan": {
            "code": PLAN_CODE,
            "name": PLAN_NAME,
            "price_inr": PLAN_PRICE_INR,
            "duration_days": PLAN_DAYS,
            "trial_days": TRIAL_DAYS,
        },
    }


class AuthService:
    def __init__(self) -> None:
        init_auth_db()

    def register(self, name: str, email: str, password: str) -> dict[str, Any]:
        name = (name or "").strip()
        email = (email or "").strip().lower()
        if len(name) < 2:
            raise ValueError("Name kam se kam 2 characters ka hona chahiye")
        if "@" not in email or "." not in email.split("@")[-1]:
            raise ValueError("Valid email daalo")
        if len(password) < 6:
            raise ValueError("Password kam se kam 6 characters ka hona chahiye")

        user_id = uuid.uuid4().hex
        now = _utcnow()
        trial_ends = now + timedelta(days=TRIAL_DAYS)
        try:
            with _conn() as conn:
                conn.execute(
                    """
                    INSERT INTO users (id, name, email, password_hash, created_at, trial_ends_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'trial')
                    """,
                    (
                        user_id,
                        name,
                        email,
                        _hash_password(password),
                        _iso(now),
                        _iso(trial_ends),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Is email se account pehle se hai — login karo") from exc

        token = self._create_session(user_id)
        user = self.get_user(user_id)
        return {
            "token": token,
            "user": user,
            "message": f"Welcome! {TRIAL_DAYS}-day free trial shuru. Baad mein ₹{PLAN_PRICE_INR} / 3 months.",
        }

    def login(self, email: str, password: str) -> dict[str, Any]:
        email = (email or "").strip().lower()
        with _conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not _verify_password(password, row["password_hash"]):
            raise ValueError("Email ya password galat hai")
        token = self._create_session(row["id"])
        return {"token": token, "user": _user_public(row), "message": "Login successful"}

    def _create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = _utcnow()
        with _conn() as conn:
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, _iso(now), _iso(now + timedelta(days=30))),
            )
        return token

    def logout(self, token: str) -> None:
        with _conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def user_from_token(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        token = token.removeprefix("Bearer ").strip()
        with _conn() as conn:
            sess = conn.execute(
                "SELECT * FROM sessions WHERE token = ?", (token,)
            ).fetchone()
            if not sess:
                return None
            if _parse(sess["expires_at"]) and _utcnow() > _parse(sess["expires_at"]):
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                return None
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (sess["user_id"],)
            ).fetchone()
        return _user_public(row) if row else None

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with _conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_public(row) if row else None

    def require_active(self, token: str | None) -> dict[str, Any]:
        user = self.user_from_token(token)
        if not user:
            raise PermissionError("Login required")
        if not user["active"]:
            raise PermissionError(
                f"Trial/subscription khatam. ₹{PLAN_PRICE_INR} mein 3 months activate karo."
            )
        return user

    def plans(self) -> dict[str, Any]:
        return {
            "trial_days": TRIAL_DAYS,
            "plans": [
                {
                    "code": PLAN_CODE,
                    "name": PLAN_NAME,
                    "price_inr": PLAN_PRICE_INR,
                    "duration_days": PLAN_DAYS,
                    "features": [
                        "Cash + Options AI signals",
                        "News impact + weekly picks",
                        "Zerodha / Dhan broker map",
                        "Paper + live order desk",
                    ],
                }
            ],
        }

    def create_checkout(self, user_id: str) -> dict[str, Any]:
        """Create Razorpay order, or mock order if keys missing."""
        user = self.get_user(user_id)
        if not user:
            raise ValueError("User not found")

        payment_id = uuid.uuid4().hex
        now = _utcnow()
        amount_paise = PLAN_PRICE_INR * 100

        if settings.razorpay_key_id and settings.razorpay_key_secret:
            order = self._razorpay_create_order(amount_paise, payment_id, user)
            provider = "razorpay"
            provider_order_id = order["id"]
            raw = order
        else:
            provider = "manual"
            provider_order_id = f"mock_{payment_id[:12]}"
            raw = {
                "id": provider_order_id,
                "amount": amount_paise,
                "currency": "INR",
                "note": "Razorpay keys missing — use /subscribe/activate-demo for testing",
            }

        with _conn() as conn:
            conn.execute(
                """
                INSERT INTO payments (
                    id, user_id, provider, provider_order_id, amount_inr, status, plan_code, created_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?)
                """,
                (
                    payment_id,
                    user_id,
                    provider,
                    provider_order_id,
                    PLAN_PRICE_INR,
                    PLAN_CODE,
                    _iso(now),
                    json.dumps(raw),
                ),
            )

        return {
            "payment_id": payment_id,
            "provider": provider,
            "order_id": provider_order_id,
            "amount_inr": PLAN_PRICE_INR,
            "currency": "INR",
            "plan_code": PLAN_CODE,
            "razorpay_key_id": settings.razorpay_key_id or None,
            "prefill": {"name": user["name"], "email": user["email"]},
            "message": (
                "Razorpay checkout ready"
                if provider == "razorpay"
                else "Demo mode: Razorpay keys nahi hain. Testing ke liye Activate demo use karo."
            ),
        }

    def _razorpay_create_order(self, amount_paise: int, receipt: str, user: dict) -> dict:
        auth = (settings.razorpay_key_id, settings.razorpay_key_secret)
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt[:40],
            "notes": {"user_id": user["id"], "plan": PLAN_CODE, "email": user["email"]},
        }
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                "https://api.razorpay.com/v1/orders",
                auth=auth,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    def verify_razorpay_payment(
        self,
        user_id: str,
        payment_id: str,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> dict[str, Any]:
        if not settings.razorpay_key_secret:
            raise ValueError("Razorpay secret not configured")
        body = f"{razorpay_order_id}|{razorpay_payment_id}"
        expected = hmac.new(
            settings.razorpay_key_secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, razorpay_signature):
            raise ValueError("Invalid payment signature")

        with _conn() as conn:
            row = conn.execute(
                "SELECT * FROM payments WHERE id = ? AND user_id = ?",
                (payment_id, user_id),
            ).fetchone()
            if not row:
                raise ValueError("Payment record not found")
            if row["provider_order_id"] != razorpay_order_id:
                raise ValueError("Order id mismatch")
            conn.execute(
                """
                UPDATE payments SET status = 'paid', provider_payment_id = ?, raw_json = ?
                WHERE id = ?
                """,
                (
                    razorpay_payment_id,
                    json.dumps(
                        {
                            "order_id": razorpay_order_id,
                            "payment_id": razorpay_payment_id,
                            "signature": razorpay_signature,
                        }
                    ),
                    payment_id,
                ),
            )
        return self._activate_plan(user_id, source="razorpay", payment_id=payment_id)

    def activate_demo(self, user_id: str, payment_id: str | None = None) -> dict[str, Any]:
        """Only when Razorpay keys missing — for local testing."""
        if settings.razorpay_key_id and settings.razorpay_key_secret and not settings.allow_demo_subscribe:
            raise ValueError("Demo activate disabled when Razorpay is configured")
        if payment_id:
            with _conn() as conn:
                conn.execute(
                    "UPDATE payments SET status = 'paid_demo' WHERE id = ? AND user_id = ?",
                    (payment_id, user_id),
                )
        return self._activate_plan(user_id, source="demo", payment_id=payment_id)

    def _activate_plan(
        self, user_id: str, source: str, payment_id: str | None = None
    ) -> dict[str, Any]:
        now = _utcnow()
        user = self.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        current_end = _parse(user.get("plan_expires_at"))
        start = current_end if current_end and current_end > now else now
        new_end = start + timedelta(days=PLAN_DAYS)
        with _conn() as conn:
            conn.execute(
                """
                UPDATE users
                SET plan_code = ?, plan_expires_at = ?, status = 'active'
                WHERE id = ?
                """,
                (PLAN_CODE, _iso(new_end), user_id),
            )
        return {
            "activated": True,
            "source": source,
            "payment_id": payment_id,
            "plan_code": PLAN_CODE,
            "plan_expires_at": _iso(new_end),
            "user": self.get_user(user_id),
            "message": f"Pro plan active — ₹{PLAN_PRICE_INR} / 3 months. Valid till {new_end.date()}.",
        }


auth = AuthService()
