import json

import pytest
from fastapi.testclient import TestClient


def test_resource_must_be_https_azure_host(isolated_home, load_module):
    config = load_module("codex_azure.config")

    with pytest.raises(ValueError):
        config._normalize_resource("http://example.com")
    with pytest.raises(ValueError):
        config._normalize_resource("https://example.com")
    assert config._normalize_resource("https://myresource.openai.azure.com") == "https://myresource.openai.azure.com"


def test_local_auth_token_persisted_owner_only(isolated_home, load_module):
    config = load_module("codex_azure.config")

    token = config.ensure_local_auth_token()
    assert token

    config_file = config.get_config_file()
    if not config.platform_support.is_windows():
        mode = config_file.stat().st_mode & 0o777
        assert mode == 0o600

    data = json.loads(config_file.read_text(encoding="utf-8"))
    assert data["local_auth_token"] == token


def test_healthz_requires_auth(isolated_home, monkeypatch, load_module):
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "https://myresource.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5")

    app_module = load_module("codex_azure.app")
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


def test_proxy_rejects_wrong_auth_before_forward(isolated_home, monkeypatch, load_module):
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "https://myresource.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5")

    app_module = load_module("codex_azure.app")
    app_module.local_auth_token = "secret"
    client = TestClient(app_module.app)

    response = client.post("/openai/v1/responses", json={"model": "azure-openai-proxy"})
    assert response.status_code == 401


def test_request_size_limit(isolated_home, monkeypatch, load_module):
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "https://myresource.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5")

    app_module = load_module("codex_azure.app")
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


def test_update_codex_config_includes_auth_header(isolated_home, load_module):
    config = load_module("codex_azure.config")

    config.update_codex_config(
        "https://myresource.openai.azure.com",
        "http://127.0.0.1:45123/openai/v1",
    )

    config_text = config.get_codex_config_file().read_text(encoding="utf-8")
    assert "X-Codex-Proxy-Auth" in config_text
    assert "azure-openai-proxy" in config_text
    assert 'base_url = "http://127.0.0.1:45123/openai/v1"' in config_text


def test_update_codex_config_preserves_existing_provider_table(isolated_home, load_module):
    config = load_module("codex_azure.config")
    codex_config_file = config.get_codex_config_file()
    codex_config_file.parent.mkdir(parents=True, exist_ok=True)
    codex_config_file.write_text(
        """
model_provider = "azure-openai-proxy"
profile = "azure_gpt_54"

[model_providers.azure-openai-proxy]
name = "azure-openai-proxy"
base_url = "http://127.0.0.1:43123/openai/v1"
wire_api = "responses"

[profiles.azure_gpt_54]
model_provider = "azure-openai-proxy"
model = "gpt-5.4"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config.update_codex_config(
        "https://myresource.openai.azure.com",
        "http://127.0.0.1:45123/openai/v1",
    )

    config_text = codex_config_file.read_text(encoding="utf-8")
    assert "[profiles.azure_gpt_54]" in config_text
    assert 'http_headers = {X-Codex-Proxy-Auth = ' in config_text


def test_update_codex_config_preserves_existing_real_model(isolated_home, load_module):
    config = load_module("codex_azure.config")
    codex_config_file = config.get_codex_config_file()
    codex_config_file.parent.mkdir(parents=True, exist_ok=True)
    codex_config_file.write_text(
        """
model = "gpt-5.4"
model_provider = "azure-openai-proxy"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config.update_codex_config(
        "https://myresource.openai.azure.com",
        "http://127.0.0.1:45123/openai/v1",
    )

    config_text = codex_config_file.read_text(encoding="utf-8")
    assert 'model = "gpt-5.4"' in config_text


def test_runtime_proxy_state_round_trip(isolated_home, load_module):
    config = load_module("codex_azure.config")

    state_file = config.save_proxy_runtime_state(pid=1234, host="0.0.0.0", port=51234)

    assert state_file == config.get_runtime_proxy_file()
    assert config.load_proxy_runtime_state() == {
        "version": config.PROXY_RUNTIME_VERSION,
        "pid": 1234,
        "host": "127.0.0.1",
        "port": 51234,
    }


def test_preferred_proxy_endpoint_uses_fixed_env_override_without_runtime(isolated_home, monkeypatch, load_module):
    monkeypatch.setenv("AZURE_OPENAI_PROXY_HOST", "0.0.0.0")
    monkeypatch.setenv("AZURE_OPENAI_PROXY_PORT", "45678")

    config = load_module("codex_azure.config")

    assert config.get_preferred_proxy_endpoint() == ("127.0.0.1", 45678)
