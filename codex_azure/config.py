import json
import os
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "codex-azure"
CONFIG_FILE = CONFIG_DIR / "config.json"
RESOURCE_KEY = "azure_openai_resource"


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