"""Unit tests for the Pydantic models in app.model.documents."""

import pytest
from pydantic import ValidationError

from app.model.documents import SupportedUploadType, UploadConstraintsResponse


class TestSupportedUploadType:
    def test_valid_payload(self):
        t = SupportedUploadType(extension="pdf", mime_type="application/pdf", max_file_size_mb=50)
        assert t.extension == "pdf"
        assert t.max_file_size_mb == 50

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            SupportedUploadType(extension="pdf", mime_type="application/pdf")


class TestUploadConstraintsResponse:
    def test_valid_payload(self):
        resp = UploadConstraintsResponse(
            supported_types=[
                SupportedUploadType(
                    extension="pdf", mime_type="application/pdf", max_file_size_mb=50
                ),
                SupportedUploadType(
                    extension="docx",
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    max_file_size_mb=25,
                ),
            ],
            max_file_size_mb=50,
        )
        assert len(resp.supported_types) == 2
        assert resp.max_file_size_mb == 50

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            UploadConstraintsResponse(supported_types=[])
