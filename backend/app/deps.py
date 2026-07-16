"""Auth dependency helpers."""
from __future__ import annotations

from fastapi import Header, HTTPException

from .services.auth import auth


def get_token(
    authorization: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return x_auth_token


def optional_user(authorization: str | None = Header(default=None), x_auth_token: str | None = Header(default=None, alias="X-Auth-Token")):
    token = get_token(authorization, x_auth_token)
    return auth.user_from_token(token)


def require_user(authorization: str | None = Header(default=None), x_auth_token: str | None = Header(default=None, alias="X-Auth-Token")):
    token = get_token(authorization, x_auth_token)
    user = auth.user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def require_active_user(authorization: str | None = Header(default=None), x_auth_token: str | None = Header(default=None, alias="X-Auth-Token")):
    token = get_token(authorization, x_auth_token)
    try:
        return auth.require_active(token)
    except PermissionError as exc:
        msg = str(exc)
        code = 401 if "Login" in msg else 402
        raise HTTPException(status_code=code, detail=msg) from exc
