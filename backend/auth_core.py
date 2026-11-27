import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jwt import ExpiredSignatureError, InvalidTokenError
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pymongo.errors import DuplicateKeyError

from app_state import db, logger, pwd_context, security
from runtime_config import JWT_EXPIRE_HOURS, JWT_SECRET

AUTH_FAILURE_WINDOW_MINUTES = 15
AUTH_FAILURE_LIMIT = 8  # per (email, ip) pair: another IP cannot lock the owner out
AUTH_IP_FAILURE_LIMIT = 30  # per IP across all emails: one IP cannot spray many accounts
# One answer for every signup failure, so signup does not reveal which emails are registered.
SIGNUP_FAILED_DETAIL = "Could not create account"


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
    to_encode.update({"exp": datetime.now(UTC) + timedelta(hours=JWT_EXPIRE_HOURS)})
    return jwt.encode(to_encode, JWT_SECRET, algorithm="HS256")


def token_for(user: dict[str, Any]) -> str:
    # tv is the user's token_version; logout-all bumps it, which revokes every older token.
    return create_access_token({"user_id": user["id"], "email": user["email"], "tv": int(user.get("token_version", 0))})


def token_version_matches(payload: dict[str, Any], user: dict[str, Any]) -> bool:
    # Tokens and users from before token versions count as version 0.
    return payload.get("tv", 0) == user.get("token_version", 0)


def verify_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except ExpiredSignatureError:
        logger.info("Rejected expired JWT")
        return None
    except InvalidTokenError as exc:
        logger.warning("Rejected invalid JWT: %s", exc.__class__.__name__)
        return None


def auth_failure_cutoff() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=AUTH_FAILURE_WINDOW_MINUTES)


async def recent_auth_failures(email: str, ip: str) -> int:
    return await db.auth_failures.count_documents({"email": email, "ip": ip, "created_at_ts": {"$gte": auth_failure_cutoff()}})


async def recent_ip_failures(ip: str) -> int:
    return await db.auth_failures.count_documents({"ip": ip, "created_at_ts": {"$gte": auth_failure_cutoff()}})


async def record_auth_failure(email: str, ip: str) -> None:
    now = datetime.now(UTC)
    # created_at_ts is a BSON date for the TTL index; created_at stays for existing readers.
    await db.auth_failures.insert_one({"email": email, "ip": ip, "created_at": now.isoformat(), "created_at_ts": now})


async def clear_auth_failures(email: str, ip: str) -> None:
    # Only this pair: a success from the owner's IP must not reset an attacker's counter elsewhere.
    await db.auth_failures.delete_many({"email": email, "ip": ip})


async def enforce_auth_throttle(email: str, ip: str) -> None:
    if await recent_auth_failures(email, ip) >= AUTH_FAILURE_LIMIT or await recent_ip_failures(ip) >= AUTH_IP_FAILURE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    payload = verify_token(credentials.credentials)
    if not payload or "user_id" not in payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await db.users.find_one({"id": payload["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not token_version_matches(payload, user):
        raise HTTPException(status_code=401, detail="Token revoked")
    return user


async def signup(user_data: UserCreate, ip: str):
    email = normalize_email(user_data.email)
    await enforce_auth_throttle(email, ip)
    existing = await db.users.find_one({"email": email})
    if existing:
        await record_auth_failure(email, ip)
        raise HTTPException(status_code=400, detail=SIGNUP_FAILED_DETAIL)

    user = User(email=email, password_hash=pwd_context.hash(user_data.password))
    user_dict = user.model_dump()
    user_dict["email"] = normalize_email(user_dict["email"])
    user_dict["created_at"] = user_dict["created_at"].isoformat()
    user_dict["token_version"] = 0
    try:
        await db.users.insert_one(user_dict)
    except DuplicateKeyError as exc:
        # A concurrent signup with the same email won the uniq_users_email index.
        await record_auth_failure(email, ip)
        raise HTTPException(status_code=400, detail=SIGNUP_FAILED_DETAIL) from exc

    config = {
        "user_id": user.id,
        "is_active": False,
        "max_daily_loss": 0.015,
        "risk_target_vol": 0.10,
        "symbols": ["BTC-USD", "ETH-USD"],
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await db.bot_configs.insert_one(config)
    await clear_auth_failures(email, ip)

    return TokenResponse(access_token=token_for(user_dict), user={"id": user.id, "email": user.email})


async def login(credentials: UserLogin, ip: str):
    email = normalize_email(credentials.email)
    await enforce_auth_throttle(email, ip)
    user = await db.users.find_one({"email": email}, {"_id": 0})
    if not user or not pwd_context.verify(credentials.password, user["password_hash"]):
        await record_auth_failure(email, ip)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    await clear_auth_failures(email, ip)
    return TokenResponse(access_token=token_for(user), user={"id": user["id"], "email": user["email"]})


async def logout_all(user: dict[str, Any]) -> dict[str, Any]:
    """Revoke every token issued to the user so far by bumping token_version."""
    await db.users.update_one({"id": user["id"]}, {"$inc": {"token_version": 1}})
    return {"status": "ok", "detail": "All existing sessions were signed out"}
