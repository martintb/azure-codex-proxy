import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AZURE_OPENAI_RESOURCE", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    monkeypatch.delenv("CODEX_AZURE_PROXY_AUTH_TOKEN", raising=False)
    return home


def test_resource_must_be_https_azure_host(isolated_home):
    from codex_azure.config import _normalize_resource

    with pytest.raises(ValueError):
        _normalize_resource("http://example.com")
    with pytest.raises(ValueError):
        _normalize_resource("https://example.com")
    assert _normalize_resource("https://myresource.openai.azure.com") == "https://myresource.openai.azure.com"


def test_local_auth_token_persisted_owner_only(isolated_home):
    from codex_azure.config import CONFIG_FILE, ensure_local_auth_token

    token = ensure_local_auth_token()
    assert token
    mode = CONFIG_FILE.stat().st_mode & 0o777
    assert mode == 0o600
    data = json.loads(CONFIG_FILE.read_text())
    assert data["local_auth_token"] == token


def test_healthz_requires_auth(isolated_home, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "https://myresource.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5")

    import importlib
    app_module = importlib.import_module("codex_azure.app")

    app_module.local_auth_token = "secret"

    async def fake_token(force_refresh=False):
        return "token"

    app_module.get_valid_token = fake_token
    client = TestClient(app_module.app)

    unauthorized = client.get("/healthz")
    assert unauthorized.status_code == 401
    authorized = client.get("/healthz", headers={"X-Codex-Proxy-Auth": "secret"})
    assert authorized.status_code == 200
    assert authorized.json() == {"ok": True}


def test_proxy_rejects_wrong_auth_before_forward(isolated_home, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "https://myresource.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5")

    import importlib
    app_module = importlib.import_module("codex_azure.app")

    app_module.local_auth_token = "secret"
    client = TestClient(app_module.app)
    response = client.post("/openai/v1/responses", json={"model": "azure-openai-proxy"})
    assert response.status_code == 401


def test_request_size_limit(isolated_home, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "https://myresource.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5")

    import importlib
    app_module = importlib.import_module("codex_azure.app")

    app_module.local_auth_token = "secret"
    app_module.MAX_REQUEST_BODY_BYTES = 4
    app_module.http_client = object()
    client = TestClient(app_module.app)
    response = client.post(
        "/openai/v1/responses",
        content=b"12345",
        headers={"X-Codex-Proxy-Auth": "secret", "content-type": "application/octet-stream"},
    )
    assert response.status_code == 413


def test_update_codex_config_includes_auth_header(isolated_home):
    from codex_azure.config import CODEX_CONFIG_FILE, update_codex_config

    update_codex_config("https://myresource.openai.azure.com")
    config_text = CODEX_CONFIG_FILE.read_text()
    assert "X-Codex-Proxy-Auth" in config_text
    assert "azure-openai-proxy" in config_text


def test_update_codex_config_preserves_existing_provider_table(isolated_home):
    from codex_azure.config import CODEX_CONFIG_FILE, update_codex_config

    CODEX_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG_FILE.write_text(
        """
model_provider = \"azure-openai-proxy\"
profile = \"azure_gpt_54\"

[model_providers.azure-openai-proxy]
name = \"azure-openai-proxy\"
base_url = \"http://127.0.0.1:43123/openai/v1\"
wire_api = \"responses\"

[profiles.azure_gpt_54]
model_provider = \"azure-openai-proxy\"
model = \"gpt-5.4\"
""".strip()
        + "\n"
    )

    update_codex_config("https://myresource.openai.azure.com")

    config_text = CODEX_CONFIG_FILE.read_text()
    assert "[profiles.azure_gpt_54]" in config_text
    assert 'http_headers = {X-Codex-Proxy-Auth = ' in config_text


def test_update_codex_config_preserves_existing_real_model(isolated_home):
    from codex_azure.config import CODEX_CONFIG_FILE, update_codex_config

    CODEX_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG_FILE.write_text(
        """
model = \"gpt-5.4\"
model_provider = \"azure-openai-proxy\"
""".strip()
        + "\n"
    )

    update_codex_config("https://myresource.openai.azure.com")

    config_text = CODEX_CONFIG_FILE.read_text()
    assert 'model = "gpt-5.4"' in config_text
