"""Unit tests for the Pydantic models in app.model.namespaces."""

import pytest
from pydantic import ValidationError

from app.model.namespaces import NamespaceDocumentResponse, NamespaceResponse


class TestNamespaceResponse:
    def test_valid_payload(self):
        ns = NamespaceResponse(namespace="public", public_read=True)
        assert ns.namespace == "public"
        assert ns.title is None

    def test_missing_required_public_read_raises(self):
        with pytest.raises(ValidationError):
            NamespaceResponse(namespace="public")


class TestNamespaceDocumentResponse:
    def test_defaults_suggested_queries_to_empty_list(self):
        doc = NamespaceDocumentResponse(id="doc-1", state="COMPLETED")
        assert doc.suggested_queries == []

    def test_full_payload(self):
        doc = NamespaceDocumentResponse(
            id="doc-1",
            namespace="public",
            title="Annual Report",
            suggested_queries=["What was revenue?"],
            state="COMPLETED",
        )
        assert doc.title == "Annual Report"
        assert doc.suggested_queries == ["What was revenue?"]

    def test_missing_required_id_raises(self):
        with pytest.raises(ValidationError):
            NamespaceDocumentResponse(state="COMPLETED")
