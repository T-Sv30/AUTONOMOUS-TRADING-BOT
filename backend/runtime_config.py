
from pydantic import ValidationError

from services.settings_v2 import SETTINGS, RuntimeRole, SettingsV2


def env_bool(name: str, default: bool = False) -> bool:
    return SettingsV2.env_bool(name, default)


def parse_csv_env(name: str, default: str) -> list[str]:
    import os

    return SettingsV2.parse_csv(os.environ.get(name, default))


def parse_runtime_role(raw_role: str | None) -> RuntimeRole:
    try:
        return RuntimeRole(str(raw_role or "all").strip().lower())
    except ValueError as exc:
        allowed = ", ".join(role.value for role in RuntimeRole)
        raise RuntimeError(f"Invalid RUNTIME_ROLE={raw_role!r}. Allowed values: {allowed}") from exc


def _raise_runtime_error(exc: ValidationError) -> None:
    message = "; ".join(error.get("msg", "invalid setting") for error in exc.errors())
    raise RuntimeError(message) from exc


def validate_origin(origin: str) -> None:
    try:
        SettingsV2(debug=True, jwt_secret="debug", cors_origins=[origin])  # noqa: S106 - throwaway value, only the origin is validated  # nosec B106
    except ValidationError as exc:
        _raise_runtime_error(exc)


def validate_cors_origins(origins: list[str], debug: bool) -> None:
    try:
        SettingsV2(debug=debug, jwt_secret="debug" if debug else "x" * 32, cors_origins=origins)
    except ValidationError as exc:
        _raise_runtime_error(exc)


def validate_jwt_secret(secret: str, debug: bool) -> None:
    try:
        SettingsV2(debug=debug, jwt_secret=secret, cors_origins=["http://localhost:3000"])
    except ValidationError as exc:
        _raise_runtime_error(exc)


DEBUG = SETTINGS.debug
MARKET_DATA_SOURCE = SETTINGS.market_data_source
RUNTIME_ROLE = SETTINGS.runtime_role
RUN_MONGO_INDEX_BOOTSTRAP = SETTINGS.run_mongo_index_bootstrap
API_EMBED_BOT_MANAGER = SETTINGS.api_embed_bot_manager
OPS_ADMIN_ENABLED = SETTINGS.ops_admin_enabled
OPS_ADMIN_EMAILS = SETTINGS.ops_admin_emails
JWT_SECRET = SETTINGS.jwt_secret
CORS_ORIGINS = SETTINGS.cors_origins
