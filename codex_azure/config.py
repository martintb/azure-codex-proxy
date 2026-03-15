import json
import os
import secrets
from pathlib import Path

import tomlkit

from . import platform as platform_support


RESOURCE_KEY = "azure_openai_resource"
DEPLOYMENT_KEY = "azure_openai_deployment"
LOCAL_AUTH_TOKEN_KEY = "local_auth_token"
CODEX_PROVIDER_NAME = "azure-openai-proxy"
CODEX_MODEL_NAME = "azure-openai-proxy"
CODEX_DUMMY_API_KEY_ENV = "CODEX_AZURE_OPENAI_DUMMY_API_KEY"
CODEX_DUMMY_API_KEY_VALUE = "azure-openai-proxy"
CODEX_LOCAL_AUTH_ENV = "CODEX_AZURE_PROXY_AUTH_TOKEN"
DEFAULT_PROXY_BASE_URL = "http://127.0.0.1:43123/openai/v1"
DEFAULT_STREAM_IDLE_TIMEOUT_MS = 1800000
DEFAULT_STREAM_MAX_RETRIES = 20
DEFAULT_REQUEST_MAX_RETRIES = 8


def _normalize_resource(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ValueError("Azure OpenAI resource cannot be empty")
    if not normalized.startswith("https://"):
        raise ValueError("Azure OpenAI resource must start with https://")
    host = normalized[len("https://") :].split("/", 1)[0].strip().lower()
    if not host:
        raise ValueError("Azure OpenAI resource must include a host")
    allowed_suffixes = (
        ".openai.azure.com",
        ".services.ai.azure.com",
        ".cognitiveservices.azure.com",
    )
    if not any(host.endswith(suffix) for suffix in allowed_suffixes):
        raise ValueError("Azure OpenAI resource host must be an Azure endpoint")
    return normalized


def get_config_file() -> Path:
    return platform_support.get_proxy_config_file()


def get_codex_config_file() -> Path:
    return platform_support.get_codex_config_file()


def _load_config_file(path: Path) -> dict:
    platform_support.assert_secure_private_file(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_config_files() -> tuple[Path, ...]:
    paths: list[Path] = []
    for path in (
        platform_support.get_proxy_config_file(),
        platform_support.get_legacy_proxy_config_file(),
    ):
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def load_config() -> dict:
    for path in _iter_config_files():
        if path.exists():
            return _load_config_file(path)
    return {}


def save_config(config: dict) -> None:
    platform_support.write_private_text(
        platform_support.get_proxy_config_file(),
        json.dumps(config, indent=2, sort_keys=True) + "\n",
    )


def get_stored_resource() -> str | None:
    config = load_config()
    value = config.get(RESOURCE_KEY)
    if not value:
        return None
    return _normalize_resource(value)


def set_stored_resource(value: str) -> str:
    normalized = _normalize_resource(value)
    config = load_config()
    config[RESOURCE_KEY] = normalized
    save_config(config)
    return normalized


def clear_stored_resource() -> None:
    config = load_config()
    if RESOURCE_KEY in config:
        del config[RESOURCE_KEY]
        save_config(config)


def get_effective_resource() -> str | None:
    env_value = os.environ.get("AZURE_OPENAI_RESOURCE")
    if env_value:
        return _normalize_resource(env_value)
    return get_stored_resource()


def get_stored_deployment() -> str | None:
    config = load_config()
    value = config.get(DEPLOYMENT_KEY)
    if not value:
        return None
    deployment = str(value).strip()
    return deployment or None


def set_stored_deployment(value: str) -> str:
    deployment = value.strip()
    if not deployment:
        raise ValueError("Azure OpenAI deployment cannot be empty")
    config = load_config()
    config[DEPLOYMENT_KEY] = deployment
    save_config(config)
    return deployment


def clear_stored_deployment() -> None:
    config = load_config()
    if DEPLOYMENT_KEY in config:
        del config[DEPLOYMENT_KEY]
        save_config(config)


def get_effective_deployment() -> str | None:
    env_value = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    if env_value:
        deployment = env_value.strip()
        return deployment or None
    return get_stored_deployment()


def get_stored_local_auth_token() -> str | None:
    config = load_config()
    value = config.get(LOCAL_AUTH_TOKEN_KEY)
    if not value:
        return None
    token = str(value).strip()
    return token or None


def ensure_local_auth_token() -> str:
    env_value = os.environ.get(CODEX_LOCAL_AUTH_ENV)
    if env_value:
        token = env_value.strip()
        if token:
            return token
    stored = get_stored_local_auth_token()
    if stored:
        return stored
    token = secrets.token_urlsafe(32)
    config = load_config()
    config[LOCAL_AUTH_TOKEN_KEY] = token
    save_config(config)
    return token


def update_codex_config(resource: str) -> Path:
    _normalize_resource(resource)
    token = ensure_local_auth_token()
    deployment = get_effective_deployment()
    codex_config_file = platform_support.get_codex_config_file()

    if codex_config_file.exists():
        platform_support.assert_secure_private_file(codex_config_file)
        document = tomlkit.parse(codex_config_file.read_text(encoding="utf-8"))
    else:
        document = tomlkit.document()

    existing_model = document.get("model")
    if isinstance(existing_model, str) and existing_model.strip() and existing_model != CODEX_MODEL_NAME:
        document["model"] = existing_model.strip()
    elif deployment:
        document["model"] = deployment
    document["model_provider"] = CODEX_PROVIDER_NAME

    providers = document.get("model_providers")
    if providers is None or not isinstance(providers, dict):
        providers = tomlkit.table()
        document["model_providers"] = providers

    provider = providers.get(CODEX_PROVIDER_NAME)
    if provider is None or not isinstance(provider, dict):
        provider = tomlkit.table()

    provider["name"] = CODEX_PROVIDER_NAME
    provider["env_key"] = CODEX_DUMMY_API_KEY_ENV
    provider["base_url"] = DEFAULT_PROXY_BASE_URL
    provider["wire_api"] = "responses"
    provider["query_params"] = {"api-version": "preview"}
    provider["stream_idle_timeout_ms"] = DEFAULT_STREAM_IDLE_TIMEOUT_MS
    provider["stream_max_retries"] = DEFAULT_STREAM_MAX_RETRIES
    provider["request_max_retries"] = DEFAULT_REQUEST_MAX_RETRIES

    http_headers = provider.get("http_headers")
    if http_headers is None or not isinstance(http_headers, dict):
        http_headers = tomlkit.inline_table()
    http_headers["X-Codex-Proxy-Auth"] = token
    provider["http_headers"] = http_headers

    providers[CODEX_PROVIDER_NAME] = provider

    platform_support.write_private_text(codex_config_file, tomlkit.dumps(document))
    return codex_config_file
