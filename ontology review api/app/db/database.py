from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


class Database:
    MIGRATION_LOCK_ID = 6_038_024_217_952_623_953
    REQUIRED_TABLES = {"alembic_version", "reviews", "plan_reviews", "idempotency_records"}
    REQUIRED_COLUMNS = {
        "plan_reviews": {"id", "plan_id", "tenant", "original_request_json", "normalized_request_json", "response_json", "ontology_checksum"},
        "idempotency_records": {"principal_id", "endpoint", "idempotency_key", "request_hash", "response_json", "review_id"},
    }

    def __init__(self, url: str):
        parsed_url = make_url(url)
        if parsed_url.get_backend_name() == "postgresql":
            if parsed_url.get_driver_name() != "psycopg":
                raise ValueError("PostgreSQL DATABASE_URL must use the psycopg driver")
            try:
                connect_timeout = int(parsed_url.query.get("connect_timeout", ""))
            except (TypeError, ValueError) as exc:
                raise ValueError("PostgreSQL DATABASE_URL requires an integer connect_timeout") from exc
            if not 1 <= connect_timeout <= 60:
                raise ValueError("PostgreSQL connect_timeout must be between 1 and 60 seconds")
        if url.startswith("sqlite:///"):
            raw_path = url.removeprefix("sqlite:///")
            if raw_path != ":memory:":
                Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.engine = create_engine(
            url,
            connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
            pool_pre_ping=True,
        )
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False, class_=Session)

    def _alembic_config(self) -> Config:
        project_root = Path(__file__).resolve().parents[2]
        config = Config(str(project_root / "alembic.ini"))
        config.set_main_option("script_location", str(project_root / "migrations"))
        # Alembic stores this value in ConfigParser, where a literal percent is
        # escaped as %% (common in URL-encoded database credentials).
        config.set_main_option("sqlalchemy.url", self.url.replace("%", "%%"))
        return config

    def migrate(self) -> None:
        config = self._alembic_config()
        if self.engine.dialect.name != "postgresql":
            command.upgrade(config, "head")
            return
        with self.engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": self.MIGRATION_LOCK_ID},
            )
            connection.commit()
            try:
                command.upgrade(config, "head")
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": self.MIGRATION_LOCK_ID},
                )
                connection.commit()

    def check(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                tables = set(inspect(connection).get_table_names())
                if not self.REQUIRED_TABLES.issubset(tables):
                    return False
                for table, required in self.REQUIRED_COLUMNS.items():
                    columns = {item["name"]: item for item in inspect(connection).get_columns(table)}
                    if not required.issubset(columns):
                        return False
                review_id_type = {item["name"]: item for item in inspect(connection).get_columns("idempotency_records")}["review_id"]["type"]
                if getattr(review_id_type, "length", 0) is not None and getattr(review_id_type, "length", 0) < 64:
                    return False
                current_heads = set(MigrationContext.configure(connection).get_current_heads())
                expected_heads = set(ScriptDirectory.from_config(self._alembic_config()).get_heads())
                if current_heads != expected_heads:
                    return False
            return True
        except Exception:
            return False

    def close(self) -> None:
        self.engine.dispose()
