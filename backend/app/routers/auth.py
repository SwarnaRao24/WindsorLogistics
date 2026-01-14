from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.core.security import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: str


def infer_role(ident: str) -> str:
    ident = ident.lower()
    if ident.startswith("owner"):
        return "owner"
    if ident.startswith("driver"):
        return "driver"
    if ident.startswith("customer"):
        return "customer"
    return ""


@router.post("/login")
async def login(body: LoginIn):
    ident = (body.email or body.username or "").strip()
    if not ident:
        raise HTTPException(status_code=422, detail="email or username required")

    # DEV ONLY:
    # - owner-1 / driver-1 / customer-1 all use password "password"
    if body.password != "password":
        raise HTTPException(status_code=401, detail="Invalid credentials")

    role = infer_role(ident)
    if not role:
        raise HTTPException(status_code=401, detail="Use owner-*, driver-*, customer-* usernames")

    token = create_access_token({"sub": ident}, role=role)
    return {"access_token": token, "token_type": "bearer", "role": role}
