from __future__ import annotations

from pathlib import Path

from yoyo import get_backend, read_migrations


MIGRATIONS_DIR = Path(__file__).resolve().parent / "yoyo_migrations"


class YoyoMigrationRunner:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def migrate(self) -> int:
        backend = get_backend(self.dsn)
        migrations = read_migrations(str(MIGRATIONS_DIR))
        with backend.lock():
            to_apply = list(backend.to_apply(migrations))
            if to_apply:
                backend.apply_migrations(to_apply)
        return len(to_apply)
