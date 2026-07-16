# AIVanya (TradeMind) — AI Trading Chat Bot

Cash + Options ke liye Hinglish AI trading desk: technical signals, news impact, Greeks, weekly delivery picks (~8% aspirational), paper trading, aur Zerodha / Dhan live order scaffolding.

## Features

| Area | Kya milta hai |
|------|----------------|
| Cash / intraday | RSI, MACD, EMA, Stoch, Bollinger, ADX, ATR → BUY / SELL / HOLD + SL/Target |
| Options | CE/PE suggestion, writing hints, Delta / Gamma / Theta / Vega / Rho |
| News | Google News RSS sentiment → trade bias |
| Weekly delivery | Watchlist se ~8% weekly move candidates (probability-based, not guaranteed) |
| Orders | Paper fill by default; live via Kite Connect when configured |
| Chat | Natural questions: `RELIANCE buy?`, `NIFTY options`, `buy 10 SBIN` |

## Quick start (Windows)

```powershell
cd "C:\Users\PawanSingh\Downloads\AI Boot For Trading"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
copy .env.example .env
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Browser: http://127.0.0.1:8000

Agar port busy ho to `--port 8001` use karo.

> **Note:** Agar Yahoo Finance block/rate-limit ho (common on some networks), bot automatically **demo candles** use karta hai taaki UI/chat kaam karta rahe. Live prices ke liye network allow karo ya baad mein NSE/broker data feed plug kar sakte ho.

## Chat examples

- `RELIANCE buy karna chahiye?`
- `INFY options` / `TCS delta theta`
- `weekly stocks` — delivery ideas near ~8% target
- `NIFTY news`
- `buy 10 SBIN` — paper order
- `buy 1 NIFTY CE 24500`
- `account` / `broker map`

## Zerodha + Dhan connect

### Zerodha (Kite Connect)

1. [developers.kite.trade](https://developers.kite.trade/) pe app banao.
2. Redirect URL set karo: `http://127.0.0.1:8001/api/broker/zerodha/callback`
3. `.env` mein:
   ```
   KITE_API_KEY=...
   KITE_API_SECRET=...
   ACTIVE_BROKER=zerodha
   ```
4. UI pe **Zerodha login** ya chat: `connect zerodha` → login URL.
5. Login ke baad callback token exchange karega → mode live + active zerodha.
6. Persistence ke liye `KITE_ACCESS_TOKEN` bhi `.env` mein save karo (roz 6 AM expire).

### DhanHQ

1. [web.dhan.co](https://web.dhan.co/) → Profile → **Access DhanHQ APIs** → token.
2. `.env` mein:
   ```
   DHAN_CLIENT_ID=...
   DHAN_ACCESS_TOKEN=...
   ACTIVE_BROKER=dhan
   ```
3. Chat: `connect dhan` → `use dhan` → `mode live`
4. Ya API: `POST /api/broker/dhan/link` `{"client_id":"...","access_token":"..."}`

> Dhan order APIs ke liye aksar **static IP whitelist** chahiye hota hai.

### Chat commands

- `connect zerodha` / `connect dhan`
- `use zerodha` / `use dhan`
- `mode live` / `mode paper`
- `account`

### Broker APIs

- `GET /api/broker/status`
- `GET /api/broker/zerodha/login`
- `POST /api/broker/zerodha/session` `{"request_token":"..."}`
- `POST /api/broker/dhan/link`
- `GET /api/broker/zerodha/funds` · `GET /api/broker/dhan/funds`
- `POST /api/broker/active` `{"broker":"zerodha"|"dhan"|"paper"}`

## API (optional)

- `POST /api/chat` `{"message":"..."}`
- `GET /api/signal/RELIANCE`
- `GET /api/options/NIFTY`
- `GET /api/weekly`
- `GET /api/news?symbol=TCS`
- `POST /api/orders`
- `GET /api/account`

## User accounts & subscription

- **Signup** → automatic **7-day free trial**
- After trial → **₹999 for 3 months** (AIVanya Pro)
- Payment via **Razorpay** (set `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`)
- Without Razorpay keys, UI has **Demo activate** for local testing (`ALLOW_DEMO_SUBSCRIBE=true`)

### Auth APIs

- `POST /api/auth/register` `{name,email,password}`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/subscribe/checkout`
- `POST /api/auth/subscribe/verify` (Razorpay signature)
- `POST /api/auth/subscribe/activate-demo`

Chat / signals / orders require `Authorization: Bearer <token>` and active trial/plan.

Yeh tool **educational** hai. Signals guarantee nahi. Options / leverage se poora capital loss ho sakta hai. ~8% weekly return rare aur risky hai — hit-rate historical filter hai, promise nahi. Live trading se pehle paper practice + SEBI-registered advisor.

## Project layout

```
backend/app/          FastAPI + services (market, options, news, weekly, broker, chat)
frontend/             Chat UI (HTML/CSS/JS)
data/                 Paper account SQLite
.env.example          Config template
```
