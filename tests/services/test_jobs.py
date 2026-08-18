"""Unit tests for the testable, non-I/O pieces of app.services.jobs.

``run_job``/``_finalize``/``_parse`` orchestrate CEClient, the indexer, the
graph store, MinIO, and rollback in one long pipeline - covering them
meaningfully would mean re-mocking most of that stack for little benefit
over an integration/E2E test. This file instead covers the pieces that
carry real branching logic on their own: admission control, exception
classification, and the small pure summary helpers.
"""

import datetime
import sqlite3
from datetime import timezone

import pytest

from talkingdb.models.document.document import DocumentModel
from talkingdb.models.document.elements.primitive.paragraph import ParagraphModel
from talkingdb.models.document.elements.primitive.table import TableModel
from talkingdb.models.document.layouts.layout import LayoutModel
from talkingdb.models.job.error import JobErrorCode

from app.core import config
from app.services import jobs


@pytest.fixture(autouse=True)
def _reset_admission_state():
    """``jobs._in_flight`` is process-global; isolate it across tests."""
    jobs._in_flight = 0
    yield
    jobs._in_flight = 0


class TestAdmissionControl:
    def test_acquire_slot_increments_in_flight(self):
        jobs.acquire_slot()
        assert jobs._in_flight == 1

    def test_acquire_slot_raises_queue_full_at_capacity(self, monkeypatch):
        monkeypatch.setattr(config, "QUEUE_CAPACITY", 1)
        jobs.acquire_slot()

        with pytest.raises(jobs.QueueFull):
            jobs.acquire_slot()
        assert jobs._in_flight == 1

    def test_release_slot_decrements_in_flight(self):
        jobs.acquire_slot()
        jobs.release_slot()
        assert jobs._in_flight == 0

    def test_release_slot_floors_at_zero(self):
        jobs.release_slot()
        jobs.release_slot()
        assert jobs._in_flight == 0

    def test_run_after_reservation_releases_slot_on_success(self, monkeypatch):
        jobs.acquire_slot()
        monkeypatch.setattr(jobs, "run_job", lambda *a, **k: None)

        jobs._run_after_reservation("job-1", "/tmp/x", "f.pdf", "{}")

        assert jobs._in_flight == 0

    def test_run_after_reservation_releases_slot_on_exception(self, monkeypatch):
        jobs.acquire_slot()

        def _boom(*a, **k):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(jobs, "run_job", _boom)

        with pytest.raises(RuntimeError):
            jobs._run_after_reservation("job-1", "/tmp/x", "f.pdf", "{}")

        assert jobs._in_flight == 0

    def test_enqueue_reserved_submits_run_after_reservation(self, monkeypatch):
        captured = {}

        def _fake_submit(fn, *args):
            captured["fn"] = fn
            captured["args"] = args

            class _ImmediateFuture:
                def result(self):
                    return fn(*args)

            return _ImmediateFuture()

        monkeypatch.setattr(jobs._executor, "submit", _fake_submit)
        monkeypatch.setattr(jobs, "run_job", lambda *a, **k: None)

        jobs.enqueue_reserved(
            job_id="job-1", temp_path="/tmp/x", filename="f.pdf", metadata_json="{}"
        )

        assert captured["fn"] is jobs._run_after_reservation
        assert captured["args"] == ("job-1", "/tmp/x", "f.pdf", "{}")


class TestNowIso:
    def test_returns_parseable_utc_iso_timestamp(self):
        stamp = jobs._now_iso()
        parsed = datetime.datetime.fromisoformat(stamp)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == datetime.timedelta(0)


class TestClassify:
    def test_value_error_is_validation_error(self):
        code, detail = jobs._classify(ValueError("bad input"))
        assert code == JobErrorCode.VALIDATION_ERROR
        assert "bad input" in detail

    def test_sqlite_operational_error_is_persist_error(self):
        code, detail = jobs._classify(sqlite3.OperationalError("locked"))
        assert code == JobErrorCode.PERSIST_ERROR

    def test_unrecognized_exception_is_internal_error(self):
        code, detail = jobs._classify(RuntimeError("plain failure"))
        assert code == JobErrorCode.INTERNAL_ERROR
        assert "RuntimeError" in detail
        assert "plain failure" in detail

    def test_exception_with_no_message_uses_class_name_only(self):
        code, detail = jobs._classify(RuntimeError())
        assert detail == "RuntimeError"

    def test_exception_from_a_reader_module_is_parse_error(self):
        class DocxReaderError(Exception):
            pass

        # Simulate the exception type living in a "...reader..." module,
        # the same way a real docx/talkingdb_ce parser error would.
        DocxReaderError.__module__ = "talkingdb_ce.reader.docx"

        code, detail = jobs._classify(DocxReaderError("malformed docx"))
        assert code == JobErrorCode.PARSE_ERROR


def _document_with(*, pages, table_pages=()):
    elements = [ParagraphModel(page=p) for p in pages]
    elements += [TableModel(page=p) for p in table_pages]
    layout = LayoutModel(orientation="portrait", elements=elements)
    return DocumentModel(layouts=[layout])


class TestBuildResultSummary:
    def test_counts_elements_and_tables_and_includes_duration(self):
        document = _document_with(pages=[1, 2], table_pages=[3])
        ctx = jobs.JobContext(job_id="job-1")

        summary = jobs._build_result_summary(document, ctx)

        assert summary["elements"] == 3
        assert summary["tables"] == 1
        assert isinstance(summary["duration_ms"], int)
        assert summary["duration_ms"] >= 0

    def test_document_with_no_elements_reports_zero(self):
        document = DocumentModel(layouts=[])
        ctx = jobs.JobContext(job_id="job-1")

        summary = jobs._build_result_summary(document, ctx)

        assert summary == {"elements": 0, "tables": 0, "duration_ms": summary["duration_ms"]}
        assert summary["elements"] == 0
        assert summary["tables"] == 0


class TestPageCount:
    def test_returns_max_page_across_elements(self):
        document = _document_with(pages=[1, 5, 3])
        assert jobs._page_count(document) == 5

    def test_ignores_elements_with_no_page(self):
        document = _document_with(pages=[None, 2, None])
        assert jobs._page_count(document) == 2

    def test_returns_none_when_no_element_has_a_page(self):
        document = _document_with(pages=[None, None])
        assert jobs._page_count(document) is None

    def test_returns_none_for_empty_document(self):
        document = DocumentModel(layouts=[])
        assert jobs._page_count(document) is None
