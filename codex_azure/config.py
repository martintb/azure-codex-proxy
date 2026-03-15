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


def update_codex_config(resource: str) -> Path:
    normalized = _normalize_resource(resource)
    CODEX_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    document = tomlkit.document()
    if CODEX_CONFIG_FILE.exists():
        document = tomlkit.parse(CODEX_CONFIG_FILE.read_text())

    document["model"] = CODEX_MODEL_NAME
    document["model_provider"] = CODEX_PROVIDER_NAME

    model_providers = document.get("model_providers")
    if model_providers is None or not isinstance(model_providers, dict):
        model_providers = tomlkit.table()
        document["model_providers"] = model_providers

    provider = tomlkit.table()
    provider["name"] = CODEX_PROVIDER_NAME
    provider["env_key"] = "OPENAI_API_KEY"
    provider["base_url"] = "http://127.0.0.1:4000/openai/v1"
    provider["wire_api"] = "responses"
    provider["query_params"] = tomlkit.inline_table()
    provider["query_params"]["api-version"] = "preview"
    provider.comment(f"Azure resource: {normalized}")
    model_providers[CODEX_PROVIDER_NAME] = provider

    CODEX_CONFIG_FILE.write_text(tomlkit.dumps(document))
    return CODEX_CONFIG_FILE