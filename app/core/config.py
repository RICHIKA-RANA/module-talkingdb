"""Configuration for asynchronous document-ingestion jobs."""

import os


def _int(name: str, default: int) -> int:
    """Read an int env var with fallback."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# --------------------------------------------------------------- concurrency
# Small bounded worker pool for CPU-heavy ingestion.
MAX_WORKERS = _int("TDB_JOB_MAX_WORKERS", min(4, os.cpu_count() or 1))

# Max queued jobs before returning HTTP 429.
QUEUE_CAPACITY = _int("TDB_JOB_QUEUE_CAPACITY", 2 * MAX_WORKERS)

# Element-level indexing fan-out per document. Sized so that MAX_WORKERS
# documents indexing concurrently share ~(2 * cpu) threads in total, instead of
# each document spawning its own (2 * cpu) pool (which multiplied to
# MAX_WORKERS * 2 * cpu threads under load). Override with TDB_INDEXER_MAX_WORKERS.
INDEXER_MAX_WORKERS = _int(
    "TDB_INDEXER_MAX_WORKERS",
    max(2, (2 * (os.cpu_count() or 1)) // MAX_WORKERS),
)

# Suggested client retry delay.
RETRY_AFTER_SECONDS = _int("TDB_JOB_RETRY_AFTER_SECONDS", 30)


# ----------------------------------------------------------- checkpoint cadence
# The indexer reports progress / checks for cancellation every Nth element.
# Batching keeps SQLite write pressure low.
CHECKPOINT_BATCH = _int("TDB_JOB_CHECKPOINT_BATCH", 25)

# Minimum spacing (seconds) between progress/heartbeat writes. Progress is
# best-effort and may lag actual work - it is never transactional.
HEARTBEAT_MIN_GAP_SECONDS = _int("TDB_JOB_HEARTBEAT_MIN_GAP_SECONDS", 2)

# How often the heartbeat refreshes cancel_requested. This bounds how stale
# JobContext.is_cancelled() can be, and therefore the cancellation latency
# during work without an explicit checkpoint.
CANCEL_POLL_INTERVAL_SECONDS = _int("TDB_JOB_CANCEL_POLL_INTERVAL_SECONDS", 2)


# -------------------------------------------------------------------- timeouts
# A job whose heartbeat is older than this is considered orphaned (its worker
# died). Short, because a live worker's background timer beats far more
# often than this.
STALE_THRESHOLD_SECONDS = _int("TDB_JOB_STALE_THRESHOLD_SECONDS", 5 * 60)


# A job whose heartbeat is still fresh but whose progress hasn't moved
# STALE_THRESHOLD_SECONDS - this is the "wedged, not dead" case.
STUCK_THRESHOLD_SECONDS = _int("TDB_JOB_STUCK_THRESHOLD_SECONDS", 75 * 60)

# High upper safety limit only. Not tuned to any expected job duration - it
# exists purely to guarantee nothing runs forever, as a backstop behind the
# heartbeat/progress checks above.
MAX_JOB_DURATION_SECONDS = _int("TDB_JOB_MAX_DURATION_SECONDS", 3 * 60 * 60)

# A background timer refreshes the heartbeat at this cadence for as long as a
# job is being processed, independent of worker-driven checkpoints.
BACKGROUND_HEARTBEAT_INTERVAL_SECONDS = _int(
    "TDB_JOB_BACKGROUND_HEARTBEAT_INTERVAL_SECONDS",
    max(5, STALE_THRESHOLD_SECONDS // 6),
)


# ------------------------------------------------------------------ retention
# How long terminal jobs are kept before the daily purge removes them (and any
# leftover temp file). Values are in seconds.
RETENTION_COMPLETED_SECONDS = _int("TDB_RETENTION_COMPLETED_SECONDS", 30 * 86400)
RETENTION_FAILED_SECONDS = _int("TDB_RETENTION_FAILED_SECONDS", 7 * 86400)
RETENTION_CANCELLED_SECONDS = _int("TDB_RETENTION_CANCELLED_SECONDS", 1 * 86400)

# How often the lifecycle daemon runs its sweep (orphan + timeout + retention).
DAEMON_INTERVAL_SECONDS = _int("TDB_JOB_DAEMON_INTERVAL_SECONDS", 60)


# ---------------------------------------------------------------------- sqlite
# Applied as `PRAGMA busy_timeout` so concurrent writers wait instead of
# failing immediately with SQLITE_BUSY.
SQLITE_BUSY_TIMEOUT_MS = _int("TDB_SQLITE_BUSY_TIMEOUT_MS", 5000)


# ------------------------------------------------------------------- documents
# Maximum number of curated suggested queries accepted per document at ingest
# time. Curated demo documents carry a small, fixed set of examples.
MAX_SUGGESTED_QUERIES = _int("TDB_MAX_SUGGESTED_QUERIES", 5)


# -------------------------------------------------------------------- projects
# Project name length bound. Names are user-supplied and shown in the Manage
# Projects panel; the cap exists to keep a single row renderable, not for storage.
MAX_PROJECT_NAME_LENGTH = _int("TDB_MAX_PROJECT_NAME_LENGTH", 120)

# Per-project document cap in the nested project tree. Bounds the response the
# way every other store reader is bounded; document_count still reports the true
# total, so a UI can render "120 documents" while listing the newest N.
TREE_DOCS_PER_PROJECT = _int("TDB_TREE_DOCS_PER_PROJECT", 50)


# ------------------------------------------------------------------- retrieval
# Node types that count as a retrievable element. Table units are included so table
# rows/cells are returned by /v1/queries; without them the extractor discards every
# table node the indexer builds.
RETRIEVAL_ELEMENT_TYPES = ("paragraph", "table", "table_row", "table_cell")

# Score multiplier per n-gram order. A longer gram is harder to hit by accident, so it is
# stronger evidence of relevance. This weights rarity of FORM; rarity of OCCURRENCE is the
# IDF term applied alongside it in the extractor.
GRAM_WEIGHTS = {
    "unigram": _int("TDB_GRAM_WEIGHT_UNIGRAM", 1),
    "bigram": _int("TDB_GRAM_WEIGHT_BIGRAM", 4),
    "trigram": _int("TDB_GRAM_WEIGHT_TRIGRAM", 9),
}

# Weight of a symbol match on a table's INHERITED context (its title/heading/lead-in)
# relative to a match on the element's own text (1.0). Below 1.0 because borrowed context
# is weaker evidence; without the discount one table's rows swamp the results.
CONTEXT_MATCH_WEIGHT = _float("TDB_CONTEXT_MATCH_WEIGHT", 0.4)

# Prevents one large table from evicting paragraph results by limiting its row/cell 
# contributions. The full table data remains available when requested.
MAX_ROWS_PER_TABLE = _int("TDB_MAX_ROWS_PER_TABLE", 5)
