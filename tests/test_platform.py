from pathlib import Path


class FakeDirs:
    def __init__(self, config_dir: Path, cache_dir: Path):
        self.user_config_dir = str(config_dir)
        self.user_cache_dir = str(cache_dir)


def test_proxy_paths_use_platformdirs_locations(isolated_home, monkeypatch, load_module):
    platform_module = load_module("codex_azure.platform")
    config_dir = isolated_home / "native-config"
    cache_dir = isolated_home / "native-cache"
    monkeypatch.setattr(platform_module, "_platform_dirs", lambda: FakeDirs(config_dir, cache_dir))

    assert platform_module.get_proxy_config_file() == config_dir / "config.json"
    assert platform_module.get_proxy_pid_file() == cache_dir / "azure-openai-proxy.pid"
    assert platform_module.get_proxy_log_file() == cache_dir / "azure-openai-proxy.log"
    assert platform_module.get_proxy_runtime_file() == cache_dir / "azure-openai-proxy.json"


def test_load_config_reads_legacy_file_until_new_path_exists(isolated_home, monkeypatch, load_module):
    platform_module = load_module("codex_azure.platform")
    config_dir = isolated_home / "native-config"
    cache_dir = isolated_home / "native-cache"
    monkeypatch.setattr(platform_module, "_platform_dirs", lambda: FakeDirs(config_dir, cache_dir))

    legacy_file = platform_module.get_legacy_proxy_config_file()
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_text('{"azure_openai_deployment": "legacy"}\n', encoding="utf-8")

    config = load_module("codex_azure.config")

    assert config.load_config()["azure_openai_deployment"] == "legacy"


def test_save_config_migrates_from_legacy_location(isolated_home, monkeypatch, load_module):
    platform_module = load_module("codex_azure.platform")
    config_dir = isolated_home / "native-config"
    cache_dir = isolated_home / "native-cache"
    monkeypatch.setattr(platform_module, "_platform_dirs", lambda: FakeDirs(config_dir, cache_dir))

    legacy_file = platform_module.get_legacy_proxy_config_file()
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_text('{"azure_openai_deployment": "legacy"}\n', encoding="utf-8")

    config = load_module("codex_azure.config")
    assert config.get_stored_deployment() == "legacy"

    config.set_stored_resource("https://myresource.openai.azure.com")

    new_file = config.get_config_file()
    assert new_file.exists()
    assert "https://myresource.openai.azure.com" in new_file.read_text(encoding="utf-8")


def test_assert_secure_private_file_skips_posix_uid_checks_on_windows(isolated_home, monkeypatch, load_module):
    platform_module = load_module("codex_azure.platform")
    sensitive_file = isolated_home / "config.json"
    sensitive_file.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(platform_module, "is_windows", lambda: True)
    monkeypatch.delattr(platform_module.os, "getuid", raising=False)

    platform_module.assert_secure_private_file(sensitive_file)
