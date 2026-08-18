"""API-level tests for GET / (app.api.root)."""


def test_get_root_returns_welcome_message(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == "Welcome to Module TalkingDB!"


def test_get_root_content_type_is_json(client):
    response = client.get("/")
    assert response.headers["content-type"].startswith("application/json")
