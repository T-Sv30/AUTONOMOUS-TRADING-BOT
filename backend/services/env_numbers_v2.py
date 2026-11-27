"""Bounded numeric settings read from the environment.

One table, used by SettingsV2 at startup and by the services that read these values,
so a malformed or out-of-range value fails with the variable named instead of
silently falling back to a default. This module imports nothing from services, so
market_data_service and settings_v2 can both use it without an import cycle.
"""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class NumericSetting:
    kind: type
    default: float
    minimum: float
    maximum: float


NUMERIC_SETTINGS: dict[str, NumericSetting] = {
    "MARKET_DATA_TIMEOUT_SECONDS": NumericSetting(float, 10.0, 1, 120),
    "SIM_SPREAD_BPS": NumericSetting(float, 2.0, 0, 100),
    "PAPER_EXTRA_SLIPPAGE_BPS": NumericSetting(float, 0.0, 0, 100),
    "PAPER_FALLBACK_SPREAD_BPS": NumericSetting(float, 2.0, 0, 100),
    "LIVE_ORDER_TIMEOUT_SECONDS": NumericSetting(float, 10.0, 1, 60),
    "RISK_METRICS_RETENTION_DAYS": NumericSetting(int, 30, 1, 3650),
    "JWT_EXPIRE_HOURS": NumericSetting(int, 12, 1, 168),
}


def env_number(name: str, environ: Mapping[str, str] | None = None) -> float | int:
    """Return the validated value of ``name``; unset or blank means the default."""
    spec = NUMERIC_SETTINGS[name]
    environ = os.environ if environ is None else environ
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return spec.kind(spec.default)
    try:
        value = spec.kind(raw.strip())
    except ValueError:
        kind = "an integer" if spec.kind is int else "a number"
        raise ValueError(f"{name}={raw!r} is not {kind}") from None
    if not math.isfinite(value) or not spec.minimum <= value <= spec.maximum:
        raise ValueError(f"{name}={raw!r} is out of range; allowed {spec.kind(spec.minimum)} to {spec.kind(spec.maximum)}")
    return value
