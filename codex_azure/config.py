import json
import os
from pathlib import Path

import tomlkit


CONFIG_DIR = Path.home() / ".config" / "codex-azure"
CONFIG_FILE = CONFIG_DIR / "config.json"
RESOURCE_KEY = "azure_openai_resource"
CODEX_CONFIG_DIR = Path.home() / ".codex"
CODEX_CONFIG_FILE = CODEX_CONFIG_DIR / "config.toml"
CODEX_PROVIDER_NAME = "azure-openai-proxy"
CODEX_MODEL_NAME = "azure-openai-proxy"
CODEX_DUMMY_API_KEY_ENV = "CODEX_AZURE_OPENAI_DUMMY_API_KEY"
CODEX_DUMMY_API_KEY_VALUE = "azure-openai-proxy"
DEFAULT_PROXY_BASE_URL = "http://127.0.0.1:43123/openai/v1"
DEPLOYMENT_KEY = "azure_openai_deployment"
DEFAULT_STREAM_IDLE_TIMEOUT_MS = 1800000
DEFAULT_STREAM_MAX_RETRIES = 20
DEFAULT_REQUEST_MAX_RETRIES = 8


def _normalize_resource(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ValueError("Azure OpenAI resource cannot be empty")
    if not normalized.startswith("http://") and not normalized.startswith("https://"):
        raise ValueError("Azure OpenAI resource must start with http:// or https://")
    return normalized


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    return json.loads(CONFIG_FILE.read_text())


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")


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


def update_codex_config(resource: str) -> Path:
    normalized = _normalize_resource(resource)
    CODEX_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    document = tomlkit.document()
    if CODEX_CONFIG_FILE.exists():
        document = tomlkit.parse(CODEX_CONFIG_FILE.read_text())

    configured_deployment = get_effective_deployment()
    document["model"] = configured_deployment or CODEX_MODEL_NAME
    document["model_provider"] = CODEX_PROVIDER_NAME

    profile_name = document.get("profile")
    if isinstance(profile_name, str):
        profiles = document.get("profiles")
        if isinstance(profiles, dict):
            profile = profiles.get(profile_name)
            if isinstance(profile, dict) and profile.get("model_provider") == "azure":
                profile["model_provider"] = CODEX_PROVIDER_NAME
                if profile.get("model") == "gpt-5.4" and configured_deployment:
                    profile["model"] = configured_deployment
                elif profile.get("model") == "gpt-5.4":
                    profile["model"] = CODEX_MODEL_NAME

    model_providers = document.get("model_providers")
    if model_providers is None or not isinstance(model_providers, dict):
        model_providers = tomlkit.table()
        document["model_providers"] = model_providers

    provider = tomlkit.table()
    provider["name"] = CODEX_PROVIDER_NAME
    provider["env_key"] = CODEX_DUMMY_API_KEY_ENV
    provider["base_url"] = DEFAULT_PROXY_BASE_URL
    provider["wire_api"] = "responses"
    provider["query_params"] = tomlkit.inline_table()
    provider["query_params"]["api-version"] = "preview"
    provider["stream_idle_timeout_ms"] = DEFAULT_STREAM_IDLE_TIMEOUT_MS
    provider["stream_max_retries"] = DEFAULT_STREAM_MAX_RETRIES
    provider["request_max_retries"] = DEFAULT_REQUEST_MAX_RETRIES
    provider.comment(f"Azure resource: {normalized}")
    model_providers[CODEX_PROVIDER_NAME] = provider

    CODEX_CONFIG_FILE.write_text(tomlkit.dumps(document))
    return CODEX_CONFIG_FILE