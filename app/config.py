from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import lru_cache
from typing import Literal, cast

from pydantic import BaseModel, Field


def _format_deploy_tag(dt: datetime) -> str:
    return f"{dt.year % 100:02d}{dt.month:02d}{dt.day:02d}{dt.hour:02d}{dt.minute:02d}"


_ENV_VALUES = ("production", "staging", "development")
_SAMESITE_VALUES = ("lax", "strict")
ADMIN_IDENTITY_EMAIL = "provoz@hotelchodovasc.cz"


def _coerce_environment(value: str) -> Literal["production", "staging", "development"]:
    normalized = value.lower()
    if normalized in _ENV_VALUES:
        return cast(Literal["production", "staging", "development"], normalized)
    return "production"


def _coerce_cookie_samesite(value: str) -> Literal["lax", "strict"]:
    normalized = value.lower()
    if normalized in _SAMESITE_VALUES:
        return cast(Literal["lax", "strict"], normalized)
    return "lax"


class Settings(BaseModel):
    # --- App basics ---
    app_name: str = Field(default="DAGMAR", description="Human-readable app name")
    environment: Literal["production", "staging", "development"] = Field(
        default="production"
    )

    # --- Network / public URLs ---
    # Canonical domain required by spec.
    public_base_url: str = Field(
        default="https://dagmar.hcasc.cz",
        description="Public base URL used in links and Android WebView.",
    )

    # --- Backend bind ---
    bind_host: str = Field(default="127.0.0.1")
    bind_port: int = Field(default=8101)

    # --- Database ---
    database_url: str = Field(
        ...,
        description=(
            "PostgreSQL DSN. For production, DAGMAR DB is in Docker and published only on loopback: "
            "postgresql+psycopg://USER:PASS@127.0.0.1:5433/DBNAME"
        ),
    )
    db_pool_size: int = Field(default=5)
    db_max_overflow: int = Field(default=10)
    db_pool_timeout_seconds: int = Field(default=30)

    # --- Admin auth (single admin account) ---
    admin_username: str = Field(default=ADMIN_IDENTITY_EMAIL)
    # Provide either admin_password (to be hashed on seed) OR admin_password_hash.
    admin_password: str | None = Field(
        default=None, description="Plain password used only by seed_admin.sh"
    )
    admin_password_hash: str | None = Field(
        default=None,
        description="Password hash stored/used by backend. Preferred in production.",
    )

    # --- Session & CSRF secrets ---
    # These must be set in /etc/dagmar/backend.env
    session_secret: str = Field(..., min_length=32)
    csrf_secret: str = Field(..., min_length=32)

    # Cookie name for admin session.
    admin_session_cookie: str = Field(default="dagmar_admin_session")
    session_max_age_seconds: int = Field(default=60 * 60 * 12)  # 12h default

    # Cookie flags enforced by spec.
    cookie_secure: bool = Field(default=True)
    cookie_samesite: Literal["lax", "strict"] = Field(default="lax")

    # --- CORS ---
    # Frontend is served on the same domain by Nginx, so CORS can stay restrictive.
    cors_enabled: bool = Field(default=False)
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["https://dagmar.hcasc.cz"])

    # --- Rate limiting ---
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_default_per_minute: int = Field(default=120)
    rate_limit_admin_login_per_minute: int = Field(default=10)
    rate_limit_instance_status_per_minute: int = Field(default=60)
    rate_limit_instance_claim_per_minute: int = Field(default=30)

    # --- Security / tokens ---
    instance_token_length: int = Field(default=48, description="Random token length")

    # --- Logging ---
    log_level: str = Field(default="INFO")
    disable_docs: bool = Field(default=True)

    # --- Deploy metadata ---
    deploy_tag: str = Field(
        default_factory=lambda: _format_deploy_tag(datetime.now(UTC)),
        description="Kód nasazení backendu (YYMMDDHHMM).",
    )

    def ensure_canonical_domain(self) -> None:
        # Hard guard: forbid the incorrect domain anywhere in runtime config.
        bad = "dochazka.hcasc.cz"
        if bad in self.public_base_url:
            raise ValueError(f"Invalid domain detected in public_base_url: {bad} is forbidden")
        for origin in self.cors_allow_origins:
            if bad in origin:
                raise ValueError(f"Invalid domain detected in cors_allow_origins: {bad} is forbidden")

    # Compatibility aliases for legacy code
    @property
    def DATABASE_URL(self) -> str:
        return self.database_url

    @property
    def DB_POOL_SIZE(self) -> int:
        return self.db_pool_size

    @property
    def DB_MAX_OVERFLOW(self) -> int:
        return self.db_max_overflow

    @property
    def DB_POOL_TIMEOUT_SECONDS(self) -> int:
        return self.db_pool_timeout_seconds

    @property
    def session_cookie_name(self) -> str:
        return self.admin_session_cookie


def _load_env_file(path: str) -> None:
    """Minimal dotenv loader.

    We intentionally avoid third-party dotenv libs to keep dependencies minimal.
    Lines are KEY=VALUE, # comments allowed.

    Environment variables already set are NOT overwritten.
    """

    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if not k:
                continue
            os.environ.setdefault(k, v)


@lru_cache(maxsize=1)
def get_settings(env_file: str = "/etc/dagmar/backend.env") -> Settings:
    # Load env file into process env if present.
    _load_env_file(env_file)

    settings = Settings(
        app_name=os.getenv("DAGMAR_APP_NAME", "DAGMAR"),
        environment=_coerce_environment(os.getenv("DAGMAR_ENV", "production")),
        public_base_url=os.getenv("DAGMAR_PUBLIC_BASE_URL", "https://dagmar.hcasc.cz"),
        bind_host=os.getenv("DAGMAR_BIND_HOST", "127.0.0.1"),
        bind_port=int(os.getenv("DAGMAR_BIND_PORT", "8101")),
        database_url=os.environ["DAGMAR_DATABASE_URL"],
        db_pool_size=int(os.getenv("DAGMAR_DB_POOL_SIZE", "5")),
        db_max_overflow=int(os.getenv("DAGMAR_DB_MAX_OVERFLOW", "10")),
        db_pool_timeout_seconds=int(os.getenv("DAGMAR_DB_POOL_TIMEOUT_SECONDS", "30")),
        admin_username=ADMIN_IDENTITY_EMAIL,
        admin_password=os.getenv("DAGMAR_ADMIN_PASSWORD") or None,
        admin_password_hash=os.getenv("DAGMAR_ADMIN_PASSWORD_HASH") or None,
        session_secret=os.environ["DAGMAR_SESSION_SECRET"],
        csrf_secret=os.environ["DAGMAR_CSRF_SECRET"],
        admin_session_cookie=os.getenv(
            "DAGMAR_ADMIN_SESSION_COOKIE", os.getenv("DAGMAR_COOKIE_NAME", "dagmar_admin_session")
        ),
        session_max_age_seconds=int(os.getenv("DAGMAR_SESSION_MAX_AGE_SECONDS", str(60 * 60 * 12))),
        cookie_secure=os.getenv("DAGMAR_COOKIE_SECURE", "true").lower() == "true",
        cookie_samesite=_coerce_cookie_samesite(os.getenv("DAGMAR_COOKIE_SAMESITE", "lax")),
        cors_enabled=os.getenv("DAGMAR_CORS_ENABLED", "false").lower() == "true",
        cors_allow_origins=(
            [o.strip() for o in os.getenv("DAGMAR_CORS_ALLOW_ORIGINS", "https://dagmar.hcasc.cz").split(",") if o.strip()]
        ),
        rate_limit_enabled=os.getenv("DAGMAR_RATE_LIMIT_ENABLED", "true").lower() == "true",
        rate_limit_default_per_minute=int(os.getenv("DAGMAR_RATE_LIMIT_DEFAULT_PER_MINUTE", "120")),
        rate_limit_admin_login_per_minute=int(
            os.getenv("DAGMAR_RATE_LIMIT_ADMIN_LOGIN_PER_MINUTE", "10")
        ),
        rate_limit_instance_status_per_minute=int(
            os.getenv("DAGMAR_RATE_LIMIT_INSTANCE_STATUS_PER_MINUTE", "60")
        ),
        rate_limit_instance_claim_per_minute=int(
            os.getenv("DAGMAR_RATE_LIMIT_INSTANCE_CLAIM_PER_MINUTE", "30")
        ),
        instance_token_length=int(os.getenv("DAGMAR_INSTANCE_TOKEN_LENGTH", "48")),
        log_level=os.getenv("DAGMAR_LOG_LEVEL", "INFO"),
        disable_docs=os.getenv("DAGMAR_DISABLE_DOCS", "true").lower() == "true",
        deploy_tag=os.getenv(
            "DAGMAR_DEPLOY_TAG",
            _format_deploy_tag(datetime.now(UTC)),
        ),
    )

    settings.ensure_canonical_domain()
    return settings


# Provide a module-level settings instance for legacy imports.
settings = get_settings()
