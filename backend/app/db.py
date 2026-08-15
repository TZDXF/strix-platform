from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# 旧版部署的已有表新增列（create_all 只建新表，不补列）
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "tasks": [
        ("project_id", "VARCHAR(32)"),
        ("created_by", "INTEGER"),
        ("branch", "VARCHAR(255) DEFAULT ''"),
        ("upload_id", "VARCHAR(32)"),
        ("report_lang", "VARCHAR(8) DEFAULT 'en'"),
        ("zh_status", "VARCHAR(16) DEFAULT ''"),
        ("report_md", "TEXT DEFAULT ''"),
    ],
    "findings": [
        ("poc_description", "TEXT DEFAULT ''"),
        ("poc_code", "TEXT DEFAULT ''"),
        ("title_zh", "TEXT DEFAULT ''"),
        ("description_zh", "TEXT DEFAULT ''"),
        ("remediation_zh", "TEXT DEFAULT ''"),
    ],
    "audit_entries": [
        ("user_id", "INTEGER"),
    ],
}


def init_db() -> None:
    from . import models  # noqa: F401 确保模型注册

    Base.metadata.create_all(engine)
    _migrate()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate() -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _MIGRATIONS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
