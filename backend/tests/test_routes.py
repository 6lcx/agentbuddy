import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentbuddy.config import Settings
from agentbuddy.main import create_app


@pytest.mark.parametrize("prefix", ["/api/v1", "/custom/api/v1"])
def test_all_routes_follow_prefix(prefix):
    client = TestClient(create_app(Settings(api_prefix=prefix)))
    assert client.get(f"{prefix}/health").json() == {"status": "ok", "service": "agentbuddy-api"}
    schema = client.get(f"{prefix}/openapi.json").json()
    assert f"{prefix}/health" in schema["paths"]
    assert all(path.startswith(prefix + "/") for path in schema["paths"])
    docs = client.get(f"{prefix}/docs")
    assert docs.status_code == 200
    assert f"{prefix}/openapi.json" in docs.text
    for path in ["/health", "/docs", "/openapi.json", "/redoc"]:
        assert client.get(path).status_code == 404
    if prefix != "/api/v1":
        assert client.get("/api/v1/health").status_code == 404


def test_prefix_from_environment(monkeypatch):
    monkeypatch.setenv("API_PREFIX", "/custom/api/v1/")
    client = TestClient(create_app())
    assert client.get("/custom/api/v1/health").status_code == 200


@pytest.mark.parametrize("prefix", ["", "/", "api/v1", "/a//b", "/a?b", "/../a"])
def test_invalid_prefix_fails_at_startup(prefix):
    with pytest.raises(ValidationError):
        Settings(api_prefix=prefix)
