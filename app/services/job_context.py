import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from talkingdb.clients.sqlite import sqlite_conn, GRAPH_DB
from talkingdb.logger.console import logger
from talkingdb.helpers.job import store as job_store
from talkingdb.models.job.stage import JobStage

from app.core import config


class JobControl(Exception):
    """Base class for job control-flow signals."""


class JobCancelled(JobControl):
    """Raised when a job is cancelled."""


class JobTimeout(JobControl):
    """Raised when a job exceeds the timeout."""


@dataclass
class JobContext:
    """Tracks job progress, heartbeat, and runtime state."""

    job_id: str
    started_monotonic: float = field(default_factory=time.monotonic)
    _last_heartbeat_monotonic: float = field(default=0.0)
    _stage: Optional[JobStage] = None
    _hb_stop: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    _hb_thread: Optional[threading.Thread] = field(default=None, repr=False)
    _cancel_event: threading.Event = field(
        default_factory=threading.Event, repr=False
    )

    # ----------------------------------------------------------- utilities
    def elapsed_seconds(self) -> float:
        """Return elapsed runtime in seconds."""
        return time.monotonic() - self.started_monotonic

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ------------------------------------------------- background heartbeat
    def start_background_heartbeat(self) -> None:
        if self._hb_thread is not None:
            return
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"tdb-job-hb-{self.job_id}",
            daemon=True,
        )
        self._hb_thread.start()

    def stop_background_heartbeat(self) -> None:
        """Stop the heartbeat timer. Idempotent; safe to call from a finally."""
        self._hb_stop.set()
        thread = self._hb_thread
        if thread is not None:
            thread.join(timeout=5)
            self._hb_thread = None

    def _heartbeat_loop(self) -> None:
        """Poll cancellation frequently without changing heartbeat writes.

        Heartbeat writes remain on ``BACKGROUND_HEARTBEAT_INTERVAL_SECONDS``.
        This shorter ``CANCEL_POLL_INTERVAL_SECONDS`` cadence only reads
        ``cancel_requested`` and latches ``_cancel_event``, bounding cancellation
        latency across long-running worker stages without adding write pressure.
        """
        write_interval = config.BACKGROUND_HEARTBEAT_INTERVAL_SECONDS
        poll_interval = min(config.CANCEL_POLL_INTERVAL_SECONDS, write_interval)
        next_write = time.monotonic() + write_interval

        while not self._hb_stop.wait(poll_interval):
            try:
                self._poll_cancel()
            except Exception:
                logger.exception(
                    f"[job {self.job_id}] background cancel poll failed"
                )

            if time.monotonic() < next_write:
                continue

            try:
                self._best_effort_progress(heartbeat=True)
                self._last_heartbeat_monotonic = time.monotonic()
            except Exception:
                logger.exception(
                    f"[job {self.job_id}] background heartbeat tick failed"
                )
            next_write = time.monotonic() + write_interval

    def _poll_cancel(self) -> None:
        if self._cancel_event.is_set():
            return
        with sqlite_conn(GRAPH_DB) as conn:
            if job_store.is_cancel_requested(conn, self.job_id):
                self._cancel_event.set()

    def _should_write_heartbeat(self, *, force: bool) -> bool:
        """Return whether a heartbeat should be written."""
        if force:
            return True

        return (
            time.monotonic()
            - self._last_heartbeat_monotonic
            >= config.HEARTBEAT_MIN_GAP_SECONDS
        )

    def _best_effort_progress(self, **kwargs: Any) -> None:
        """Write progress updates with best-effort semantics."""
        try:
            with sqlite_conn(GRAPH_DB) as conn:
                job_store.update_progress(conn, self.job_id, **kwargs)
        except sqlite3.OperationalError as exc:
            logger.warning(
                f"[job {self.job_id}] progress write dropped: {exc}"
            )

    # -------------------------------------------------- public checkpoint API
    def set_stage(
        self,
        stage: JobStage,
        *,
        status_message: Optional[str] = None,
    ) -> None:
        """Update the current job stage."""
        self._stage = stage

        self._best_effort_progress(
            stage=stage,
            done_units=0,
            total_units=0,
            status_message=status_message,
            heartbeat=True,
        )
        self._last_heartbeat_monotonic = time.monotonic()

    def checkpoint(
        self,
        *,
        done_units: Optional[int] = None,
        total_units: Optional[int] = None,
        status_message: Optional[str] = None,
        progress_details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Run cancel, timeout, and heartbeat checks."""
        if self._cancel_event.is_set():
            raise JobCancelled(self.job_id)

        with sqlite_conn(GRAPH_DB) as conn:
            if job_store.is_cancel_requested(conn, self.job_id):
                self._cancel_event.set()
                raise JobCancelled(self.job_id)

        if self.elapsed_seconds() > config.MAX_JOB_DURATION_SECONDS:
            raise JobTimeout(self.job_id)

        if not self._should_write_heartbeat(force=False):
            return

        self._best_effort_progress(
            stage=self._stage,
            done_units=done_units,
            total_units=total_units,
            status_message=status_message,
            progress_details=progress_details,
            heartbeat=True,
        )
        self._last_heartbeat_monotonic = time.monotonic()
