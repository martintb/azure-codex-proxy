import importlib
import sys

import platformdirs
import pytest


class FakePlatformDirs:
    def __init__(self, config_dir, cache_dir):
        self.user_config_dir = str(config_dir)
        self.user_cache_dir = str(cache_dir)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    config_dir = home / "native-config"
    cache_dir = home / "native-cache"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setattr(
        platformdirs,
        "PlatformDirs",
        lambda *args, **kwargs: FakePlatformDirs(config_dir, cache_dir),
    )
    monkeypatch.delenv("AZURE_OPENAI_RESOURCE", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    monkeypatch.delenv("CODEX_AZURE_PROXY_AUTH_TOKEN", raising=False)
    return home


@pytest.fixture
def load_module():
    def _load_module(name: str):
        module = importlib.import_module(name)
        return importlib.reload(module)

    return _load_module


@pytest.fixture(autouse=True)
def clear_modules():
    yield
    for name in ("codex_azure.app", "codex_azure.cli", "codex_azure.config", "codex_azure.platform"):
        sys.modules.pop(name, None)
