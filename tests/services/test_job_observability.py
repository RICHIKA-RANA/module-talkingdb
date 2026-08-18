"""Unit tests for app.services.job_observability."""

import json

import pytest

from talkingdb.models.job.error import JobErrorCode
from talkingdb.models.job.job import JobModel
from talkingdb.models.job.stage import JobStage
from talkingdb.models.job.state import JobState
from talkingdb.models.job.type import JobType

from app.services import job_observability
from app.services.job_observability import _diff_ms, emit_lifecycle


class TestDiffMs:
    def test_returns_none_when_start_missing(self):
        assert _diff_ms(None, "2026-01-01T00:00:01+00:00") is None

    def test_returns_none_when_end_missing(self):
        assert _diff_ms("2026-01-01T00:00:00+00:00", None) is None

    def test_returns_none_for_unparseable_timestamp(self):
        assert _diff_ms("not-a-timestamp", "2026-01-01T00:00:01+00:00") is None

    def test_computes_millisecond_delta(self):
        start = "2026-01-01T00:00:00+00:00"
        end = "2026-01-01T00:00:01.500000+00:00"
        assert _diff_ms(start, end) == 1500


def _make_job(**overrides) -> JobModel:
    job = JobModel.new(job_type=JobType.DOCUMENT, filename="report.pdf")
    job.job_id = "job-1"
    job.created_at = "2026-01-01T00:00:00+00:00"
    job.started_at = "2026-01-01T00:00:01+00:00"
    job.completed_at = "2026-01-01T00:00:03+00:00"
    job.state = JobState.COMPLETED
    job.stage = JobStage.PERSISTING
    job.file_size_bytes = 4096
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


class TestEmitLifecycle:
    def test_logs_structured_json_record(self, monkeypatch):
        records = []
        monkeypatch.setattr(job_observability.logger, "info", records.append)

        job = _make_job()
        emit_lifecycle(job, rollback_ms=250)

        assert len(records) == 1
        record = json.loads(records[0])
        assert record["event"] == "job.lifecycle"
        assert record["job_id"] == "job-1"
        assert record["job_type"] == "document"
        assert record["state"] == "COMPLETED"
        assert record["stage"] == "PERSISTING"
        assert record["file_size_bytes"] == 4096
        assert record["queue_wait_ms"] == 1000
        assert record["processing_ms"] == 2000
        assert record["rollback_ms"] == 250
        assert record["error_code"] is None
        assert record["filename"] == "report.pdf"

    def test_defaults_rollback_ms_to_none(self, monkeypatch):
        records = []
        monkeypatch.setattr(job_observability.logger, "info", records.append)

        emit_lifecycle(_make_job())

        record = json.loads(records[0])
        assert record["rollback_ms"] is None

    def test_includes_error_code_when_failed(self, monkeypatch):
        records = []
        monkeypatch.setattr(job_observability.logger, "info", records.append)

        job = _make_job(
            state=JobState.FAILED, error_code=JobErrorCode.PARSE_ERROR
        )
        emit_lifecycle(job)

        record = json.loads(records[0])
        assert record["state"] == "FAILED"
        assert record["error_code"] == "PARSE_ERROR"

    def test_stage_is_none_when_job_has_no_stage(self, monkeypatch):
        records = []
        monkeypatch.setattr(job_observability.logger, "info", records.append)

        emit_lifecycle(_make_job(stage=None))

        record = json.loads(records[0])
        assert record["stage"] is None
