"""Lifecycle daemon for ingestion jobs.

One background thread (started from the FastAPI lifespan) ticks every
``DAEMON_INTERVAL_SECONDS`` and performs five idempotent passes:

  1. Orphan sweep   - jobs whose worker died (no heartbeat past
                      ``STALE_THRESHOLD_SECONDS``) are finalized as
                      ``FAILED(INTERNAL_ERROR)``.
  2. Stuck sweep    - jobs whose worker is still alive (heartbeat fresh) but
                      whose progress hasn't moved past
                      ``STUCK_THRESHOLD_SECONDS`` are finalized as
                      ``FAILED(STUCK)``. Catches a worker wedged inside a
                      library or subprocess call, which the orphan sweep
                      can't see because the background heartbeat timer keeps
                      beating regardless.
  3. Timeout sweep  - jobs that exceeded ``MAX_JOB_DURATION_SECONDS`` are
                      finalized as ``FAILED(TIMEOUT)``. This is a high,
                      untuned safety ceiling, not a health check - the
                      backstop for cases where nothing above caught it.
  4. Retention      - terminal jobs older than their per-state retention
                      window are hard-deleted (and any leftover temp file
                      is unlinked).
  5. Temp-file GC   - spooled files that no row references are unlinked,
                      with a generous freshness grace so an in-flight submit
                      is never targeted.

Every transition goes through :func:`jobs.finalize_externally`, which routes
into the same state-guarded ``_finalize`` the worker uses, so the daemon and
a finishing worker can never both succeed on the same job.
"""

import os
import threading
from datetime import datetime, timedelta, timezone

from talkingdb.clients.sqlite import sqlite_conn, GRAPH_DB
from talkingdb.helpers import spool
from talkingdb.logger.console import logger
from talkingdb.helpers.job import store as job_store
from talkingdb.models.failure import messages as failures
from talkingdb.models.failure.reason import FailureReason
from talkingdb.models.job.error import JobErrorCode
from talkingdb.models.job.state import JobState

from app.core import config
from app.services import jobs


_TEMP_FILE_GRACE_SECONDS = 10 * 60


_stop = threading.Event()
_thread: threading.Thread | None = None


# ----------------------------------------------------------------- helpers
def _now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Convert a datetime to ISO format."""
    return dt.isoformat()


# ---------------------------------------------------------------- passes
def _sweep_orphans(now: datetime) -> None:
    """Fail jobs whose heartbeat became stale."""
    stale_before = _iso(now - timedelta(seconds=config.STALE_THRESHOLD_SECONDS))
    with sqlite_conn(GRAPH_DB) as conn:
        candidates = job_store.select_orphan_candidates(conn, stale_before)
    for job in candidates:
        logger.warning(
            f"[daemon] orphan: {job.job_id} state={job.state.value} "
            f"stage={job.stage.value if job.stage else None}"
        )

        jobs.finalize_externally(
            job.job_id,
            JobState.FAILED,
            graph_id=job.result_graph_id,
            temp_path=job.temp_path,
            error_code=JobErrorCode.INTERNAL_ERROR,
            error_message=failures.GENERIC_MESSAGE,
            failure_reason=FailureReason.PROCESSING_FAILED,
            status_message="Upload failed",
        )


def _sweep_stuck(now: datetime) -> None:
    """Fail jobs that are alive (fresh heartbeat) but not progressing."""
    stuck_before = _iso(now - timedelta(seconds=config.STUCK_THRESHOLD_SECONDS))
    heartbeat_fresh_after = _iso(now - timedelta(seconds=config.STALE_THRESHOLD_SECONDS))
    with sqlite_conn(GRAPH_DB) as conn:
        candidates = job_store.select_stuck_candidates(
            conn, stuck_before, heartbeat_fresh_after
        )
    for job in candidates:
        logger.warning(
            f"[daemon] stuck: {job.job_id} state={job.state.value} "
            f"stage={job.stage.value if job.stage else None} "
            f"no progress for over {config.STUCK_THRESHOLD_SECONDS}s"
        )

        jobs.finalize_externally(
            job.job_id,
            JobState.FAILED,
            graph_id=job.result_graph_id,
            temp_path=job.temp_path,
            error_code=JobErrorCode.STUCK,
            error_message=failures.GENERIC_MESSAGE,
            failure_reason=FailureReason.PROCESSING_FAILED,
            status_message="Upload stalled",
        )


def _sweep_timeouts(now: datetime) -> None:
    """Fail jobs that exceeded the timeout."""
    deadline = _iso(now - timedelta(seconds=config.MAX_JOB_DURATION_SECONDS))
    with sqlite_conn(GRAPH_DB) as conn:
        candidates = job_store.select_timeout_candidates(conn, deadline)
    for job in candidates:
        logger.warning(
            f"[daemon] timeout: {job.job_id} stage="
            f"{job.stage.value if job.stage else None} "
            f"exceeded MAX_JOB_DURATION_SECONDS="
            f"{config.MAX_JOB_DURATION_SECONDS}"
        )

        jobs.finalize_externally(
            job.job_id,
            JobState.FAILED,
            graph_id=job.result_graph_id,
            temp_path=job.temp_path,
            error_code=JobErrorCode.TIMEOUT,
            error_message=failures.GENERIC_MESSAGE,
            failure_reason=FailureReason.PROCESSING_FAILED,
            status_message="Upload timed out",
        )


def _purge_retention(now: datetime) -> None:
    """Delete terminal job rows that are past their retention window."""
    completed_before = _iso(
        now - timedelta(seconds=config.RETENTION_COMPLETED_SECONDS)
    )

    failed_before = _iso(
        now - timedelta(seconds=config.RETENTION_FAILED_SECONDS)
    )

    cancelled_before = _iso(
        now - timedelta(seconds=config.RETENTION_CANCELLED_SECONDS)
    )

    with sqlite_conn(GRAPH_DB) as conn:
        expired = job_store.select_retention_expired(
            conn,
            completed_before_iso=completed_before,
            failed_before_iso=failed_before,
            cancelled_before_iso=cancelled_before,
        )

    for job in expired:
        spool.discard(job.temp_path)

        with sqlite_conn(GRAPH_DB) as conn:
            job_store.delete(conn, job.job_id)


def _gc_orphan_temp_files(now: datetime) -> None:
    """Delete unreferenced temp files."""
    if not os.path.isdir(spool.SPOOL_DIR):
        return

    with sqlite_conn(GRAPH_DB) as conn:
        referenced = job_store.select_referenced_temp_paths(conn)

    grace = now - timedelta(seconds=_TEMP_FILE_GRACE_SECONDS)

    for entry in os.scandir(spool.SPOOL_DIR):
        if not entry.is_file():
            continue

        if entry.path in referenced:
            continue

        try:
            mtime = datetime.fromtimestamp(
                entry.stat().st_mtime, tz=timezone.utc
            )
        except FileNotFoundError:
            continue

        if mtime > grace:
            continue

        spool.discard(entry.path)


def tick() -> None:
    """Run one daemon cycle."""
    now = _now_utc()

    _sweep_orphans(now)
    _sweep_stuck(now)
    _sweep_timeouts(now)
    _purge_retention(now)
    _gc_orphan_temp_files(now)


# ------------------------------------------------------------------- loop
def _loop() -> None:
    """Run the daemon loop."""
    logger.info("[daemon] started")

    while not _stop.is_set():
        try:
            tick()
        except Exception:
            logger.exception("[daemon] tick failed")

        _stop.wait(config.DAEMON_INTERVAL_SECONDS)

    logger.info("[daemon] stopped")


def start() -> None:
    """Start the daemon thread. Idempotent - safe to call from lifespan."""
    global _thread

    if _thread is not None and _thread.is_alive():
        return

    _stop.clear()
    _thread = threading.Thread(target=_loop, name="tdb-job-daemon", daemon=True)
    _thread.start()


def stop() -> None:
    """Stop the daemon loop."""
    _stop.set()
