"""API-level tests for /index (app.api.index).

``POST /index/document/elements`` accepts a full ``DocumentModel`` (a
dataclass with nested layouts/elements) as JSON, which is impractical to
hand-build as a request body in an API-level test - that scenario is
exercised at the unit level instead (see
tests/services/test_indexer.py-adjacent route-handler tests below),
calling the route coroutine directly with real, small in-memory model
instances. Here we cover request validation and the GET /index/html route,
which only needs a graph_id.
"""

import asyncio

import pytest
import talkingdb.clients.sqlite as sqlite_client
from talkingdb.models.document.document import DocumentModel
from talkingdb.models.document.elements.base.base import RunModel
from talkingdb.models.document.elements.primitive.paragraph import ParagraphModel
from talkingdb.models.document.layouts.layout import LayoutModel
from talkingdb.models.graph.graph import GraphModel
from talkingdb.models.metadata.metadata import Metadata

from app.api.index import parse_element, view_graph
from app.model.index import IndexElementRequest


class TestParseElementValidation:
    def test_missing_body_returns_422(self, client):
        response = client.post("/index/document/elements")
        assert response.status_code == 422

    def test_missing_document_field_returns_422(self, client):
        response = client.post(
            "/index/document/elements",
            json={"metadata": {"scope": "org"}},
        )
        assert response.status_code == 422


class TestViewGraphHtml:
    def test_unknown_graph_id_returns_404(self, client, initialized_db):
        response = client.get("/index/html", params={"graph_id": "graph::nope"})
        assert response.status_code == 404

    def test_known_graph_returns_html_page(self, client, initialized_db):
        gm = GraphModel.create("graph::html-view", directed=True)
        gm.graph.add_node("n1", type="paragraph", text="hello")
        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            gm.save(conn)

        response = client.get("/index/html", params={"graph_id": "graph::html-view"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<!DOCTYPE html>" in response.text
        assert '"n1"' in response.text


def _document_with_one_paragraph() -> DocumentModel:
    layout = LayoutModel(
        orientation="PORTRAIT",
        elements=[ParagraphModel(runs=[RunModel(text="Quarterly revenue rose sharply.")])],
    )
    doc = DocumentModel(layouts=[layout], filename="report.pdf")
    doc.assign_ids()
    return doc


class TestParseElementRouteHandler:
    """Calls the route coroutine directly with real in-memory models,
    exercising the same code the HTTP route body runs without fighting
    FastAPI's request parsing of a DocumentModel body."""

    def test_indexes_document_and_returns_graph_id(self, initialized_db, fake_spacy_backend):
        request = IndexElementRequest(
            metadata=Metadata(), document=_document_with_one_paragraph()
        )

        response = asyncio.run(parse_element(request))

        assert response["graph_id"].startswith("graph::")
        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            reloaded = GraphModel.load(conn, response["graph_id"])
        assert len(reloaded.graph.nodes) > 0

    def test_defaults_metadata_when_absent(self, initialized_db, fake_spacy_backend):
        request = IndexElementRequest(
            metadata=Metadata(scope="org"), document=_document_with_one_paragraph()
        )

        response = asyncio.run(parse_element(request))

        assert "graph_id" in response


class TestViewGraphRouteHandler:
    def test_returns_html_for_existing_graph(self, initialized_db):
        gm = GraphModel.create("graph::direct-call", directed=True)
        gm.graph.add_node("n1", type="paragraph", text="hello")
        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            gm.save(conn)

        html = asyncio.run(view_graph("graph::direct-call"))

        assert "<!DOCTYPE html>" in html
        assert '"n1"' in html

    def test_raises_404_for_unknown_graph(self, initialized_db):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(view_graph("graph::does-not-exist"))
        assert exc_info.value.status_code == 404
