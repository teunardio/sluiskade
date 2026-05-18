"""
SQLite database, single file at /data/sluiskade.db.

Schema is created on first access. Four tables:
    photos              uploaded pictures with soft-delete
    allowed_residents   whitelist of email addresses that may log in
    access_requests     pending toegangsaanvragen from non-whitelisted users
    bewoner_otps        one-time codes for the magic-link login

Sessions are stateless (signed cookies via itsdangerous), so no table
for those.

Soft-delete is built in for photos: deleted_at != NULL means the photo
is hidden from every public view but still recoverable from the admin
trash. APScheduler will permanently purge entries older than 30 days
(Sprint 3 admin work).
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


CREATE TABLE IF NOT EXISTS allowed_residents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name        TEXT,
    added_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    added_by    TEXT
);


CREATE TABLE IF NOT EXISTS access_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT    NOT NULL COLLATE NOCASE,
    voornaam     TEXT,
    achternaam   TEXT,
    motivatie    TEXT,
    status       TEXT    NOT NULL DEFAULT 'pending'
                 CHECK(status IN ('pending', 'approved', 'rejected')),
    requested_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    handled_at   TEXT,
    handled_by   TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_status ON access_requests(status);


CREATE TABLE IF NOT EXISTS bewoner_otps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT    NOT NULL COLLATE NOCASE,
    code        TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    used_at     TEXT,
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_otps_email   ON bewoner_otps(email);
CREATE INDEX IF NOT EXISTS idx_otps_expires ON bewoner_otps(expires_at);
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


def hard_delete_photo(photo_id: int) -> Optional[dict]:
    """Remove a photo row from the database entirely.

    Returns the deleted row as a dict (so the caller can also remove
    the files from disk via photo_service.delete_files), or None if no
    row existed. Used by bewoners to permanently delete their own uploads;
    sluiswachters always go through soft_delete_photo so the admin can
    recover.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM photos WHERE id = ?", (photo_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        return dict(row)


# ---------------------------------------------------------------------------
# Allowed-residents helpers (whitelist)
# ---------------------------------------------------------------------------

def add_allowed_resident(
    email: str, *, name: Optional[str] = None, added_by: Optional[str] = None
) -> int:
    """Add an email to the whitelist. Idempotent: returns the existing id
    if the email is already on the list."""
    email = email.strip().lower()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM allowed_residents WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            return existing["id"]
        cur = conn.execute(
            "INSERT INTO allowed_residents (email, name, added_by) VALUES (?, ?, ?)",
            (email, name, added_by),
        )
        return cur.lastrowid


def is_email_allowed(email: str) -> bool:
    """True if this email is on the allowed_residents list."""
    email = email.strip().lower()
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM allowed_residents WHERE email = ?", (email,)
        ).fetchone()
        return row is not None


def list_allowed_residents() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM allowed_residents ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def remove_allowed_resident(email: str) -> bool:
    email = email.strip().lower()
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM allowed_residents WHERE email = ?", (email,)
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Access-request helpers
# ---------------------------------------------------------------------------

def save_access_request(
    email: str,
    voornaam: str,
    achternaam: str,
    motivatie: Optional[str] = None,
) -> int:
    email = email.strip().lower()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO access_requests (email, voornaam, achternaam, motivatie)
            VALUES (?, ?, ?, ?)
            """,
            (email, voornaam, achternaam, motivatie),
        )
        return cur.lastrowid


def list_pending_access_requests() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM access_requests WHERE status = 'pending' "
            "ORDER BY requested_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_access_request(request_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM access_requests WHERE id = ?", (request_id,)
        ).fetchone()
        return dict(row) if row else None


def mark_access_request_handled(
    request_id: int, *, new_status: str, handled_by: str
) -> bool:
    if new_status not in ("approved", "rejected"):
        raise ValueError("new_status must be 'approved' or 'rejected'")
    with get_db() as conn:
        cur = conn.execute(
            """
            UPDATE access_requests
            SET status = ?, handled_at = CURRENT_TIMESTAMP, handled_by = ?
            WHERE id = ? AND status = 'pending'
            """,
            (new_status, handled_by, request_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Bewoner OTP helpers
# ---------------------------------------------------------------------------

def save_bewoner_otp(email: str, code: str, expires_at: str) -> int:
    """Store a new OTP code. expires_at must be ISO-8601 UTC."""
    email = email.strip().lower()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO bewoner_otps (email, code, expires_at) VALUES (?, ?, ?)",
            (email, code, expires_at),
        )
        return cur.lastrowid


def get_valid_bewoner_otp(email: str, code: str) -> Optional[dict]:
    """Return the OTP row if it matches and is unused and unexpired."""
    email = email.strip().lower()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM bewoner_otps
            WHERE email = ?
              AND code = ?
              AND used_at IS NULL
              AND expires_at > CURRENT_TIMESTAMP
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email, code),
        ).fetchone()
        return dict(row) if row else None


def mark_bewoner_otp_used(otp_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE bewoner_otps SET used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (otp_id,),
        )


def cleanup_expired_otps() -> int:
    """Delete OTPs that are expired or used. Returns rows deleted.
    Called periodically by APScheduler (Sprint 3 housekeeping)."""
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM bewoner_otps "
            "WHERE expires_at < datetime('now', '-1 day') OR used_at IS NOT NULL"
        )
        return cur.rowcount
