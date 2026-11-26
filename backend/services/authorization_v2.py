from collections.abc import Iterable
from enum import Enum
from typing import Any


class Scope(str, Enum):
    TRADING_PAPER = "trading:paper"
    TRADING_LIVE_PREVIEW = "trading:live-preview"
    TRADING_LIVE_EXECUTE = "trading:live-execute"
    OPS_READINESS = "ops:readiness"
    OPS_INDEXES = "ops:indexes"
    OPS_HALT = "ops:halt"
    ADMIN_ROLES = "admin:roles"
    ADMIN_ALL = "admin:*"


class Role(str, Enum):
    USER = "user"
    TRADER = "trader"
    ADMIN = "admin"


DEFAULT_USER_SCOPES = {
    Scope.TRADING_PAPER.value,
    Scope.TRADING_LIVE_PREVIEW.value,
    Scope.OPS_READINESS.value,
    Scope.OPS_HALT.value,
}

ROLE_SCOPES = {
    Role.USER.value: DEFAULT_USER_SCOPES,
    Role.TRADER.value: DEFAULT_USER_SCOPES | {Scope.TRADING_LIVE_EXECUTE.value},
    Role.ADMIN.value: {Scope.ADMIN_ALL.value},
}


def normalize_values(values: Iterable[Any] | None) -> set[str]:
    return {str(value).strip() for value in (values or []) if str(value).strip()}


def normalize_roles(user: dict[str, Any]) -> set[str]:
    roles = normalize_values(user.get("roles"))
    if not roles:
        roles = {Role.USER.value}
    return roles


def explicit_scopes(user: dict[str, Any]) -> set[str]:
    return normalize_values(user.get("scopes"))


def effective_scopes(user: dict[str, Any]) -> set[str]:
    roles = normalize_roles(user)
    scopes = explicit_scopes(user)
    for role in roles:
        scopes |= set(ROLE_SCOPES.get(role, set()))
    return scopes


def has_scope(user: dict[str, Any], required_scope: str) -> bool:
    scopes = effective_scopes(user)
    return Scope.ADMIN_ALL.value in scopes or required_scope in scopes


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user["email"],
        "roles": sorted(normalize_roles(user)),
        "scopes": sorted(effective_scopes(user)),
    }
