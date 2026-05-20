from __future__ import annotations

from app.config import Settings
from app.db.schema_bootstrap import ensure_schema_up_to_date


def _settings() -> Settings:
    return Settings(
        database_url="sqlite:///./test.db",
        session_secret="x" * 32,
        csrf_secret="y" * 32,
    )


def test_ensure_schema_up_to_date_sets_database_url_and_restores_env(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_upgrade(cfg, revision: str) -> None:
        captured["revision"] = revision
        captured["database_url"] = __import__("os").environ["DATABASE_URL"]
        captured["script_location"] = cfg.get_main_option("script_location")

    monkeypatch.setattr("app.db.schema_bootstrap.command.upgrade", fake_upgrade)
    monkeypatch.setenv("DATABASE_URL", "postgresql://before")

    ensure_schema_up_to_date(_settings())

    assert captured["revision"] == "head"
    assert captured["database_url"] == "sqlite:///./test.db"
    assert captured["script_location"].endswith("app/db/migrations")
    assert __import__("os").environ["DATABASE_URL"] == "postgresql://before"
