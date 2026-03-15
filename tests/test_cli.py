from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeDirs:
    def __init__(self, config_dir: Path, cache_dir: Path):
        self.user_config_dir = str(config_dir)
        self.user_cache_dir = str(cache_dir)


def test_has_command_uses_shutil_which(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    calls = []

    def fake_which(command):
        calls.append(command)
        return "/usr/bin/az"

    monkeypatch.setattr(cli.shutil, "which", fake_which)

    assert cli._has_command("az") is True
    assert calls == ["az"]


def test_read_proxy_pid_falls_back_to_legacy_path(isolated_home, monkeypatch, load_module):
    platform_module = load_module("codex_azure.platform")
    monkeypatch.setattr(
        platform_module,
        "_platform_dirs",
        lambda: FakeDirs(isolated_home / "native-config", isolated_home / "native-cache"),
    )

    legacy_pid_file = platform_module.get_legacy_proxy_pid_file()
    legacy_pid_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_pid_file.write_text("4321\n", encoding="utf-8")

    cli = load_module("codex_azure.cli")
    pid, path = cli._read_proxy_pid()

    assert pid == 4321
    assert path == legacy_pid_file


def test_start_proxy_writes_pid_file_to_native_cache_dir(isolated_home, monkeypatch, load_module):
    platform_module = load_module("codex_azure.platform")
    monkeypatch.setattr(
        platform_module,
        "_platform_dirs",
        lambda: FakeDirs(isolated_home / "native-config", isolated_home / "native-cache"),
    )

    legacy_pid_file = platform_module.get_legacy_proxy_pid_file()
    legacy_pid_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_pid_file.write_text("99\n", encoding="utf-8")

    cli = load_module("codex_azure.cli")
    popen_calls = []
    codex_config_calls = []
    runtime_states = iter(
        [
            None,
            None,
            {"pid": 1234, "host": "127.0.0.1", "port": 51234},
        ]
    )

    monkeypatch.setattr(cli, "_is_proxy_healthy", lambda host=None, port=None: (host, port) == ("127.0.0.1", 51234))
    monkeypatch.setattr(cli, "_ensure_resource", lambda: "https://myresource.openai.azure.com")
    monkeypatch.setattr(cli, "_ensure_deployment", lambda: "gpt-5")
    monkeypatch.setattr(cli, "_ensure_az_login", lambda: None)
    monkeypatch.setattr(cli, "ensure_local_auth_token", lambda: "secret")
    monkeypatch.setattr(cli, "load_proxy_runtime_state", lambda: next(runtime_states))
    monkeypatch.setattr(
        cli,
        "_update_codex_proxy_config",
        lambda resource, proxy_base_url=None: codex_config_calls.append((resource, proxy_base_url)),
    )

    def fake_popen(args, **kwargs):
        popen_calls.append((args, kwargs))
        return SimpleNamespace(pid=1234, poll=lambda: None)

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)

    cli._start_proxy()

    pid_file = platform_module.get_proxy_pid_file()
    log_file = platform_module.get_proxy_log_file()
    assert pid_file.read_text(encoding="utf-8") == "1234\n"
    assert log_file.exists()
    assert not legacy_pid_file.exists()
    assert codex_config_calls == [
        ("https://myresource.openai.azure.com", "http://127.0.0.1:51234/openai/v1")
    ]

    _, kwargs = popen_calls[0]
    assert kwargs["env"][cli.CODEX_LOCAL_AUTH_ENV] == "secret"
    if cli.platform_support.is_windows():
        assert kwargs["creationflags"] == (
            getattr(cli.subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(cli.subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        assert kwargs["start_new_session"] is True


def test_launch_codex_execs_on_posix(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    monkeypatch.setattr(cli.platform_support, "is_windows", lambda: False)

    captured = {}

    def fake_execvp(command, args):
        captured["command"] = command
        captured["args"] = args
        raise RuntimeError("stop")

    monkeypatch.setattr(cli.os, "execvp", fake_execvp)

    with pytest.raises(RuntimeError, match="stop"):
        cli._launch_codex(["chat"])

    assert captured == {"command": "codex", "args": ["codex", "chat"]}


def test_launch_codex_returns_child_status_on_windows(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    monkeypatch.setattr(cli.platform_support, "is_windows", lambda: True)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=7))

    with pytest.raises(SystemExit) as excinfo:
        cli._launch_codex(["chat"])

    assert excinfo.value.code == 7


def test_stop_proxy_uses_taskkill_on_windows(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    pid_file = isolated_home / "proxy.pid"
    pid_file.write_text("123\n", encoding="utf-8")
    running = iter([True, False])
    taskkill_calls = []

    monkeypatch.setattr(cli.platform_support, "is_windows", lambda: True)
    monkeypatch.setattr(cli, "_read_proxy_pid", lambda: (123, pid_file))
    monkeypatch.setattr(cli, "_pid_matches_proxy", lambda pid: True)
    monkeypatch.setattr(cli, "_is_process_running", lambda pid: next(running))
    monkeypatch.setattr(cli, "_terminate_windows_process", lambda pid, force: taskkill_calls.append((pid, force)))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cli, "_remove_pid_files", lambda: taskkill_calls.append(("removed", None)))

    assert cli._stop_proxy_process(timeout_seconds=0.1) is True
    assert taskkill_calls == [(123, False), ("removed", None)]


def test_read_proxy_pid_prefers_runtime_state(isolated_home, monkeypatch, load_module):
    platform_module = load_module("codex_azure.platform")
    monkeypatch.setattr(
        platform_module,
        "_platform_dirs",
        lambda: FakeDirs(isolated_home / "native-config", isolated_home / "native-cache"),
    )

    runtime_file = platform_module.get_proxy_runtime_file()
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text('{"version": 1, "pid": 9876, "host": "127.0.0.1", "port": 45678}\n', encoding="utf-8")

    legacy_pid_file = platform_module.get_legacy_proxy_pid_file()
    legacy_pid_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_pid_file.write_text("4321\n", encoding="utf-8")

    cli = load_module("codex_azure.cli")
    pid, path = cli._read_proxy_pid()

    assert pid == 9876
    assert path == runtime_file
