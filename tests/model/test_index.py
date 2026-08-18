"""Unit tests for app.model.index.IndexElementRequest."""

import pytest
from pydantic import ValidationError

from app.model.index import IndexElementRequest


class TestIndexElementRequest:
    def test_missing_document_raises(self):
        with pytest.raises(ValidationError):
            IndexElementRequest(metadata={})

    def test_missing_metadata_raises(self):
        with pytest.raises(ValidationError):
            IndexElementRequest(document={"layouts": [], "filename": "a.pdf"})

    def test_valid_minimal_payload(self):
        request = IndexElementRequest(
            metadata={}, document={"layouts": [], "filename": "a.pdf"}
        )
        assert request.document.filename == "a.pdf"
