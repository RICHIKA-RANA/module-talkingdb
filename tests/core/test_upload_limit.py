"""Tests for app.core.upload_limit.UploadSizeLimitMiddleware."""

import asyncio

import pytest

from app.core.upload_limit import UploadSizeLimitMiddleware


def _make_scope(*, method="POST", path="/v1/documents", content_length=None):
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {"type": "http", "method": method, "path": path, "headers": headers}


async def _run(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


class TestUploadSizeLimitMiddleware:
    def test_passes_through_non_http_scope(self):
        calls = []

        async def inner(scope, receive, send):
            calls.append(scope)

        middleware = UploadSizeLimitMiddleware(inner)
        asyncio.run(middleware({"type": "lifespan"}, None, None))

        assert calls == [{"type": "lifespan"}]

    def test_passes_through_non_matching_path(self):
        calls = []

        async def inner(scope, receive, send):
            calls.append(scope)

        middleware = UploadSizeLimitMiddleware(inner)
        scope = _make_scope(path="/v1/other", content_length=10**9)
        asyncio.run(middleware(scope, None, None))

        assert calls == [scope]

    def test_passes_through_get_on_matching_path(self):
        calls = []

        async def inner(scope, receive, send):
            calls.append(scope)

        middleware = UploadSizeLimitMiddleware(inner)
        scope = _make_scope(method="GET", content_length=10**9)
        asyncio.run(middleware(scope, None, None))

        assert calls == [scope]

    def test_passes_through_when_under_cap(self):
        calls = []

        async def inner(scope, receive, send):
            calls.append(scope)

        middleware = UploadSizeLimitMiddleware(inner)
        scope = _make_scope(content_length=1024)
        asyncio.run(middleware(scope, None, None))

        assert calls == [scope]

    def test_rejects_oversized_upload_with_413(self):
        async def inner(scope, receive, send):
            raise AssertionError("inner app should not be called")

        middleware = UploadSizeLimitMiddleware(inner)
        scope = _make_scope(content_length=10**12)

        sent = asyncio.run(_run(middleware, scope))

        start = next(m for m in sent if m["type"] == "http.response.start")
        body = next(m for m in sent if m["type"] == "http.response.body")
        assert start["status"] == 413
        assert b"FILE_TOO_LARGE" in body["body"]

    def test_custom_paths_are_respected(self):
        calls = []

        async def inner(scope, receive, send):
            calls.append(scope)

        middleware = UploadSizeLimitMiddleware(inner, paths=("/v1/custom",))
        scope = _make_scope(path="/v1/documents", content_length=10**12)
        asyncio.run(middleware(scope, None, None))

        # /v1/documents is no longer a guarded path, so it passes through
        # even though the content-length is huge.
        assert calls == [scope]


class TestContentLengthOverEveryCap:
    def test_413_response_via_real_endpoint(self, client):
        response = client.post(
            "/v1/documents",
            headers={"Content-Length": str(10**12)},
        )
        assert response.status_code == 413
        assert response.json()["detail"]["error_code"] == "FILE_TOO_LARGE"
