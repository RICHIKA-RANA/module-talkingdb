"""Unit tests for the pure and DB-touching functions in app.api.validators."""

import pytest
from fastapi import HTTPException

import talkingdb.clients.sqlite as sqlite_client
from talkingdb.helpers.namespace import store as namespace_store
from talkingdb.helpers.project import store as project_store

from app.api import validators
from app.core import config


class TestCleanOptionalText:
    def test_none_returns_none(self):
        assert validators.clean_optional_text(None) is None

    def test_strips_whitespace(self):
        assert validators.clean_optional_text("  hello  ") == "hello"

    def test_blank_string_becomes_none(self):
        assert validators.clean_optional_text("   ") is None

    def test_empty_string_becomes_none(self):
        assert validators.clean_optional_text("") is None

    def test_non_blank_text_passes_through(self):
        assert validators.clean_optional_text("hello world") == "hello world"


class TestParseSuggestedQueries:
    def test_none_returns_none(self):
        assert validators.parse_suggested_queries(None) is None

    def test_empty_list_returns_none(self):
        assert validators.parse_suggested_queries([]) is None

    def test_strips_and_drops_blank_entries(self):
        result = validators.parse_suggested_queries(["  what is x?  ", "", "   ", "another?"])
        assert result == ["what is x?", "another?"]

    def test_all_blank_entries_returns_none(self):
        assert validators.parse_suggested_queries(["", "   "]) is None

    def test_within_limit_passes(self):
        values = [f"query {i}" for i in range(config.MAX_SUGGESTED_QUERIES)]
        result = validators.parse_suggested_queries(values)
        assert len(result) == config.MAX_SUGGESTED_QUERIES

    def test_over_limit_raises_422(self):
        values = [f"query {i}" for i in range(config.MAX_SUGGESTED_QUERIES + 1)]
        with pytest.raises(HTTPException) as exc_info:
            validators.parse_suggested_queries(values)
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["error_code"] == "VALIDATION_ERROR"


class TestValidateProjectName:
    def test_valid_name_passes_through(self):
        assert validators.validate_project_name("My Project") == "My Project"

    def test_strips_whitespace(self):
        assert validators.validate_project_name("  My Project  ") == "My Project"

    def test_none_raises_422(self):
        with pytest.raises(HTTPException) as exc_info:
            validators.validate_project_name(None)
        assert exc_info.value.status_code == 422
        assert "required" in exc_info.value.detail["message"]

    def test_blank_raises_422(self):
        with pytest.raises(HTTPException) as exc_info:
            validators.validate_project_name("   ")
        assert exc_info.value.status_code == 422

    def test_too_long_raises_422(self):
        too_long = "x" * (config.MAX_PROJECT_NAME_LENGTH + 1)
        with pytest.raises(HTTPException) as exc_info:
            validators.validate_project_name(too_long)
        assert exc_info.value.status_code == 422
        assert "at most" in exc_info.value.detail["message"]

    def test_exactly_max_length_passes(self):
        exactly_max = "x" * config.MAX_PROJECT_NAME_LENGTH
        assert validators.validate_project_name(exactly_max) == exactly_max

    def test_collapses_internal_whitespace(self):
        assert validators.validate_project_name("My   Project") == "My Project"


class TestValidateProjectOwned:
    def test_unknown_project_raises_404(self, initialized_db):
        with pytest.raises(HTTPException) as exc_info:
            validators.validate_project_owned("proj-does-not-exist", "alice@example.com")
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == "PROJECT_NOT_FOUND"

    def test_owned_project_returns_project_dict(self, initialized_db):
        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            project = project_store.create(
                conn,
                name="My Project",
                logo=None,
                logo_media_type=None,
                owner_email="alice@example.com",
            )

        result = validators.validate_project_owned(project["project_id"], "alice@example.com")

        assert result["project_id"] == project["project_id"]

    def test_project_owned_by_someone_else_raises_404(self, initialized_db):
        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            project = project_store.create(
                conn,
                name="Bob's Project",
                logo=None,
                logo_media_type=None,
                owner_email="bob@example.com",
            )

        with pytest.raises(HTTPException) as exc_info:
            validators.validate_project_owned(project["project_id"], "alice@example.com")
        assert exc_info.value.status_code == 404


class TestValidateNamespace:
    def test_none_returns_none(self, initialized_db):
        assert validators.validate_namespace(None) is None

    def test_blank_returns_none(self, initialized_db):
        assert validators.validate_namespace("   ") is None

    def test_unknown_namespace_raises_400(self, initialized_db):
        with pytest.raises(HTTPException) as exc_info:
            validators.validate_namespace("does-not-exist")
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error_code"] == "NAMESPACE_NOT_FOUND"

    def test_known_namespace_passes_through(self, initialized_db):
        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            namespace_store.upsert_namespace(conn, "demo-library", public_read=True)

        assert validators.validate_namespace("demo-library") == "demo-library"
