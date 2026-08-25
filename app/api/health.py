"""Service health endpoint.

Reports the health of module-ttt itself plus its dependencies:

  * SQLite is always required.
  * MinIO is an optional plugin - skipped when not configured - but once
    MINIO_ACCESS_KEY/SECRET_KEY are set, it's load-bearing for document
    storage, so a configured-but-unreachable MinIO is treated the same as
    Minio being down.
  * The LLM provider configured-but-broken there
    is reported as "degraded" without failing the whole health check.
  * Disk space is calculated, if below talkingdb.helpers.spool.assert_spool_capacity() then failed
"""

import os
import time

import httpx
import shutil
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from talkingdb.clients.sqlite import sqlite_conn, GRAPH_DB
from talkingdb.clients.minio import get_minio_client, MINIO_BUCKET
from talkingdb.clients.minio import is_configured as minio_is_configured
from talkingdb.helpers.client import config as ce_config
from talkingdb.models.api.mode import ClientMode
from talkingdb.logger.console import logger
from talkingdb.helpers.spool import SPOOL_DIR, MIN_FREE_SPOOL_MB


from app.core import llm

router = APIRouter(tags=["Health"])


def _check_sqlite() -> dict:
    """Required. The graph DB is on local disk, so this mainly catches disk
    issues (permissions, corruption, out of space) rather than network faults.
    """
    try:
        with sqlite_conn(GRAPH_DB) as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except Exception as e:
        logger.error("Health check: sqlite failed: %s", e)
        return {"status": "error", "detail": str(e)}


def _check_minio() -> dict:
    """MinIO is an optional plugin (talkingdb.clients.minio.is_configured()). But once
    it IS configured, document storage depends on it, so an unreachable
    MinIO in that state is treated as critical (503).
    """
    if not minio_is_configured():
        return {"status": "not_configured"}

    start = time.monotonic()
    try:
        client = get_minio_client()
        exists = client.bucket_exists(MINIO_BUCKET)
        latency_ms = int((time.monotonic() - start) * 1000)
        if not exists:
            return {
                "status": "error",
                "detail": f"Bucket '{MINIO_BUCKET}' does not exist",
                "latency_ms": latency_ms,
            }
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as e:
        logger.error("Health check: minio failed: %s", e)
        return {"status": "error", "detail": str(e)}


def _check_llm() -> dict:
    """Optional - only present when LLM_PROVIDER is set.
    """
    provider = os.getenv("LLM_PROVIDER")
    if not provider:
        return {"status": "not_configured"}

    if llm.is_configured():
        return {"status": "ok", "provider": provider}

    return {
        "status": "degraded",
        "provider": provider,
        "detail": (
            f"LLM_PROVIDER={provider!r} is set but not fully configured "
            "(missing API key or base URL)"
        ),
    }


def _check_disk_space() -> dict:
    """Required. Mirrors assert_spool_capacity()'s
    threshold.
    """
    try:
        os.makedirs(SPOOL_DIR, exist_ok=True)
        usage = shutil.disk_usage(SPOOL_DIR)
        free_mb = usage.free // (1024 * 1024)
        if free_mb < MIN_FREE_SPOOL_MB:
            return {
                "status": "error",
                "detail": (
                    f"Only {free_mb}MB free on '{SPOOL_DIR}' "
                    f"(minimum required: {MIN_FREE_SPOOL_MB}MB)"
                ),
                "free_mb": free_mb,
            }
        return {"status": "ok", "free_mb": free_mb}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.get(
    "/health",
    summary="Service health check",
    description=(
        "Reports the health of this service and its dependencies."
    ),
)
@router.head("/health")
def health():
    checks = {
        "sqlite": _check_sqlite(),
        "minio": _check_minio(),
        "llm": _check_llm(),
        "disk_space": _check_disk_space()
    }

    critical_failed = any(
        checks[name]["status"] == "error" for name in ("sqlite", "minio", "disk_space")
    )
    any_degraded = any(check["status"] ==
                       "degraded" for check in checks.values())

    if critical_failed:
        overall, status_code = "unhealthy", 503
    elif any_degraded:
        overall, status_code = "degraded", 200
    else:
        overall, status_code = "ok", 200

    return JSONResponse(
        status_code=status_code,
        content={"status": overall, "checks": checks},
    )
