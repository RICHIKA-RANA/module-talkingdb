"""API response models for ingestion jobs.

Only the fields listed here are part of the stable public contract; the
``progress_details`` column and internal job-table columns are deliberately
absent so consumers cannot couple to non-contractual surface.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class JobAcceptedResponse(BaseModel):
    """Response for accepted ingestion jobs."""

    job_id: str = Field(..., description="Stable job id")
    job_type: str = Field(..., description="Kind of background operation (e.g. 'document')")
    session_id: Optional[str] = Field(
        None, description="Session this document is linked to"
    )
    state: str = Field(..., description="Current job state")


class JobStatusResponse(BaseModel):
    """Response for job status polling."""

    job_id: str
    job_type: str = Field(
        ...,
        description="Kind of background operation (e.g. 'document')",
    )
    session_id: Optional[str] = Field(
        None, description="Session this document is linked to"
    )
    project_id: Optional[str] = Field(
        None,
        description=(
            "Project this document is filed under. Null means it sits at the "
            "owner's root level, outside any project."
        ),
    )
    state: str = Field(
        ...,
        description="QUEUED | ONGOING | CANCELLING | CANCELLED | COMPLETED | FAILED",
    )
    stage: Optional[str] = Field(
        None,
        description=(
            "VALIDATING | PARSING | ELEMENT_EXTRACTION | TREE_GENERATION | "
            "INDEXING | PERSISTING. Null on any terminal state."
        ),
    )
    progress: int = Field(
        None,
        description="0-100 once the work size is known, Reset to 0 on terminal state",
    )
    progress_at: Optional[str] = Field(
        None,
        description=(
            "Timestamp of the last real progress signal-not a heartbeat. Compare against wall-clock time client-side "
            "to notice a stalled job; use alongside `state`/`stage` rather "
            "than as a standalone health check."
        ),
    )
    status_message: Optional[str] = Field(
        None, description="Short, human-readable status text for UI display"
    )
    result_graph_id: Optional[str] = Field(
        None, description="Set only when state is COMPLETED"
    )
    file_name: Optional[str] = Field(
    None, description="Original uploaded file name"
    )
    file_size: Optional[int] = Field(
    None, description="Original uploaded file Size"
    )
    page_count: Optional[int] = Field(
        None,
        description=(
            "Document page count. Null until COMPLETED or if pagination is unavailable."
        ),
    )
    result_summary: Optional[Dict[str, Any]] = Field(
        None,
        description="Terminal-only summary (elements/tables/duration_ms)",
    )
    error_code: Optional[str] = Field(
        None,
        description=(
            "VALIDATION_ERROR | PARSE_ERROR | INDEX_ERROR | PERSIST_ERROR | "
            "TIMEOUT | STUCK | INTERNAL_ERROR. Set only on FAILED."
        ),
    )
    error_message: Optional[str] = None
