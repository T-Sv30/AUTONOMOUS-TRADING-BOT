import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from runtime_config import JWT_SECRET


class LiveApprovalChallengeError(RuntimeError):
    pass


class LiveApprovalChallengeServiceV2:
    """Nonce-bound approval challenges for non-dry-run live orders."""

    VERSION = "live_approval_challenge_v2"
    DEFAULT_EXPIRY_SECONDS = 300

    def __init__(self, db):
        self.db = db

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def canonical_json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def sha256_payload(cls, payload: dict[str, Any]) -> str:
        return hashlib.sha256(cls.canonical_json(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_side(side: str) -> str:
        normalized = str(side).upper().strip()
        if normalized not in {"BUY", "SELL"}:
            raise LiveApprovalChallengeError("side must be BUY or SELL")
        return normalized

    @classmethod
    def order_intent(
        cls,
        *,
        user_id: str,
        side: str,
        symbol: str,
        notional_usd: float | None = None,
        base_units: float | None = None,
        reference_price: float | None = None,
    ) -> dict[str, Any]:
        intent = {
            "user_id": user_id,
            "side": cls.normalize_side(side),
            "symbol": str(symbol).strip().upper(),
            "dry_run": False,
        }
        if notional_usd is not None:
            intent["notional_usd"] = round(float(notional_usd), 8)
        if base_units is not None:
            intent["base_units"] = round(float(base_units), 12)
        if reference_price is not None:
            intent["reference_price"] = round(float(reference_price), 8)
            intent["notional_usd"] = round(float(base_units or 0.0) * float(reference_price), 8)
        return intent

    @classmethod
    def sign(cls, challenge_id: str, payload_hash: str) -> str:
        message = f"{cls.VERSION}:{challenge_id}:{payload_hash}".encode()
        return hmac.new(JWT_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()

    @classmethod
    def token_hash(cls, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def create_challenge(
        self,
        *,
        user_id: str,
        side: str,
        symbol: str,
        notional_usd: float | None = None,
        base_units: float | None = None,
        reference_price: float | None = None,
        expires_in_seconds: int = DEFAULT_EXPIRY_SECONDS,
    ) -> dict[str, Any]:
        now = self.utc_now()
        expires_at = now + timedelta(seconds=max(30, min(int(expires_in_seconds), 900)))
        challenge_id = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        intent = self.order_intent(
            user_id=user_id,
            side=side,
            symbol=symbol,
            notional_usd=notional_usd,
            base_units=base_units,
            reference_price=reference_price,
        )
        payload = {"intent": intent, "nonce": nonce, "expires_at": self.iso(expires_at), "version": self.VERSION}
        payload_hash = self.sha256_payload(payload)
        signature = self.sign(challenge_id, payload_hash)
        approval_token = f"{challenge_id}.{signature}"
        record = {
            "challenge_id": challenge_id,
            "user_id": user_id,
            "intent": intent,
            "nonce": nonce,
            "payload_hash": payload_hash,
            "approval_token_hash": self.token_hash(approval_token),
            "status": "pending",
            "created_at": self.iso(now),
            "expires_at": self.iso(expires_at),
            "expires_at_ts": expires_at,  # BSON date for the TTL index; readers keep using the ISO field
            "used_at": None,
            "version": self.VERSION,
        }
        await self.db.live_approval_challenges.insert_one(dict(record))  # copy: insert_one adds an ObjectId _id that breaks JSON responses
        return {
            "challenge_id": challenge_id,
            "approval_token": approval_token,
            "intent": intent,
            "expires_at": record["expires_at"],
            "status": "pending",
        }

    async def verify_and_consume(
        self,
        *,
        user_id: str,
        approval_token: str | None,
        side: str,
        symbol: str,
        notional_usd: float | None = None,
        base_units: float | None = None,
        reference_price: float | None = None,
    ) -> dict[str, Any]:
        if not approval_token or "." not in approval_token:
            raise LiveApprovalChallengeError("valid approval challenge token required")
        challenge_id, provided_signature = approval_token.split(".", 1)
        record = await self.db.live_approval_challenges.find_one({"challenge_id": challenge_id}, {"_id": 0})
        if not record:
            raise LiveApprovalChallengeError("approval challenge not found")
        expected_token_hash = record.get("approval_token_hash")
        if not hmac.compare_digest(expected_token_hash or "", self.token_hash(approval_token)):
            raise LiveApprovalChallengeError("approval challenge token mismatch")
        expected_signature = self.sign(challenge_id, record.get("payload_hash", ""))
        if not hmac.compare_digest(expected_signature, provided_signature):
            raise LiveApprovalChallengeError("approval challenge signature mismatch")
        if record.get("status") != "pending" or record.get("used_at"):
            raise LiveApprovalChallengeError("approval challenge already used")
        expires_at = datetime.fromisoformat(record["expires_at"])
        if self.utc_now() >= expires_at:
            await self.db.live_approval_challenges.update_one({"challenge_id": challenge_id}, {"$set": {"status": "expired"}})
            raise LiveApprovalChallengeError("approval challenge expired")
        expected_intent = self.order_intent(
            user_id=user_id,
            side=side,
            symbol=symbol,
            notional_usd=notional_usd,
            base_units=base_units,
            reference_price=reference_price,
        )
        if record.get("intent") != expected_intent:
            raise LiveApprovalChallengeError("approval challenge does not match requested order")
        used_at = self.iso(self.utc_now())
        result = await self.db.live_approval_challenges.update_one(
            {"challenge_id": challenge_id, "status": "pending", "used_at": None},
            {"$set": {"status": "used", "used_at": used_at}},
        )
        if getattr(result, "modified_count", 0) != 1:
            raise LiveApprovalChallengeError("approval challenge replay detected")
        return {"challenge_id": challenge_id, "status": "used", "used_at": used_at, "intent": expected_intent}
