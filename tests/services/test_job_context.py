"""Unit tests for app.services.job_context.JobContext."""

import time

import pytest

import talkingdb.clients.sqlite as sqlite_client
from talkingdb.helpers.job import store as job_store
from talkingdb.models.job.job import JobModel
from talkingdb.models.job.stage import JobStage
from talkingdb.models.job.type import JobType

from app.core import config
from app.services.job_context import JobCancelled, JobContext, JobTimeout


def _insert_job(db_path: str, job_id: str = "job-1") -> JobModel:
    job = JobModel.new(job_type=JobType.DOCUMENT)
    job.job_id = job_id
    with sqlite_client.sqlite_conn(db_path) as conn:
        job_store.insert(conn, job)
        job_store.mark_ongoing(conn, job_id, job.created_at)
    return job


class TestElapsedSeconds:
    def test_elapsed_seconds_is_non_negative_and_increases(self):
        ctx = JobContext(job_id="job-1")
        first = ctx.elapsed_seconds()
        time.sleep(0.01)
        second = ctx.elapsed_seconds()
        assert first >= 0
        assert second > first


class TestSetStage:
    def test_set_stage_persists_stage_and_status_message(self, initialized_db):
        _insert_job(initialized_db)
        ctx = JobContext(job_id="job-1")

        ctx.set_stage(JobStage.PARSING, status_message="Parsing document")

        with sqlite_client.sqlite_conn(initialized_db) as conn:
            job = job_store.get(conn, "job-1")
        assert job.stage == JobStage.PARSING
        assert job.status_message == "Parsing document"
        assert ctx._stage == JobStage.PARSING


class TestCheckpoint:
    def test_checkpoint_raises_job_cancelled_when_cancel_requested(
        self, initialized_db
    ):
        _insert_job(initialized_db)
        with sqlite_client.sqlite_conn(initialized_db) as conn:
            job_store.request_cancel(conn, "job-1")

        ctx = JobContext(job_id="job-1")

        with pytest.raises(JobCancelled):
            ctx.checkpoint(status_message="still going")

    def test_checkpoint_raises_job_timeout_when_over_duration(
        self, initialized_db, monkeypatch
    ):
        _insert_job(initialized_db)
        monkeypatch.setattr(config, "MAX_JOB_DURATION_SECONDS", 0)
        ctx = JobContext(job_id="job-1")

        with pytest.raises(JobTimeout):
            ctx.checkpoint(status_message="too slow")

    def test_checkpoint_skips_write_within_min_heartbeat_gap(
        self, initialized_db, monkeypatch
    ):
        _insert_job(initialized_db)
        monkeypatch.setattr(config, "HEARTBEAT_MIN_GAP_SECONDS", 999)
        ctx = JobContext(job_id="job-1")
        ctx.set_stage(JobStage.PARSING, status_message="first")

        # Immediately within the heartbeat gap: checkpoint must not overwrite
        # the status message written by set_stage above.
        ctx.checkpoint(status_message="second, should be dropped")

        with sqlite_client.sqlite_conn(initialized_db) as conn:
            job = job_store.get(conn, "job-1")
        assert job.status_message == "first"

    def test_checkpoint_writes_when_gap_elapsed(self, initialized_db, monkeypatch):
        _insert_job(initialized_db)
        monkeypatch.setattr(config, "HEARTBEAT_MIN_GAP_SECONDS", 0)
        ctx = JobContext(job_id="job-1")
        ctx.set_stage(JobStage.PARSING, status_message="first")

        ctx.checkpoint(
            done_units=3, total_units=10, status_message="now with progress"
        )

        with sqlite_client.sqlite_conn(initialized_db) as conn:
            job = job_store.get(conn, "job-1")
        assert job.status_message == "now with progress"
        assert job.done_units == 3
        assert job.total_units == 10


class TestBackgroundHeartbeat:
    def test_start_stop_is_idempotent_and_joins_thread(self, initialized_db):
        _insert_job(initialized_db)
        ctx = JobContext(job_id="job-1")

        ctx.start_background_heartbeat()
        first_thread = ctx._hb_thread
        assert first_thread is not None
        assert first_thread.is_alive()

        # Calling start again while already running is a no-op.
        ctx.start_background_heartbeat()
        assert ctx._hb_thread is first_thread

        ctx.stop_background_heartbeat()
        assert ctx._hb_thread is None
        assert not first_thread.is_alive()

        # Idempotent: stopping again must not raise.
        ctx.stop_background_heartbeat()

    def test_background_heartbeat_writes_progress_periodically(
        self, initialized_db, monkeypatch
    ):
        monkeypatch.setattr(config, "BACKGROUND_HEARTBEAT_INTERVAL_SECONDS", 0.02)
        _insert_job(initialized_db)
        ctx = JobContext(job_id="job-1")

        ctx.start_background_heartbeat()
        try:
            deadline = time.monotonic() + 2
            heartbeat_seen = None
            while time.monotonic() < deadline:
                with sqlite_client.sqlite_conn(initialized_db) as conn:
                    job = job_store.get(conn, "job-1")
                if job.heartbeat_at is not None:
                    heartbeat_seen = job.heartbeat_at
                    break
                time.sleep(0.02)
        finally:
            ctx.stop_background_heartbeat()

        assert heartbeat_seen is not None
