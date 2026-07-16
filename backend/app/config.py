"""AI Trading Bot — configuration."""
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "AI Trading Bot"
    debug: bool = True

    # paper | live
    trading_mode: str = "paper"
    # Which live broker to use when mode=live: zerodha | dhan
    active_broker: str = "zerodha"

    # Zerodha Kite Connect
    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""
    kite_user_id: str = ""

    # DhanHQ
    dhan_client_id: str = ""
    dhan_access_token: str = ""
    dhan_app_id: str = ""
    dhan_app_secret: str = ""

    # Optional LLM polish
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    max_risk_per_trade_pct: float = 1.0
    weekly_target_return_pct: float = 8.0
    paper_starting_cash: float = 500000.0

    data_dir: Path = Path(__file__).resolve().parents[2] / "data"

    class Config:
        env_file = str(Path(__file__).resolve().parents[2] / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
