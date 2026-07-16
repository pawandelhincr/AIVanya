from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ..deps import require_active_user, require_user
from ..services.auth import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    email: str = Field(..., min_length=5, max_length=120)
    password: str = Field(..., min_length=6, max_length=128)


class LoginIn(BaseModel):
    email: str
    password: str


class VerifyPayIn(BaseModel):
    payment_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class DemoActivateIn(BaseModel):
    payment_id: str | None = None


@router.get("/plans")
def plans():
    return auth.plans()


@router.post("/register")
def register(body: RegisterIn):
    try:
        return auth.register(body.name, body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login")
def login(body: LoginIn):
    try:
        return auth.login(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/logout")
def logout(
    user=Depends(require_user),
    authorization: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
):
    from ..deps import get_token

    token = get_token(authorization, x_auth_token)
    if token:
        auth.logout(token)
    return {"ok": True, "user_id": user["id"]}


@router.get("/me")
def me(user=Depends(require_user)):
    return {"user": user}


@router.post("/subscribe/checkout")
def checkout(user=Depends(require_user)):
    try:
        return auth.create_checkout(user["id"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/subscribe/verify")
def verify(body: VerifyPayIn, user=Depends(require_user)):
    try:
        return auth.verify_razorpay_payment(
            user_id=user["id"],
            payment_id=body.payment_id,
            razorpay_order_id=body.razorpay_order_id,
            razorpay_payment_id=body.razorpay_payment_id,
            razorpay_signature=body.razorpay_signature,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/subscribe/activate-demo")
def activate_demo(body: DemoActivateIn, user=Depends(require_user)):
    try:
        return auth.activate_demo(user["id"], body.payment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/gate-check")
def gate_check(user=Depends(require_active_user)):
    return {"ok": True, "user": user}
