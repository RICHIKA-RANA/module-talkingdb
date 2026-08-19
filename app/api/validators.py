import unicodedata
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from talkingdb.clients.sqlite import sqlite_conn, GRAPH_DB
from talkingdb.helpers.namespace import store as namespace_store
from talkingdb.helpers.project import store as project_store

from app.core import config


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error_code": "VALIDATION_ERROR", "message": message},
    )


def clean_optional_text(value: Optional[str]) -> Optional[str]:
    """Trim a free-text form field; treat blank as absent."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def parse_suggested_queries(
    values: Optional[List[str]],
) -> Optional[List[str]]:
    if not values:
        return None

    cleaned = [text.strip() for text in values if text and text.strip()]

    if len(cleaned) > config.MAX_SUGGESTED_QUERIES:
        raise _unprocessable(
            f"at most {config.MAX_SUGGESTED_QUERIES} suggested_queries are allowed"
        )

    return cleaned or None


def validate_project_name(name: Optional[str]) -> str:
    name = clean_optional_text(name)
    if name is None:
        raise _unprocessable("name is required")

    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in name):
        raise _unprocessable("name must not contain control characters")

    name = " ".join(name.split())
    name = unicodedata.normalize("NFC", name)

    if len(name) > config.MAX_PROJECT_NAME_LENGTH:
        raise _unprocessable(
            f"name must be at most {config.MAX_PROJECT_NAME_LENGTH} characters"
        )
    return name


def validate_project_owned(project_id: str, owner_email: str) -> Dict[str, Any]:
    with sqlite_conn(GRAPH_DB) as conn:
        project = project_store.get_for_owner(conn, project_id, owner_email)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "PROJECT_NOT_FOUND",
                "message": f"Unknown project: {project_id}",
            },
        )
    return project


def validate_namespace(namespace: Optional[str]) -> Optional[str]:
    namespace = clean_optional_text(namespace)
    if namespace is None:
        return None
    with sqlite_conn(GRAPH_DB) as conn:
        if namespace_store.get_namespace(conn, namespace) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "NAMESPACE_NOT_FOUND",
                    "message": f"Unknown namespace: {namespace}",
                },
            )
    return namespace
