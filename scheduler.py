"""
APScheduler achtergrondtaken voor Sluiskade.

Op dit moment één job:
    auto_purge_old_trash()
        Dagelijks om 03:00 UTC: vind soft-deleted foto's ouder dan 30
        dagen en verwijder ze definitief (DB-rij + bestanden van disk).

Gunicorn-safe: omdat Gunicorn meerdere workers draait, zou een naieve
init de job in elke worker schedulen. We gebruiken een filesystem-lock
zodat alleen de eerste worker (die de lock pakt) de scheduler start.
Andere workers slaan het stilletjes over.

Lock-file wordt automatisch vrijgegeven als het proces eindigt, dus
restart van de container herstelt 'm netjes.
"""
from __future__ import annotations

import atexit
import fcntl
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import db
import photo_service

log = logging.getLogger(__name__)

DATA_PATH = os.environ.get("DATA_PATH", "/data")
LOCK_PATH = os.path.join(DATA_PATH, "scheduler.lock")
PURGE_AFTER_DAYS = int(os.environ.get("PURGE_AFTER_DAYS", "30"))

_scheduler: Optional[BackgroundScheduler] = None
_lock_fd = None  # bewaar de fd globally zodat de lock blijft staan


def _try_acquire_lock() -> bool:
    """Probeer een exclusieve flock op LOCK_PATH te pakken.
    Returnt True als deze worker de lead-worker is."""
    global _lock_fd
    try:
        os.makedirs(DATA_PATH, exist_ok=True)
        _lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Schrijf PID erin voor debugging
        os.ftruncate(_lock_fd, 0)
        os.write(_lock_fd, str(os.getpid()).encode())
        return True
    except (OSError, IOError):
        if _lock_fd is not None:
            try:
                os.close(_lock_fd)
            except OSError:
                pass
            _lock_fd = None
        return False


def auto_purge_old_trash() -> dict:
    """De daadwerkelijke job. Verwijdert oude soft-deletes definitief.

    Returnt een dict met counts zodat de log meteen leesbaar is.
    Faalt graceful: één foto-probleem stopt niet de hele job.
    """
    started = datetime.now(timezone.utc)
    rows = db.purge_old_soft_deletes(days=PURGE_AFTER_DAYS)
    files_removed = 0
    for row in rows:
        try:
            photo_service.delete_files(row["filename"], row.get("thumb_filename"))
            files_removed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("Auto-purge kon files niet verwijderen voor id=%s: %s", row["id"], exc)

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    summary = {
        "rows_purged": len(rows),
        "files_removed": files_removed,
        "duration_ms": duration_ms,
    }
    if rows:
        log.info("Auto-purge done: %s", summary)
    return summary


def init_scheduler() -> None:
    """Start de scheduler in de lead-worker.
    Idempotent: meerdere calls zijn no-ops als 'ie al draait."""
    global _scheduler

    if _scheduler is not None:
        return

    if not _try_acquire_lock():
        log.info("Scheduler: andere worker heeft de lock, deze worker doet niets.")
        return

    log.info("Scheduler: lead-worker, taken worden gepland.")
    _scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
    _scheduler.add_job(
        auto_purge_old_trash,
        CronTrigger(hour=3, minute=0),  # elke dag 03:00 UTC
        id="auto_purge",
        name="Verwijder soft-deletes ouder dan 30 dagen",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    log.info("Scheduler: auto_purge job geplant (dagelijks 03:00 UTC, na %s dagen).", PURGE_AFTER_DAYS)

    # Net afsluiten als Gunicorn ons stuurt
    atexit.register(_shutdown)


def _shutdown() -> None:
    global _scheduler, _lock_fd
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None
