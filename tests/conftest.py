"""Root pytest configuration.

Session-wide setup that has to happen *before* any ``app.*`` module is
imported:

* ``app.api.auth`` imports ``talkingdb.helpers.jwt``, which raises
  ``RuntimeError`` at *import time* if ``JWT_SECRET_KEY`` is not set. Pytest
  imports conftest.py files before it imports test modules, so setting the
  env var here (at module import time, not inside a fixture) guarantees it
  exists before anything under ``tests/`` triggers that import chain.
* ``talkingdb.helpers.client`` reads ``CLIENT_MODE`` at import time; pin it
  to "direct" so tests never depend on a real Content-Elementizer service.
* ``GRAPH_DB`` is given a throwaway default here too, as a last-resort
  safety net - every test that touches SQLite should still go through the
  ``sqlite_db``/``initialized_db`` fixtures below, but this ensures a
  fixture gap fails loudly against a scratch file instead of silently
  writing into the real ``data/graphs.db`` checked into this repo.

Everything else (temporary SQLite databases, the FastAPI app + TestClient,
a fake MinIO backend, a fake spaCy backend, a fake LiteLLM backend) lives in
the fixtures below, shared by every test under ``tests/``.
"""

import importlib
import sqlite3
from types import SimpleNamespace

import pytest

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-for-pytest")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60")
os.environ.setdefault("CLIENT_MODE", "direct")
os.environ.setdefault("GRAPH_DB", "/tmp/pytest-module-ttt-default-graphs.db")


# --------------------------------------------------------------------------
# Real-SQLite fixtures, shared by every test layer.
#
# ``talkingdb.clients.sqlite.sqlite_conn(db_path)`` always takes an explicit
# path - there is no env-driven default read per call. Every app module that
# needs a connection does ``from talkingdb.clients.sqlite import GRAPH_DB``
# at its own module level, which binds a private copy of that string in
# *that* module's namespace. Patching ``talkingdb.clients.sqlite.GRAPH_DB``
# alone therefore does not affect modules that already imported their own
# copy - each such module has to be patched individually, which is what
# ``_GRAPH_DB_MODULES`` below is for.
# --------------------------------------------------------------------------

_GRAPH_DB_MODULES = (
    "talkingdb.helpers.auth",
    "talkingdb.helpers.graph_cache",
    "app.api.auth",
    "app.api.deps",
    "app.api.documents",
    "app.api.jobs",
    "app.api.namespaces",
    "app.api.projects",
    "app.api.public",
    "app.api.validators",
    "app.services.graph",
    "app.services.indexer",
    "app.services.job_context",
    "app.services.job_daemon",
    "app.services.jobs",
    "app.services.workers",
)


def _reset_sqlite_connections() -> None:
    """Close and drop every cached thread-local SQLite connection.

    ``talkingdb.clients.sqlite`` caches one connection per (thread, db_path)
    in ``_thread_local.connections``. Without this, a later test reusing the
    same thread could silently reuse a connection opened against a previous
    test's (already-deleted) temp DB file.
    """
    import talkingdb.clients.sqlite as sqlite_client

    connections = getattr(sqlite_client._thread_local, "connections", None)
    if not connections:
        return
    for conn in list(connections.values()):
        try:
            conn.close()
        except sqlite3.Error:
            pass
    connections.clear()


def _reset_graph_cache() -> None:
    """Drop every entry cached by the process-wide ``graph_cache`` singleton.

    ``talkingdb.helpers.graph_cache.graph_cache`` is created once at import
    time and lives for the whole test session. It caches loaded
    ``GraphModel`` instances by ``graph_id`` with no per-DB scoping, so a
    graph loaded (or absent) against one test's temp DB would otherwise leak
    into the next test that reuses the same graph_id against a different DB.
    """
    import talkingdb.helpers.graph_cache as graph_cache_module

    graph_cache_module.graph_cache._cache.clear()


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    """Point every app module's GRAPH_DB at an empty, isolated temp file.

    Schema is NOT created here - callers use ``initialized_db``, or the real
    app lifespan via the ``client`` fixture, so tests that specifically want
    to exercise schema-creation idempotency can do so against a truly blank
    file.
    """
    import talkingdb.clients.sqlite as sqlite_client

    db_path = str(tmp_path / "test_graphs.db")
    monkeypatch.setattr(sqlite_client, "GRAPH_DB", db_path)
    for modname in _GRAPH_DB_MODULES:
        mod = importlib.import_module(modname)
        monkeypatch.setattr(mod, "GRAPH_DB", db_path, raising=False)

    _reset_sqlite_connections()
    _reset_graph_cache()
    yield db_path
    _reset_sqlite_connections()
    _reset_graph_cache()


@pytest.fixture
def initialized_db(sqlite_db):
    """A temp SQLite DB with every store's schema created directly.

    Mirrors app.services.workers.init_database(), minus ensure_bucket() -
    tests that need MinIO too should also request the ``fake_minio`` fixture
    (or use the ``client`` fixture, which runs the real lifespan - including
    ensure_bucket() - against a patched MinIO client).
    """
    import talkingdb.clients.sqlite as sqlite_client
    from talkingdb.helpers.file_graph import store as file_graph_store
    from talkingdb.helpers.job import store as job_store
    from talkingdb.helpers.namespace import store as namespace_store
    from talkingdb.helpers.project import store as project_store
    from talkingdb.models.auth.api_key import APIKeyModel
    from talkingdb.models.auth.user import UserModel
    from talkingdb.models.graph.graph import GraphModel

    with sqlite_client.sqlite_conn(sqlite_db) as conn:
        GraphModel.init_db(conn)
        UserModel.init_db(conn)
        APIKeyModel.init_db(conn)
        job_store.init_db(conn)
        namespace_store.init_db(conn)
        namespace_store.ensure_reserved(conn)
        project_store.init_db(conn)
        file_graph_store.init_db(conn)
    return sqlite_db


@pytest.fixture
def make_user(initialized_db):
    """Factory: create a user (email, plaintext password) -> UserModel."""

    def _make(email: str = "alice@example.com", password: str = "correct-horse-battery"):
        import talkingdb.clients.sqlite as sqlite_client
        from talkingdb.helpers.auth import hash_password
        from talkingdb.models.auth.user import UserModel

        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            return UserModel.create(
                conn=conn, email=email, password_hash=hash_password(password)
            )

    return _make


@pytest.fixture
def make_api_key(initialized_db):
    """Factory: create an API key for an (existing or new) user email."""

    def _make(user_email: str = "alice@example.com"):
        import talkingdb.clients.sqlite as sqlite_client
        from talkingdb.models.auth.api_key import APIKeyModel

        with sqlite_client.sqlite_conn(sqlite_client.GRAPH_DB) as conn:
            return APIKeyModel.create(conn=conn, user_email=user_email)

    return _make


@pytest.fixture
def auth_headers(make_user, make_api_key):
    """Bearer-auth headers for a freshly created user + API key."""
    make_user(email="alice@example.com", password="correct-horse-battery")
    api_key_obj = make_api_key(user_email="alice@example.com")
    return {"Authorization": f"Bearer {api_key_obj.api_key}"}


# --------------------------------------------------------------------------
# Fake MinIO backend.
#
# Every consumer (``talkingdb.clients.minio.ensure_bucket``,
# ``talkingdb.helpers.file_store.*``) reaches the client through
# ``get_minio_client()``, which lazily creates - and caches - a real
# ``Minio`` instance in the module-level ``_client`` singleton. Because that
# laziness check (``if _client is None``) and the cache itself both live in
# ``talkingdb.clients.minio``'s own globals, pre-seeding ``_client`` there is
# a single patch point that redirects every caller, regardless of which
# module imported ``get_minio_client``/``ensure_bucket`` by name.
# --------------------------------------------------------------------------


class _FakeMinioObject:
    __slots__ = ("data",)

    def __init__(self, data: bytes):
        self.data = data

    @property
    def size(self):
        return len(self.data)


class _FakeMinioStream:
    """Stand-in for the urllib3 response minio.get_object() returns."""

    def __init__(self, data: bytes):
        self._data = data
        self.closed = False
        self.released = False

    def stream(self, chunk_size: int = 32 * 1024):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i : i + chunk_size]

    def read(self):
        return self._data

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class FakeMinioClient:
    """In-memory stand-in for the subset of minio.Minio the app calls."""

    def __init__(self):
        self._buckets = set()
        self._objects = {}  # (bucket, key) -> bytes

    def _not_found(self, key: str):
        from minio.error import S3Error

        return S3Error(
            code="NoSuchKey",
            message="Object does not exist",
            resource=key,
            request_id="fake-request-id",
            host_id="fake-host-id",
            response=None,
        )

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self._buckets

    def make_bucket(self, bucket: str) -> None:
        self._buckets.add(bucket)

    def put_object(self, bucket, key, data, length=-1, **kwargs):
        payload = data.read() if hasattr(data, "read") else data
        self._objects[(bucket, key)] = payload

    def fput_object(self, bucket, key, local_path, **kwargs):
        with open(local_path, "rb") as fh:
            self._objects[(bucket, key)] = fh.read()

    def stat_object(self, bucket, key):
        if (bucket, key) not in self._objects:
            raise self._not_found(key)
        return _FakeMinioObject(self._objects[(bucket, key)])

    def get_object(self, bucket, key):
        if (bucket, key) not in self._objects:
            raise self._not_found(key)
        return _FakeMinioStream(self._objects[(bucket, key)])

    def remove_object(self, bucket, key):
        self._objects.pop((bucket, key), None)

    def presigned_get_object(self, bucket, key, expires=None, **kwargs):
        if (bucket, key) not in self._objects:
            raise self._not_found(key)
        return f"https://fake-minio.test/{bucket}/{key}"


@pytest.fixture
def fake_minio(monkeypatch):
    """Replace the MinIO client singleton with an in-memory fake."""
    import talkingdb.clients.minio as minio_client

    fake = FakeMinioClient()
    monkeypatch.setattr(minio_client, "_client", fake)
    yield fake


# --------------------------------------------------------------------------
# Fake LiteLLM backend for app.core.llm / app.services.summarizer.
# --------------------------------------------------------------------------


@pytest.fixture
def mock_llm(monkeypatch):
    """Replace litellm.completion with a deterministic fake.

    ``app.core.llm`` calls ``litellm.completion(...)`` as a module-qualified
    attribute access, so patching the attribute on the shared ``litellm``
    module redirects every caller (including app.services.summarizer, which
    goes through app.core.llm).
    """
    import litellm

    calls = []

    def _fake_completion(*, messages, timeout=None, **kwargs):
        calls.append({"messages": messages, "timeout": timeout, **kwargs})
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="This is a fake LLM response.")
                )
            ]
        )

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    return calls


# --------------------------------------------------------------------------
# Real FastAPI app + TestClient.
#
# app.main now imports cleanly (every router - including documents and
# projects - resolves at the pinned dependency revisions), so tests run
# against the real app and its real lifespan, not a partial router subset.
# The lifespan calls app.services.workers.init_database() (real schema
# creation against the patched GRAPH_DB, plus ensure_bucket() against the
# patched MinIO client) and app.services.job_daemon.start()/stop().
# --------------------------------------------------------------------------


@pytest.fixture
def client(sqlite_db, fake_minio):
    """A TestClient wired to the real app, an isolated temp SQLite file,
    and an in-memory MinIO fake. Triggers the real startup/shutdown lifespan."""
    from fastapi.testclient import TestClient

    import app.main as main_module

    with TestClient(main_module.app) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# Fake spaCy backend for app.services.package_text_tokenizer.TextTokenizer.
#
# TextTokenizer lazily calls ``spacy.load("en_core_web_md")`` on first use.
# Downloading/loading the real model is slow, needs network access, and
# would make tokenizer behaviour (and therefore extractor/query-ranking
# behaviour) depend on a large opaque third-party model rather than on our
# code. For unit/API tests we substitute a small, deterministic fake spaCy
# pipeline that implements just the attributes TextTokenizer actually reads
# (``text``, ``lemma_``, ``is_alpha``, ``is_stop``, ``is_space``,
# ``is_punct``) plus a no-op Matcher (compound-phrase matching is not
# exercised by these tests; TextTokenizer degrades gracefully to per-token
# output when the matcher finds nothing, which is exactly what we want to
# test against).
# --------------------------------------------------------------------------

_FAKE_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "in", "on", "at", "of",
    "to", "for", "and", "or", "but", "with", "as", "by", "it", "this",
    "that", "be", "been",
}
_FAKE_PUNCT = set(".,;:!?()[]{}\"'`")


class _FakeToken:
    def __init__(self, text: str):
        self.text = text
        self.is_space = text.isspace()
        self.is_punct = text in _FAKE_PUNCT
        self.is_alpha = text.isalpha()
        self.is_stop = text.lower() in _FAKE_STOPWORDS
        # Cheap deterministic "lemma": lowercase, drop a single trailing 's'
        # off words of >3 chars so e.g. "drugs" -> "drug" without a real
        # morphological analyzer.
        lower = text.lower()
        if self.is_alpha and len(lower) > 3 and lower.endswith("s"):
            lower = lower[:-1]
        self.lemma_ = lower


class _FakeVocab:
    def __init__(self):
        self.strings = {}


class _FakeDoc(list):
    pass


class _FakeNLP:
    def __init__(self):
        self.vocab = _FakeVocab()

    def __call__(self, text: str) -> _FakeDoc:
        import re

        raw_tokens = re.findall(r"\w+(?:\.\w+)*|[^\w\s]", text, flags=re.UNICODE)
        return _FakeDoc(_FakeToken(t) for t in raw_tokens)


class _FakeMatcher:
    """No-op stand-in for spacy.matcher.Matcher: never matches a compound."""

    def __init__(self, vocab, *args, **kwargs):
        self._vocab = vocab

    def add(self, name, patterns):
        pass

    def __call__(self, doc):
        return []


@pytest.fixture
def fake_spacy_backend(monkeypatch):
    """Replace TextTokenizer's spaCy pipeline with a small fake, in-place.

    Patches the names already bound inside app.services.package_text_tokenizer
    (``spacy`` and ``Matcher``), not the spacy package itself, so it has no
    effect outside that module.
    """
    import app.services.package_text_tokenizer as tokenizer_module

    monkeypatch.setattr(tokenizer_module.spacy, "load", lambda *a, **kw: _FakeNLP())
    monkeypatch.setattr(tokenizer_module, "Matcher", _FakeMatcher)
    yield
