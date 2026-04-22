from yoyo import step

from research_auto.infrastructure.postgres.schema import EXTENSION_SQL, CATALOG_SQL


steps = [step(EXTENSION_SQL), step(CATALOG_SQL)]
