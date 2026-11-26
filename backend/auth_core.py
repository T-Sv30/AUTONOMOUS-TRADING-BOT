import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jwt import ExpiredSignatureError, InvalidTokenError
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app_state import db, logger, pwd_context, security
from runtime_config import JWT_SECRET

AUTH_FAILURE_WINDOW_MINUTES = 15
AUTH_FAILURE_LIMIT = 8


class User(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: EmailStr
    password_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type label, not a secret
    user: dict[str, Any]


def normalize_email(email: str) -> str:
    return str(email).lower().strip()


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    to_encode.update({"exp": datetime.now(UTC) + timedelta(days=7)})
    return jwt.encode(to_encode, JWT_SECRET, algorithm="HS256")


def verify_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except ExpiredSignatureError:
        logger.info("Rejected expired JWT")
        return None
    except InvalidTokenError as exc:
        logger.warning("Rejected invalid JWT: %s", exc.__class__.__name__)
        return None


async def recent_auth_failures(email: str) -> int:
    cutoff = (datetime.now(UTC) - timedelta(minutes=AUTH_FAILURE_WINDOW_MINUTES)).isoformat()
    return await db.auth_failures.count_documents({"email": email, "created_at": {"$gte": cutoff}})


async def record_auth_failure(email: str) -> None:
    await db.auth_failures.insert_one({"email": email, "created_at": datetime.now(UTC).isoformat()})


async def clear_auth_failures(email: str) -> None:
    await db.auth_failures.delete_many({"email": email})


async def enforce_auth_throttle(email: str) -> None:
    failures = await recent_auth_failures(email)
    if failures >= AUTH_FAILURE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    payload = verify_token(credentials.credentials)
    if not payload or "user_id" not in payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def signup(user_data: UserCreate):
    email = normalize_email(user_data.email)
    await enforce_auth_throttle(email)
    existing = await db.users.find_one({"email": email})
    if existing:
        await record_auth_failure(email)
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(email=email, password_hash=pwd_context.hash(user_data.password))
    user_dict = user.model_dump()
    user_dict["email"] = normalize_email(user_dict["email"])
    user_dict["created_at"] = user_dict["created_at"].isoformat()
    await db.users.insert_one(user_dict)

    config = {
        "user_id": user.id,
        "is_active": False,
        "max_daily_loss": 0.015,
        "risk_target_vol": 0.10,
        "symbols": ["BTC-USD", "ETH-USD"],
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await db.bot_configs.insert_one(config)
    await clear_auth_failures(email)

    token = create_access_token({"user_id": user.id, "email": user.email})
    return TokenResponse(access_token=token, user={"id": user.id, "email": user.email})


async def login(credentials: UserLogin):
    email = normalize_email(credentials.email)
    await enforce_auth_throttle(email)
    user = await db.users.find_one({"email": email}, {"_id": 0})
    if not user or not pwd_context.verify(credentials.password, user["password_hash"]):
        await record_auth_failure(email)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    await clear_auth_failures(email)
    token = create_access_token({"user_id": user["id"], "email": user["email"]})
    return TokenResponse(access_token=token, user={"id": user["id"], "email": user["email"]})
