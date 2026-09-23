"""Apply numbered SQL migrations from app/migrations/, tracked via PRAGMA user_version.

Files are named NNNN_description.sql. Each one runs in its own transaction
together with the user_version bump, so a failed migration leaves the DB untouched.
"""

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_[\w-]+\.sql$")


def list_migrations() -> list[tuple[int, Path]]:
    migrations = []
    for path in MIGRATIONS_DIR.iterdir():
        match = _NAME_RE.match(path.name)
        if match:
            migrations.append((int(match.group(1)), path))
    migrations.sort()
    versions = [v for v, _ in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError(f"Migration numbers must be contiguous from 0001, got {versions}")
    return migrations


def migrate(db_path: str | Path) -> int:
    """Bring the database up to the latest migration. Returns the resulting version."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version, path in list_migrations():
            if version <= current:
                continue
            script = path.read_text()
            try:
                conn.executescript(
                    f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
                )
            except sqlite3.Error as exc:
                conn.rollback()
                raise RuntimeError(f"Migration {path.name} failed: {exc}") from exc
            current = version
        return current
    finally:
        conn.close()
