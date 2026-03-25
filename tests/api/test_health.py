"""Tests for the /health endpoint."""
import inspect
import pytest
from httpx import ASGITransport, AsyncClient

from services.api.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "conversation-api"


def test_startup_does_not_require_anthropic_key(monkeypatch):
    """Startup should not raise if ANTHROPIC_API_KEY is missing."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    # The _require_env calls happen at startup — test that ANTHROPIC_API_KEY
    # is no longer in the required list
    from services.api import main
    source = inspect.getsource(main.startup)
    assert "ANTHROPIC_API_KEY" not in source
    assert "ELEVENLABS_API_KEY" not in source
