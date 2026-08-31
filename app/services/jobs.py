"""Async document-ingestion runtime."""

import asyncio
import os
import shutil
import sqlite3
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from starlette.datastructures import UploadFile

from talkingdb.clients.sqlite import sqlite_conn, GRAPH_DB
from talkingdb.helpers import spool
from talkingdb.helpers.client import config as ce_config
from talkingdb.helpers.graph import rollback_graph
from talkingdb.logger.console import logger
from talkingdb.models.document.document import DocumentModel
from talkingdb.models.document.elements.primitive.table import TableModel
from talkingdb.models.document.indexes.index import FileIndexModel
from talkingdb.helpers.file_graph import store as file_graph_store
from talkingdb.helpers.job import store as job_store
from talkingdb.models.failure import messages as failures
from talkingdb.models.failure.failure import DocumentFailure
from talkingdb.models.failure.reason import FailureReason
from talkingdb.models.job.error import JobErrorCode
from talkingdb.models.job.job import JobModel
from talkingdb.models.job.stage import JobStage
from talkingdb.models.job.state import JobState
from talkingdb.models.metadata.metadata import DEFAULT_METADATA, Metadata
from talkingdb_ce.client import CEClient
from talkingdb_ce.services.reader.killable_subprocess import ReadCancelled
from talkingdb.helpers import file_store

from app.core import config
from app.services.job_context import JobCancelled, JobContext, JobTimeout
from app.services.job_observability import emit_lifecycle


# ----------------------------------------------------------------- admission
class QueueFull(Exception):
    """Raised when the bounded admission queue is full."""


class JobNotFound(Exception):
    """Raised by :func:`retry_job` when the job id doesn't exist."""


class JobNotRetryable(Exception):
    """Raised by :func:`retry_job` when the job can't be retried right now.

    Carries a human-readable ``reason`` for the API layer to surface.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_executor = ThreadPoolExecutor(
    max_workers=config.MAX_WORKERS,
    thread_name_prefix="tdb-job",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def acquire_slot() -> None:
    """Reserve one bounded admission slot."""
    with sqlite_conn(GRAPH_DB) as conn:
        if not job_store.acquire_admission_slot(conn, config.QUEUE_CAPACITY):
            raise QueueFull()


def release_slot() -> None:
    """Release a slot previously held by :func:`acquire_slot`.`"""
    with sqlite_conn(GRAPH_DB) as conn:
        job_store.release_admission_slot(conn)


def enqueue_reserved(
    *,
    job_id: str,
    temp_path: str,
    filename: str,
    metadata_json: str,
) -> None:
    """Submit work whose slot has already been reserved.

    The wrapper guarantees the slot is released exactly once, regardless of
    how the job ends.
    """
    _executor.submit(
        _run_after_reservation, job_id, temp_path, filename, metadata_json
    )


def _run_after_reservation(
    job_id: str, temp_path: str, filename: str, metadata_json: str
) -> None:
    """Run a reserved job and always release its slot."""
    try:
        run_job(job_id, temp_path, filename, metadata_json)
    finally:
        release_slot()


# -------------------------------------------------------- parse checkpoints
def parse_checkpoint_dir(job_id: str) -> str:
    """Deterministic per-job checkpoint dir for resumable PDF parsing."""
    return os.path.join(config.PARSE_CHECKPOINT_ROOT, job_id)


def discard_parse_checkpoint(checkpoint_dir: str) -> None:
    try:
        shutil.rmtree(checkpoint_dir, ignore_errors=True)
    except OSError:
        logger.warning(f"failed to remove parse checkpoint dir: {checkpoint_dir}")


# ---------------------------------------------------------------------- run
def run_job(
    job_id: str, temp_path: str, filename: str, metadata_json: str
) -> None:
    """Execute one ingestion job to a terminal state."""
    if not _transition_to_ongoing(job_id):
        spool.discard(temp_path)
        return

    ctx = JobContext(job_id=job_id)
    ctx.start_background_heartbeat()
    graph_id: Optional[str] = None
    result_summary = None
    checkpoint_dir = parse_checkpoint_dir(job_id)
    parse_failed_retryably = False

    try:
        ctx.set_stage(JobStage.PARSING, status_message="Parsing document")
        try:
            try:
                parse_result = _parse(
                    temp_path, filename, metadata_json,
                    cancel_check=ctx.cancel_or_timeout_requested,
                    checkpoint_dir=checkpoint_dir,
                )
            except ReadCancelled:
                if ctx.is_timed_out():
                    parse_failed_retryably = True
                    raise JobTimeout(job_id)
                raise JobCancelled(job_id)
            except DocumentFailure:
                raise
            except BaseException:
                parse_failed_retryably = True
                raise
        finally:
            if not parse_failed_retryably:
                discard_parse_checkpoint(checkpoint_dir)

        ctx.checkpoint(status_message="Parsed; preparing to index")

        ctx.set_stage(
            JobStage.ELEMENT_EXTRACTION,
            status_message="Reading document structure",
        )
        from app.services.indexer import IndexerService

        indexer = IndexerService()
        graph_id = indexer.gm.graph_id

        with sqlite_conn(GRAPH_DB) as conn:
            job_store.set_result_graph_id(conn, job_id, graph_id)
            file_graph_store.set_graph_id(conn, job_id, graph_id)  

        ctx.set_stage(
            JobStage.TREE_GENERATION,
            status_message="Building document tree",
        )

        def _on_tree_progress(done: int, total: int) -> None:
            """Forward progress updates through the job context."""
            ctx.checkpoint(
                done_units=done,
                total_units=total,
                status_message=f"Building document tree ({done}/{total})",
            )

        indexer.graph_file_index(
            FileIndexModel(**parse_result["file_index"]),
            progress=_on_tree_progress,
        )

        ctx.set_stage(JobStage.INDEXING,
                      status_message="Indexing document elements")
        document = DocumentModel.from_dict(parse_result["document"])

        def _on_progress(done: int, total: int) -> None:
            """Forward progress updates through the job context."""
            ctx.checkpoint(
                done_units=done,
                total_units=total,
                status_message=f"Indexing elements ({done}/{total})",
            )

        indexer.index_document(
            document, progress=_on_progress, cancel_check=ctx.is_cancelled
        )

        ctx.set_stage(JobStage.PERSISTING, status_message="Saving graph")
        ctx.checkpoint(status_message="Saving graph")

        result_summary = _build_result_summary(document, ctx)

        _finalize(
            job_id,
            JobState.COMPLETED,
            graph_id=graph_id,
            temp_path=temp_path,
            result_summary=result_summary,
            page_count=_page_count(document),
            status_message="Document indexed",
        )

    except JobCancelled:
        _finalize(
            job_id,
            JobState.CANCELLED,
            graph_id=graph_id,
            temp_path=temp_path,
            status_message="Upload cancelled, cleaned up",
        )
    except JobTimeout:
        logger.warning(
            f"[job {job_id}] exceeded MAX_JOB_DURATION_SECONDS="
            f"{config.MAX_JOB_DURATION_SECONDS}"
        )
        _finalize(
            job_id,
            JobState.FAILED,
            graph_id=graph_id,
            temp_path=temp_path,
            error_code=JobErrorCode.TIMEOUT,
            error_message=failures.GENERIC_MESSAGE,
            failure_reason=FailureReason.PROCESSING_FAILED,
            status_message="Upload timed out",
            retryable=parse_failed_retryably,
        )
    except BaseException as exc:
        reason, error_code, detail = _classify(exc)
        logger.exception(
            f"[job {job_id}] failed: {error_code.value}/{reason.value}: {detail}"
        )
        _finalize(
            job_id,
            JobState.FAILED,
            graph_id=graph_id,
            temp_path=temp_path,
            error_code=error_code,
            error_message=failures.message_for(reason),
            failure_reason=reason,
            status_message="Upload failed",
            retryable=parse_failed_retryably,
        )
    finally:
        ctx.stop_background_heartbeat()


# ------------------------------------------------------------- pipeline steps
def _transition_to_ongoing(job_id: str) -> bool:
    with sqlite_conn(GRAPH_DB) as conn:
        return job_store.mark_ongoing(conn, job_id, _now_iso())


def _parse(
    temp_path: str,
    filename: str,
    metadata_json: str,
    cancel_check: Optional[Callable[[], bool]] = None,
    checkpoint_dir: Optional[str] = None,
) -> dict:
    """Parse a spooled document using CEClient."""
    metadata = Metadata.ensure_metadata(Metadata.from_json(metadata_json))
    client = CEClient(ce_config)
    with open(temp_path, "rb") as fh:
        upload = UploadFile(filename=filename, file=fh)
        return asyncio.run(
            client.parse_file(
                file=upload, metadata=metadata, cancel_check=cancel_check,
                checkpoint_dir=checkpoint_dir,
            )
        )


def _build_result_summary(document: DocumentModel, ctx: JobContext) -> dict:
    elements_total = 0
    tables = 0
    for element in document.iter_elements():
        elements_total += 1
        if isinstance(element, TableModel):
            tables += 1
    return {
        "elements": elements_total,
        "tables": tables,
        "duration_ms": int(ctx.elapsed_seconds() * 1000),
    }


def _page_count(document: DocumentModel) -> Optional[int]:
    pages = [
        element.page
        for element in document.iter_elements()
        if getattr(element, "page", None)
    ]
    return max(pages) if pages else None


# ------------------------------------------------------------- classification
def _classify(
    exc: BaseException,
) -> Tuple[FailureReason, JobErrorCode, str]:
    """Resolve a failure into (reason, coarse code, internal detail)."""
    name = type(exc).__name__
    detail = f"{name}: {exc}" if str(exc) else name

    if isinstance(exc, DocumentFailure):
        return (
            exc.reason,
            failures.error_code_for(exc.reason),
            exc.detail or detail,
        )

    if isinstance(exc, sqlite3.OperationalError):
        return FailureReason.PROCESSING_FAILED, JobErrorCode.PERSIST_ERROR, detail

    return FailureReason.PROCESSING_FAILED, JobErrorCode.INTERNAL_ERROR, detail


# ------------------------------------------------------------------ finalize
def _finalize(
    job_id: str,
    terminal_state: JobState,
    *,
    graph_id: Optional[str],
    temp_path: Optional[str],
    result_summary: Optional[dict] = None,
    page_count: Optional[int] = None,
    error_code: Optional[JobErrorCode] = None,
    error_message: Optional[str] = None,
    failure_reason: Optional[FailureReason] = None,
    status_message: Optional[str] = None,
    retryable: bool = False,
) -> None:
    """Finalize the job and clean up based on its actual terminal state.

    Cancellation may override ``COMPLETED`` or ``FAILED``.

    ``retryable=True`` means the FAILED job can be resumed via
    ``POST /v1/jobs/{id}/retry`` from its parsing checkpoint, so the uploaded
    blob, dedup mapping row, and parse checkpoint are preserved instead of rolled
    back. If cancellation wins the race (``applied_state`` is ``CANCELLED``),
    rollback always applies. The local ``temp_path`` is discarded in either case;
    retry re-fetches the original bytes from the preserved blob.
    """
    with sqlite_conn(GRAPH_DB) as conn:
        applied_state = job_store.finalize(
            conn,
            job_id,
            terminal_state,
            result_graph_id=graph_id if terminal_state == JobState.COMPLETED else None,
            result_summary=result_summary,
            page_count=page_count,
            error_code=error_code,
            error_message=error_message,
            failure_reason=failure_reason,
            status_message=status_message,
        )

    if applied_state is None:
        logger.info(
            f"[job {job_id}] finalize lost the race; "
            f"current row already terminal"
        )
        return

    if applied_state != terminal_state:
        logger.warning(
            f"[job {job_id}] a pending cancel overrode "
            f"{terminal_state.value} -> {applied_state.value}"
        )

    preserve_for_retry = retryable and applied_state == JobState.FAILED

    rollback_ms: Optional[int] = None
    if applied_state != JobState.COMPLETED:
        rollback_start = time.monotonic()
        rollback_graph(graph_id)
        rollback_ms = int((time.monotonic() - rollback_start) * 1000)

        if not preserve_for_retry:
            mapping = None
            remaining = []
            with sqlite_conn(GRAPH_DB) as conn:
                mapping = file_graph_store.get_by_job_id(conn, job_id)
                if mapping is not None:
                    file_graph_store.delete_by_job_id(conn, job_id)
                    remaining = file_graph_store.get_by_channel_hash(
                        conn, mapping.channel, mapping.file_hash
                    )

            if mapping is not None and not remaining:
                file_store.delete_file(mapping.channel, mapping.file_hash)

    spool.discard(temp_path)

    with sqlite_conn(GRAPH_DB) as conn:
        terminal_job = job_store.get(conn, job_id)
    if terminal_job is not None:
        emit_lifecycle(terminal_job, rollback_ms=rollback_ms)


def finalize_externally(
    job_id: str,
    terminal_state: JobState,
    *,
    graph_id: Optional[str],
    temp_path: Optional[str],
    error_code: Optional[JobErrorCode] = None,
    error_message: Optional[str] = None,
    failure_reason: Optional[FailureReason] = None,
    status_message: Optional[str] = None,
    retryable: bool = False,
) -> None:
    """Public entry point the lifecycle daemon calls on orphans / timeouts.

    Identical semantics to the worker's internal finalize - same load-bearing
    ordering, same state-guarded UPDATE. Exposed so the daemon does not have
    to duplicate the cleanup choreography.
    """
    _finalize(
        job_id,
        terminal_state,
        graph_id=graph_id,
        temp_path=temp_path,
        error_code=error_code,
        error_message=error_message,
        failure_reason=failure_reason,
        status_message=status_message,
        retryable=retryable,
    )


# --------------------------------------------------------------------- retry
def _respool_from_minio(
    channel: str, file_hash: str, filename: Optional[str]
) -> Tuple[str, int]:
    """Re-download a previously-uploaded blob to a fresh local temp file."""
    stream = file_store.get_file_stream(channel, file_hash)
    if stream is None:
        raise FileNotFoundError(f"blob missing for retry: {channel}/{file_hash}")

    try:
        os.makedirs(spool.SPOOL_DIR, exist_ok=True)
        _, ext = os.path.splitext(filename or "")
        tmp = tempfile.NamedTemporaryFile(
            prefix="tdb-upload-", suffix=(ext or ".bin"),
            dir=spool.SPOOL_DIR, delete=False,
        )
        size = 0
        try:
            for chunk in stream.stream(1024 * 1024):
                tmp.write(chunk)
                size += len(chunk)
            tmp.flush()
            return tmp.name, size
        except BaseException:
            tmp.close()
            spool.discard(tmp.name)
            raise
        finally:
            if not tmp.closed:
                tmp.close()
    finally:
        stream.close()
        stream.release_conn()


def retry_job(job_id: str) -> JobModel:
    """Retry a FAILED job from its last parsing checkpoint."""
    with sqlite_conn(GRAPH_DB) as conn:
        job = job_store.get(conn, job_id)
    if job is None:
        raise JobNotFound(job_id)
    if job.state != JobState.FAILED:
        raise JobNotRetryable("Only a failed job can be retried")

    with sqlite_conn(GRAPH_DB) as conn:
        mapping = file_graph_store.get_by_job_id(conn, job_id)
    if mapping is None:
        raise JobNotRetryable(
            "This failure isn't resumable - please re-upload the document"
        )

    spool.assert_spool_capacity()
    acquire_slot()
    temp_path: Optional[str] = None
    retried_job: Optional[JobModel] = None
    enqueued = False
    try:
        try:
            temp_path, _size = _respool_from_minio(
                mapping.channel, mapping.file_hash, job.filename
            )
        except FileNotFoundError:
            raise JobNotRetryable(
                "The uploaded file is no longer available - please re-upload"
            )

        with sqlite_conn(GRAPH_DB) as conn:
            retried_job = job_store.reset_for_retry(
                conn, job_id,
                temp_path=temp_path, max_retries=config.MAX_JOB_RETRIES,
            )
        if retried_job is None:
            raise JobNotRetryable(
                f"This job has already been retried the maximum "
                f"{config.MAX_JOB_RETRIES} time(s), or is no longer failed"
            )

        enqueue_reserved(
            job_id=job_id,
            temp_path=temp_path,
            filename=job.filename or "document",
            metadata_json=retried_job.metadata_json or DEFAULT_METADATA,
        )
        enqueued = True
    finally:
        if not enqueued:
            spool.discard(temp_path)
            release_slot()

    return retried_job
