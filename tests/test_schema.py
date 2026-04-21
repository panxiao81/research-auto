from __future__ import annotations

from research_auto.infrastructure.postgres.schema import SCHEMA_SQL


def test_schema_adds_storage_columns_for_existing_artifacts_table() -> None:
    assert "alter table artifacts add column if not exists storage_uri text;" in SCHEMA_SQL
    assert "alter table artifacts add column if not exists storage_key text;" in SCHEMA_SQL
