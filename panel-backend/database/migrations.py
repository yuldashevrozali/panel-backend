from pathlib import Path

from sqlalchemy import text

from database.connection import engine


def run_migrations() -> None:
    migration_dir = Path(__file__).with_name("migrations")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(100) PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
        applied = {row[0] for row in connection.execute(text("SELECT version FROM schema_migrations"))}
        for migration in sorted(migration_dir.glob("*.sql")):
            if migration.name in applied:
                continue
            connection.execute(text(migration.read_text()))
            connection.execute(text("INSERT INTO schema_migrations (version) VALUES (:version)"), {"version": migration.name})
