"""Tests for app.api.deps.require_public_namespace, via a couple of direct
unit-style calls against a real sqlite fixture (there is no standalone
route that returns its raw dict; app.api.public's routes exercise it
indirectly and are covered in tests/api/test_public.py)."""

import pytest
from fastapi import HTTPException

import talkingdb.clients.sqlite as sqlite_client
from talkingdb.helpers.namespace import store as namespace_store

from app.api.deps import require_public_namespace


def test_raises_404_for_unknown_namespace(initialized_db):
    with pytest.raises(HTTPException) as exc_info:
        require_public_namespace(namespace="totally-unknown")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error_code"] == "NAMESPACE_NOT_FOUND"


def test_raises_404_for_private_namespace(initialized_db):
    with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
        namespace_store.upsert_namespace(conn, "private-ns", public_read=False)

    with pytest.raises(HTTPException) as exc_info:
        require_public_namespace(namespace="private-ns")
    assert exc_info.value.status_code == 404


def test_returns_namespace_dict_for_public_namespace(initialized_db):
    with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
        namespace_store.upsert_namespace(
            conn, "open-ns", title="Open", public_read=True
        )

    result = require_public_namespace(namespace="open-ns")

    assert result["namespace"] == "open-ns"
    assert result["public_read"] is True
