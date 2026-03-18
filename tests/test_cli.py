import sys
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


def test_ensure_codex_installed_raises_helpful_error(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    monkeypatch.setattr(cli, "_has_command", lambda command: False)

    with pytest.raises(RuntimeError, match="`codex` was not found on PATH"):
        cli._ensure_codex_installed()


def test_cli_import_does_not_import_app_module(isolated_home, load_module):
    cli = load_module("codex_azure.cli")

    assert cli.LOCAL_AUTH_HEADER == "x-codex-proxy-auth"
    assert "codex_azure.app" not in sys.modules


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


def test_start_proxy_reuses_healthy_existing_proxy_without_spawning(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    codex_config_calls = []
    popen_calls = []

    monkeypatch.setattr(cli, "_is_proxy_healthy", lambda host=None, port=None: True)
    monkeypatch.setattr(cli, "_get_running_proxy_base_url", lambda: "http://127.0.0.1:51234/openai/v1")
    monkeypatch.setattr(cli, "get_effective_resource", lambda: "https://myresource.openai.azure.com")
    monkeypatch.setattr(
        cli,
        "_update_codex_proxy_config",
        lambda resource, proxy_base_url=None: codex_config_calls.append((resource, proxy_base_url)),
    )
    monkeypatch.setattr(cli, "_ensure_resource", lambda: pytest.fail("should reuse the existing proxy"))
    monkeypatch.setattr(cli, "_ensure_deployment", lambda: pytest.fail("should reuse the existing proxy"))
    monkeypatch.setattr(cli, "_ensure_az_login", lambda: pytest.fail("should reuse the existing proxy"))
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)),
    )

    cli._start_proxy()

    assert popen_calls == []
    assert codex_config_calls == [
        ("https://myresource.openai.azure.com", "http://127.0.0.1:51234/openai/v1")
    ]


def test_start_proxy_reports_auth_guidance_from_log(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    log_file = cli.platform_support.get_proxy_log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(
        "ERROR Azure authentication failed during proxy startup: Please run 'az login' to set up an account\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "_is_proxy_healthy", lambda host=None, port=None: False)
    monkeypatch.setattr(cli, "_ensure_resource", lambda: "https://myresource.openai.azure.com")
    monkeypatch.setattr(cli, "_ensure_deployment", lambda: "gpt-5")
    monkeypatch.setattr(cli, "_ensure_az_login", lambda: None)
    monkeypatch.setattr(cli, "ensure_local_auth_token", lambda: "secret")
    monkeypatch.setattr(cli, "load_proxy_runtime_state", lambda: None)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=1234, poll=lambda: 1),
    )

    with pytest.raises(RuntimeError, match="az login --use-device-code"):
        cli._start_proxy()


def test_start_proxy_reports_generic_log_error_when_unclassified(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    log_file = cli.platform_support.get_proxy_log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("plain startup failure\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_is_proxy_healthy", lambda host=None, port=None: False)
    monkeypatch.setattr(cli, "_ensure_resource", lambda: "https://myresource.openai.azure.com")
    monkeypatch.setattr(cli, "_ensure_deployment", lambda: "gpt-5")
    monkeypatch.setattr(cli, "_ensure_az_login", lambda: None)
    monkeypatch.setattr(cli, "ensure_local_auth_token", lambda: "secret")
    monkeypatch.setattr(cli, "load_proxy_runtime_state", lambda: None)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=1234, poll=lambda: 1),
    )

    with pytest.raises(RuntimeError, match="Proxy exited before it became healthy"):
        cli._start_proxy()


def test_get_codex_passthrough_args_distinguishes_internal_commands(isolated_home, load_module):
    cli = load_module("codex_azure.cli")

    assert cli._get_codex_passthrough_args([]) == []
    assert cli._get_codex_passthrough_args(["chat"]) == ["chat"]
    assert cli._get_codex_passthrough_args(["--model", "gpt-5.4"]) == ["--model", "gpt-5.4"]
    assert cli._get_codex_passthrough_args(["run", "--help"]) == ["--help"]
    assert cli._get_codex_passthrough_args(["config"]) is None
    assert cli._get_codex_passthrough_args(["stop-proxy"]) is None
    assert cli._get_codex_passthrough_args(["--help"]) is None


def test_main_checks_for_codex_before_starting_proxy(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    start_calls = []

    monkeypatch.setattr(cli.sys, "argv", ["codex-azure", "chat"])
    monkeypatch.setattr(cli, "_ensure_codex_installed", lambda: (_ for _ in ()).throw(RuntimeError("missing codex")))
    monkeypatch.setattr(cli, "_start_proxy", lambda: start_calls.append("started"))

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    assert start_calls == []


@pytest.mark.parametrize(
    ("argv", "expected_codex_args"),
    [
        ([], []),
        (["chat"], ["chat"]),
        (["--model", "gpt-5.4"], ["--model", "gpt-5.4"]),
        (["run", "--help"], ["--help"]),
        (["run", "config"], ["config"]),
    ],
)
def test_main_passes_codex_args_through(isolated_home, monkeypatch, load_module, argv, expected_codex_args):
    cli = load_module("codex_azure.cli")
    calls = []

    monkeypatch.setattr(cli.sys, "argv", ["codex-azure", *argv])
    monkeypatch.setattr(cli, "_ensure_codex_installed", lambda: calls.append("ensure"))
    monkeypatch.setattr(cli, "_start_proxy", lambda: calls.append("start"))
    monkeypatch.setattr(cli, "ensure_local_auth_token", lambda: "secret")
    monkeypatch.setattr(cli, "_launch_codex", lambda args: calls.append(("launch", args)))

    cli.main()

    assert calls == ["ensure", "start", ("launch", expected_codex_args)]


def test_main_routes_internal_config_command_without_launching_codex(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    calls = []

    monkeypatch.setattr(cli.sys, "argv", ["codex-azure", "config", "show-resource"])
    monkeypatch.setattr(cli, "_print_resource", lambda: calls.append("show-resource") or 0)
    monkeypatch.setattr(cli, "_ensure_codex_installed", lambda: calls.append("ensure"))
    monkeypatch.setattr(cli, "_start_proxy", lambda: calls.append("start"))
    monkeypatch.setattr(cli, "_launch_codex", lambda args: calls.append(("launch", args)))

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert calls == ["show-resource"]


def test_main_shows_top_level_help_without_launching_codex(isolated_home, monkeypatch, load_module, capsys):
    cli = load_module("codex_azure.cli")
    calls = []

    monkeypatch.setattr(cli.sys, "argv", ["codex-azure", "--help"])
    monkeypatch.setattr(cli, "_ensure_codex_installed", lambda: calls.append("ensure"))
    monkeypatch.setattr(cli, "_start_proxy", lambda: calls.append("start"))
    monkeypatch.setattr(cli, "_launch_codex", lambda args: calls.append(("launch", args)))

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert "Start the Azure token-refreshing proxy and then launch codex." in capsys.readouterr().out
    assert calls == []


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


def test_launch_codex_raises_helpful_error_when_missing(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    monkeypatch.setattr(cli.platform_support, "is_windows", lambda: False)
    monkeypatch.setattr(cli.os, "execvp", lambda command, args: (_ for _ in ()).throw(FileNotFoundError("missing")))

    with pytest.raises(RuntimeError, match="`codex` was not found on PATH"):
        cli._launch_codex(["chat"])


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


def test_restart_proxy_operates_on_shared_singleton(isolated_home, monkeypatch, load_module, capsys):
    cli = load_module("codex_azure.cli")
    calls = []

    monkeypatch.setattr(cli, "_stop_proxy_process", lambda timeout_seconds=5.0: calls.append("stop") or True)
    monkeypatch.setattr(cli, "_start_proxy", lambda: calls.append("start"))

    assert cli._restart_proxy() == 0
    assert calls == ["stop", "start"]
    assert capsys.readouterr().out == "Restarted Azure OpenAI proxy.\n"


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


def test_set_deployment_updates_managed_model_in_codex_config(isolated_home, load_module):
    config = load_module("codex_azure.config")
    resource = config.set_stored_resource("https://myresource.openai.azure.com")
    config.set_stored_deployment("gpt-5.4")
    codex_config_file = config.get_codex_config_file()
    codex_config_file.parent.mkdir(parents=True, exist_ok=True)
    codex_config_file.write_text(
        """
model = "some-other-model"
model_provider = "azure-openai-proxy"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    cli = load_module("codex_azure.cli")

    assert cli._set_deployment("gpt-5.5") == 0
    assert codex_config_file.read_text(encoding="utf-8").splitlines()[0] == 'model = "gpt-5.5"'


def test_ensure_az_login_requires_azure_cli(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    monkeypatch.setattr(cli, "_has_command", lambda command: False)

    with pytest.raises(RuntimeError, match="Azure CLI \\(`az`\\) is required"):
        cli._ensure_az_login()


def test_ensure_az_login_runs_device_code_flow_for_tty(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")
    run_calls = []

    monkeypatch.setattr(cli, "_has_command", lambda command: True)
    monkeypatch.setattr(cli, "_az_logged_in", lambda: False)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(cli.subprocess, "run", lambda args, check=True: run_calls.append((args, check)))

    cli._ensure_az_login()

    assert run_calls == [(["az", "login", "--use-device-code"], True)]


def test_ensure_az_login_requires_interactive_terminal_when_logged_out(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")

    monkeypatch.setattr(cli, "_has_command", lambda command: True)
    monkeypatch.setattr(cli, "_az_logged_in", lambda: False)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))

    with pytest.raises(RuntimeError, match="stdin is not interactive"):
        cli._ensure_az_login()


def test_ensure_az_login_reports_failed_device_code_flow(isolated_home, monkeypatch, load_module):
    cli = load_module("codex_azure.cli")

    monkeypatch.setattr(cli, "_has_command", lambda command: True)
    monkeypatch.setattr(cli, "_az_logged_in", lambda: False)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda args, check=True: (_ for _ in ()).throw(cli.subprocess.CalledProcessError(1, args)),
    )

    with pytest.raises(RuntimeError, match="Azure CLI login did not complete successfully"):
        cli._ensure_az_login()
