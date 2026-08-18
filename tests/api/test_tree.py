"""API-level tests for /v1/tree/json (app.api.tree)."""

import talkingdb.clients.sqlite as sqlite_client
from talkingdb.models.graph.graph import GraphModel


def _save_graph(graph_id: str) -> None:
    gm = GraphModel.create(graph_id, directed=True)
    gm.graph.add_node("n1", type="paragraph", text="hello world")
    gm.graph.add_node("n2", type="paragraph", text="goodbye world")
    gm.graph.add_edge("n1", "n2", type="part_of")
    with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
        gm.save(conn)


class TestDocumentTreeJson:
    def test_requires_auth(self, client, initialized_db):
        response = client.get("/v1/tree/json", params={"graph_id": "graph::x"})
        assert response.status_code in (401, 403)

    def test_unknown_graph_id_returns_404(self, client, auth_headers):
        response = client.get(
            "/v1/tree/json",
            params={"graph_id": "graph::does-not-exist"},
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error_code"] == "GRAPH_NOT_FOUND"

    def test_known_graph_returns_node_link_json(
        self, client, auth_headers, initialized_db
    ):
        _save_graph("graph::demo-tree")

        response = client.get(
            "/v1/tree/json",
            params={"graph_id": "graph::demo-tree"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        body = response.json()
        node_ids = {node["id"] for node in body["nodes"]}
        assert node_ids == {"n1", "n2"}
