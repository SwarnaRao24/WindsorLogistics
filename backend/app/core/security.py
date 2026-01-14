import os
from datetime import datetime, timedelta, timezone
import jwt

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALG = "HS256"
JWT_EXPIRE_MIN = int(os.getenv("JWT_EXPIRE_MIN", "60"))

def create_access_token(data: dict, role: str) -> str:
    payload = data.copy()
    payload["role"] = role
    payload["iat"] = int(datetime.now(timezone.utc).timestamp())
    payload["exp"] = int(
        (datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MIN)).timestamp()
    )

    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
