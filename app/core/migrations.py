from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def run_migrations() -> None:
    project_root = Path(__file__).resolve().parents[2]
    alembic_ini = project_root / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(project_root / "alembic"))
    command.upgrade(config, "head")
