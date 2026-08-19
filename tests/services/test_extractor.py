"""Unit tests for app.services.extractor.ExtractorService.

Builds small hand-crafted graphs directly (bypassing the indexer) with the
same node/edge shape it produces: symbol nodes typed ``unigram`` /
``bigram`` / ``trigram`` connected to element nodes (typed e.g.
``paragraph``) via ``contains`` (direct match) or ``context`` (borrowed,
discounted) edges.

Requires ``fake_spacy_backend`` (the query text goes through the real
``TextTokenizer``) and ``sqlite_db`` (``graph_cache`` reads/writes through
the patched ``GRAPH_DB``, see the note on ``_reset_graph_cache`` in
conftest.py).
"""

import math

import pytest

import talkingdb.clients.sqlite as sqlite_client
from talkingdb.models.graph.graph import GraphModel

from app.core import config
from app.services.extractor import ExtractorService


def _new_graph(graph_id: str) -> GraphModel:
    return GraphModel.create(graph_id, directed=True)


def _save(gm: GraphModel, db_path: str) -> None:
    with sqlite_client.sqlite_conn(db_path) as conn:
        gm.save(conn)


@pytest.fixture
def build_graph(initialized_db):
    """Factory: build+save a graph from declarative element/symbol specs.

    ``elements`` is a list of (node_id, type, text, metadata) tuples.
    ``symbol_edges`` is a list of (symbol_id, gram_type, element_id, edge_type).
    """

    def _build(graph_id, elements, symbol_edges):
        gm = _new_graph(graph_id)
        for node_id, node_type, text, metadata in elements:
            gm.graph.add_node(
                node_id, type=node_type, text=text, metadata=metadata
            )
        for symbol_id, gram_type, element_id, edge_type in symbol_edges:
            if symbol_id not in gm.graph:
                gm.graph.add_node(symbol_id, type=gram_type)
            gm.graph.add_edge(symbol_id, element_id, type=edge_type)
        _save(gm, initialized_db)
        return graph_id

    return _build


class TestExtractRankingAndScoring:
    def test_ranks_elements_and_symbols_by_weighted_idf_score(
        self, build_graph, fake_spacy_backend
    ):
        graph_id = build_graph(
            "graph::extract-rank",
            elements=[
                ("el1", "paragraph", "hello world example one", None),
                ("el2", "paragraph", "something else entirely", None),
            ],
            symbol_edges=[
                ("hello", "unigram", "el1", "contains"),
                ("world", "unigram", "el1", "contains"),
                ("world", "unigram", "el2", "context"),
                ("hello_world", "bigram", "el1", "contains"),
            ],
        )

        service = ExtractorService(graph_ids=[graph_id], max_matches=10)
        result = service.extract("hello world")

        element_ids = [e["id"] for e in result["elements"]]
        assert element_ids == ["el1", "el2"]
        assert result["elements"][0]["content"] == "hello world example one"
        assert result["elements"][0]["type"] == "paragraph"

        log3 = math.log(3)
        assert result["elements"][0]["score"] == pytest.approx(6 * log3)
        assert result["elements"][1]["score"] == pytest.approx(
            config.CONTEXT_MATCH_WEIGHT * log3
        )

        symbol_ids = [s["id"] for s in result["symbols"]]
        assert symbol_ids == ["hello_world", "world", "hello"]
        assert result["symbols"][0]["type"] == "bigram"
        assert result["symbols"][0]["score"] == pytest.approx(
            config.GRAM_WEIGHTS["bigram"] * log3
        )

    def test_no_matching_symbols_returns_empty_result(
        self, build_graph, fake_spacy_backend
    ):
        graph_id = build_graph(
            "graph::extract-empty",
            elements=[("el1", "paragraph", "completely unrelated text", None)],
            symbol_edges=[("unrelated", "unigram", "el1", "contains")],
        )

        service = ExtractorService(graph_ids=[graph_id])
        result = service.extract("nothing matches here")

        assert result == {"elements": [], "symbols": []}

    def test_max_matches_truncates_results(self, build_graph, fake_spacy_backend):
        elements = [
            (f"el{i}", "paragraph", f"alpha content number {i}", None)
            for i in range(5)
        ]
        symbol_edges = [
            ("alpha", "unigram", f"el{i}", "contains") for i in range(5)
        ]
        graph_id = build_graph("graph::extract-cap", elements, symbol_edges)

        service = ExtractorService(graph_ids=[graph_id], max_matches=2)
        result = service.extract("alpha")

        assert len(result["elements"]) == 2

    def test_multiple_graphs_are_merged(self, build_graph, fake_spacy_backend):
        graph_a = build_graph(
            "graph::extract-multi-a",
            elements=[("el1", "paragraph", "alpha in graph a", None)],
            symbol_edges=[("alpha", "unigram", "el1", "contains")],
        )
        graph_b = build_graph(
            "graph::extract-multi-b",
            elements=[("el1", "paragraph", "alpha in graph b", None)],
            symbol_edges=[("alpha", "unigram", "el1", "contains")],
        )

        service = ExtractorService(graph_ids=[graph_a, graph_b])
        result = service.extract("alpha")

        element_ids = {e["id"] for e in result["elements"]}
        graph_ids_seen = {e["graph_id"] for e in result["elements"]}
        assert element_ids == {"el1"}
        assert graph_ids_seen == {graph_a, graph_b}
        assert len(result["elements"]) == 2


class TestCapPerTable:
    def test_caps_rows_per_table_but_keeps_other_elements(
        self, build_graph, fake_spacy_backend
    ):
        elements = [("para", "paragraph", "beta paragraph content", None)]
        symbol_edges = [("beta", "unigram", "para", "contains")]

        # MAX_ROWS_PER_TABLE rows for table "t1", plus one extra row that
        # should be dropped by the per-table cap.
        for i in range(config.MAX_ROWS_PER_TABLE + 1):
            row_id = f"row{i}"
            elements.append(
                (
                    row_id,
                    "table_row",
                    f"beta row {i}",
                    {"table_id": "t1"},
                )
            )
            symbol_edges.append(("beta", "unigram", row_id, "contains"))

        graph_id = build_graph("graph::extract-table-cap", elements, symbol_edges)

        service = ExtractorService(graph_ids=[graph_id], max_matches=100)
        result = service.extract("beta")

        row_hits = [e for e in result["elements"] if e["type"] == "table_row"]
        para_hits = [e for e in result["elements"] if e["type"] == "paragraph"]
        assert len(row_hits) == config.MAX_ROWS_PER_TABLE
        assert len(para_hits) == 1
        assert para_hits[0]["id"] == "para"
