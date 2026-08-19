"""API-level tests for /public (app.api.public), which requires no auth."""

import talkingdb.clients.sqlite as sqlite_client
from talkingdb.helpers.job import store as job_store
from talkingdb.helpers.namespace import store as namespace_store
from talkingdb.models.job.job import JobModel
from talkingdb.models.job.state import JobState
from talkingdb.models.job.type import JobType


def _insert_completed_job(namespace: str) -> JobModel:
    job = JobModel.new(job_type=JobType.DOCUMENT, namespace=namespace, filename="a.pdf")
    job.state = JobState.COMPLETED
    with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
        job_store.insert(conn, job)
    return job


class TestListPublicNamespaces:
    def test_no_auth_required(self, client, initialized_db):
        response = client.get("/public/namespaces")
        assert response.status_code == 200

    def test_includes_reserved_demo_namespace(self, client, initialized_db):
        response = client.get("/public/namespaces")
        names = {ns["namespace"] for ns in response.json()}
        assert "demo-library" in names

    def test_excludes_private_namespaces(self, client, initialized_db):
        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            namespace_store.upsert_namespace(conn, "private-ns", public_read=False)
            namespace_store.upsert_namespace(conn, "open-ns", public_read=True)

        response = client.get("/public/namespaces")

        names = {ns["namespace"] for ns in response.json()}
        assert "open-ns" in names
        assert "private-ns" not in names


class TestListPublicNamespaceDocuments:
    def test_unknown_namespace_returns_404(self, client, initialized_db):
        response = client.get("/public/namespaces/does-not-exist/documents")
        assert response.status_code == 404
        assert response.json()["detail"]["error_code"] == "NAMESPACE_NOT_FOUND"

    def test_private_namespace_returns_404_even_though_it_exists(
        self, client, initialized_db
    ):
        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            namespace_store.upsert_namespace(conn, "secret", public_read=False)

        response = client.get("/public/namespaces/secret/documents")

        assert response.status_code == 404

    def test_public_namespace_lists_its_documents_without_auth(
        self, client, initialized_db
    ):
        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            namespace_store.upsert_namespace(conn, "open-ns", public_read=True)
        job = _insert_completed_job("open-ns")

        response = client.get("/public/namespaces/open-ns/documents")

        assert response.status_code == 200
        ids = [doc["id"] for doc in response.json()]
        assert ids == [job.job_id]
