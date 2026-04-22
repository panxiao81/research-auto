from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from research_auto.infrastructure.postgres.migrations import YoyoMigrationRunner


class Database:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            yield conn

    def bootstrap(self) -> None:
        self.migrate()

    def migrate(self) -> int:
        return YoyoMigrationRunner(self.dsn).migrate()
