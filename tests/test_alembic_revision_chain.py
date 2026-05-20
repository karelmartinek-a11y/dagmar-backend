from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_chain_resolves_head() -> None:
    alembic_ini = Path(__file__).resolve().parents[1] / "app" / "db" / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str((alembic_ini.parent / "migrations").as_posix()))

    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()

    assert heads == ["2026_05_20_0011"]
