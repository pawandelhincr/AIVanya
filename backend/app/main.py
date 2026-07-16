"""AI Trading Bot — FastAPI entrypoint."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routers.api import router
from .routers.auth import router as auth_router
from .services.broker import init_db
from .services.auth import init_auth_db, ensure_admin_user

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"

app = FastAPI(title=settings.app_name, version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(router)

static_dir = FRONTEND / "static"
templates_dir = FRONTEND / "templates"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()
    init_auth_db()
    ensure_admin_user()


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = templates_dir / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))
