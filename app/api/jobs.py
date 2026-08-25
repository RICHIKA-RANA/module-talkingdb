from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from fastapi.concurrency import run_in_threadpool

from talkingdb.clients.sqlite import sqlite_conn, GRAPH_DB
from talkingdb.helpers.auth import verify_api_key
from talkingdb.helpers.job import store as job_store
from talkingdb.models.api.response import ErrorResponse
from talkingdb.models.job.job import JobModel

from app.model.jobs import JobStatusResponse
from app.services import jobs as jobs_service


router = APIRouter(prefix="/v1", tags=["Jobs"])


def _no_store(response: Response) -> None:
    """Prevent proxies / browsers / gateways from caching job status."""
    response.headers["Cache-Control"] = "no-store"


def _job_or_404(job_id: str) -> JobModel:
    """Return a persisted job or raise HTTP 404."""
    with sqlite_conn(GRAPH_DB) as conn:
        job = job_store.get(conn, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "JOB_NOT_FOUND",
                "message": f"Unknown job id: {job_id}",
            },
        )
    return job


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Get current status of a job",
    description=(
        "Return the current lifecycle state and progress of a job. "
        "The ``job_type`` field tells the caller what kind of background "
        "operation this job represents."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        404: {"model": ErrorResponse, "description": "Unknown job id"},
    },
)
async def get_job_status(
    response: Response,
    job_id: str = Path(..., description="Stable job identifier"),
    api_key: str = Depends(verify_api_key),
) -> JobStatusResponse:
    """Fetch the latest persisted state for a job."""
    _no_store(response)
    job = _job_or_404(job_id)
    return JobStatusResponse(**job.to_status_payload())


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request cancellation of a job",
    description=(
        "Request cooperative cancellation of a queued or running job. "
        "Idempotent: cancelling a terminal job echoes the same terminal state."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        404: {"model": ErrorResponse, "description": "Unknown job id"},
    },
)
async def cancel_job(
    response: Response,
    job_id: str = Path(..., description="Stable job identifier"),
    api_key: str = Depends(verify_api_key),
) -> JobStatusResponse:
    """Request cancellation for a queued or running job."""
    _no_store(response)
    _job_or_404(job_id)
    with sqlite_conn(GRAPH_DB) as conn:
        updated = job_store.request_cancel(conn, job_id)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="vanished"
        )
    return JobStatusResponse(**updated.to_status_payload())


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed job from its last checkpoint",
    description=(
        "Resume a FAILED job without re-uploading the file. "
        "Only eligible for retryable parse failures (transient timeout/crash), not content issues. "
        "The uploaded file must still be available and within the retry limit. "
        "Otherwise, returns 409; re-upload the file."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        404: {"model": ErrorResponse, "description": "Unknown job id"},
        409: {"model": ErrorResponse, "description": "Job is not eligible for retry"},
        429: {"model": ErrorResponse, "description": "Worker queue is at capacity"},
        500: {"model": ErrorResponse, "description": "Retry failed unexpectedly (e.g. storage unreachable)"},
    },
)
async def retry_job(
    response: Response,
    job_id: str = Path(..., description="Stable job identifier"),
    api_key: str = Depends(verify_api_key),
) -> JobStatusResponse:
    _no_store(response)
    try:
        updated = await run_in_threadpool(jobs_service.retry_job, job_id)
    except jobs_service.JobNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "JOB_NOT_FOUND",
                "message": f"Unknown job id: {job_id}",
            },
        )
    except jobs_service.JobNotRetryable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "JOB_NOT_RETRYABLE", "message": exc.reason},
        )
    except jobs_service.QueueFull:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error_code": "QUEUE_FULL",
                "message": "Ingestion worker pool is at capacity",
            },
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "RETRY_FAILED",
                "message": "Retry failed unexpectedly. Please try again.",
            },
        )
    return JobStatusResponse(**updated.to_status_payload())
