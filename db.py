"""
SQLite database · single file at /data/sluiskade.db.

Schema is created on first access. We keep one table for now (photos);
allowed_residents, access_requests, otps and sessions arrive in Sprint 2
when the bewoner-portaal lands.

Soft-delete is built in: deleted_at != NULL means the photo is hidden
from every public view but still recoverable from the admin trash.
APScheduler will permanently purge entries older than 30 days (Sprint 3
admin work).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

DATA_PATH = os.environ.get("DATA_PATH", "/data")
DB_PATH = os.path.join(DATA_PATH, "sluiskade.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT    NOT NULL UNIQUE,
    thumb_filename  TEXT,
    source          TEXT    NOT NULL CHECK(source IN ('sluis', 'bewoner', 'admin')),
    uploader_email  TEXT,
    width           INTEGER,
    height          INTEGER,
    file_size       INTEGER,
    uploaded_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at      TEXT,
    deleted_by      TEXT,
    deleted_reason  TEXT
);

CREATE INDEX IF NOT EXISTS idx_photos_uploaded ON photos(uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_photos_deleted  ON photos(deleted_at);
CREATE INDEX IF NOT EXISTS idx_photos_source   ON photos(source);
"""


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """Context-managed SQLite connection with row factory and FK on.

    Uses sqlite3's default deferred-transaction mode and relies on the
    Python-level commit()/rollback() methods, which are no-ops when there
    is nothing pending. Avoids the autocommit + manual BEGIN/COMMIT
    confusion that bites you with executescript().
    """
    os.makedirs(DATA_PATH, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables and indexes if they don't exist yet. Idempotent.

    Also flips the database to WAL journal mode on first run · that's a
    persistent file-level setting so we only need to do it once.
    """
    with get_db() as conn:
        # WAL is persisted on the DB file itself; safe to set repeatedly.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Photo helpers
# ---------------------------------------------------------------------------

def insert_photo(
    *,
    filename: str,
    thumb_filename: Optional[str],
    source: str,
    uploader_email: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    file_size: Optional[int] = None,
) -> int:
    """Insert a row and return the new photo id."""
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO photos
                (filename, thumb_filename, source, uploader_email,
                 width, height, file_size)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (filename, thumb_filename, source, uploader_email,
             width, height, file_size),
        )
        return cur.lastrowid


def get_photo(photo_id: int) -> Optional[dict]:
    """Fetch one photo by id (including soft-deleted ones)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM photos WHERE id = ?", (photo_id,)
        ).fetchone()
        return dict(row) if row else None


def list_photos(
    *, limit: int = 50, offset: int = 0, include_deleted: bool = False
) -> list[dict]:
    """Return a page of photos, newest first."""
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM photos {where} "
            f"ORDER BY uploaded_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def count_photos(*, include_deleted: bool = False) -> int:
    """Total photo count (excludes soft-deleted unless asked)."""
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    with get_db() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM photos {where}"
        ).fetchone()[0]


def soft_delete_photo(
    photo_id: int, *, deleted_by: str, reason: Optional[str] = None
) -> bool:
    """Mark a photo as deleted. Returns True if a row was actually updated."""
    with get_db() as conn:
        cur = conn.execute(
            """
            UPDATE photos
            SET deleted_at = CURRENT_TIMESTAMP,
                deleted_by = ?,
                deleted_reason = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (deleted_by, reason, photo_id),
        )
        return cur.rowcount > 0
