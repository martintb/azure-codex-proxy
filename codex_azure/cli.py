import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .app import LOCAL_AUTH_HEADER
from .config import (
    CODEX_DUMMY_API_KEY_ENV,
    CODEX_DUMMY_API_KEY_VALUE,
    CODEX_LOCAL_AUTH_ENV,
    clear_proxy_runtime_state,
    clear_stored_deployment,
    clear_stored_resource,
    ensure_local_auth_token,
    get_effective_deployment,
    get_effective_resource,
    get_preferred_proxy_endpoint,
    get_proxy_base_url,
    get_proxy_health_url,
    load_proxy_runtime_state,
    set_stored_deployment,
    set_stored_resource,
    update_codex_config,
)
from . import platform as platform_support


def _get_preferred_proxy_base_url() -> str:
    host, port = get_preferred_proxy_endpoint()
    return get_proxy_base_url(host, port)


def _get_preferred_proxy_health_url() -> str:
    host, port = get_preferred_proxy_endpoint()
    return get_proxy_health_url(host, port)


def _get_running_proxy_base_url() -> str | None:
    runtime_state = load_proxy_runtime_state()
    if runtime_state is None:
        return None
    return get_proxy_base_url(str(runtime_state["host"]), int(runtime_state["port"]))


def _update_codex_proxy_config(resource: str, proxy_base_url: str | None = None) -> Path:
    return update_codex_config(resource, proxy_base_url or _get_preferred_proxy_base_url())


def _prompt_for_resource() -> str:
    while True:
        value = input("Azure OpenAI resource URL: ").strip()
        try:
            return set_stored_resource(value)
        except ValueError as exc:
            print(f"Invalid resource: {exc}", file=sys.stderr)


def _prompt_for_deployment() -> str:
    while True:
        value = input("Azure OpenAI deployment name: ").strip()
        try:
            return set_stored_deployment(value)
        except ValueError as exc:
            print(f"Invalid deployment: {exc}", file=sys.stderr)


def _ensure_resource() -> str:
    resource = get_effective_resource()
    if resource:
        return resource
    if not sys.stdin.isatty():
        raise RuntimeError(
            "AZURE_OPENAI_RESOURCE is not set and no stored resource exists. Run 'codex-azure config set-resource'."
        )
    print("Azure OpenAI resource is not configured.", file=sys.stderr)
    resource = _prompt_for_resource()
    print(f"Stored Azure OpenAI resource: {resource}", file=sys.stderr)
    return resource


def _ensure_deployment() -> str:
    deployment = get_effective_deployment()
    if deployment:
        return deployment
    if not sys.stdin.isatty():
        raise RuntimeError(
            "AZURE_OPENAI_DEPLOYMENT is not set and no stored deployment exists. Run 'codex-azure config set-deployment'."
        )
    print("Azure OpenAI deployment is not configured.", file=sys.stderr)
    deployment = _prompt_for_deployment()
    resource = _ensure_resource()
    _update_codex_proxy_config(resource)
    print(f"Stored Azure OpenAI deployment: {deployment}", file=sys.stderr)
    return deployment


def _print_resource() -> int:
    resource = get_effective_resource()
    if not resource:
        print("No Azure OpenAI resource configured.", file=sys.stderr)
        return 1
    print(resource)
    return 0


def _print_deployment() -> int:
    deployment = get_effective_deployment()
    if not deployment:
        print("No Azure OpenAI deployment configured.", file=sys.stderr)
        return 1
    print(deployment)
    return 0


def _set_resource(value: str | None) -> int:
    if value is None:
        if not sys.stdin.isatty():
            raise RuntimeError("A resource URL is required when stdin is not interactive.")
        value = input("Azure OpenAI resource URL: ").strip()
    resource = set_stored_resource(value)
    codex_config_path = _update_codex_proxy_config(resource)
    print(f"Stored Azure OpenAI resource: {resource}")
    print(f"Updated Codex config: {codex_config_path}")
    return 0


def _set_deployment(value: str | None) -> int:
    if value is None:
        if not sys.stdin.isatty():
            raise RuntimeError("A deployment name is required when stdin is not interactive.")
        value = input("Azure OpenAI deployment name: ").strip()
    deployment = set_stored_deployment(value)
    resource = _ensure_resource()
    codex_config_path = _update_codex_proxy_config(resource)
    print(f"Stored Azure OpenAI deployment: {deployment}")
    print(f"Updated Codex config: {codex_config_path}")
    return 0


def _clear_resource() -> int:
    clear_stored_resource()
    print("Cleared stored Azure OpenAI resource.")
    return 0


def _clear_deployment() -> int:
    clear_stored_deployment()
    resource = get_effective_resource()
    if resource:
        _update_codex_proxy_config(resource)
    print("Cleared stored Azure OpenAI deployment.")
    return 0


def _get_pid_file() -> Path:
    return platform_support.get_proxy_pid_file()


def _get_log_file() -> Path:
    return platform_support.get_proxy_log_file()


def _remove_pid_files() -> None:
    clear_proxy_runtime_state()
    for path in platform_support.iter_proxy_pid_files():
        path.unlink(missing_ok=True)


def _read_pid_from_file(path: Path) -> int | None:
    platform_support.assert_secure_private_file(path)
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        path.unlink(missing_ok=True)
        return None


def _read_proxy_pid() -> tuple[int | None, Path | None]:
    runtime_state = load_proxy_runtime_state()
    if runtime_state is not None:
        return int(runtime_state["pid"]), platform_support.get_proxy_runtime_file()
    for path in platform_support.iter_proxy_pid_files():
        if not path.exists():
            continue
        pid = _read_pid_from_file(path)
        if pid is not None:
            return pid, path
    return None, None


def _get_windows_shell() -> str:
    for command in ("powershell", "pwsh"):
        if shutil.which(command):
            return command
    return "powershell"


def _get_process_command_line(pid: int) -> str | None:
    if platform_support.is_windows():
        completed = subprocess.run(
            [
                _get_windows_shell(),
                "-NoProfile",
                "-Command",
                (
                    f"$process = Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\" "
                    "-ErrorAction SilentlyContinue; "
                    "if ($null -ne $process) { $process.CommandLine }"
                ),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        command_line = completed.stdout.strip()
        return command_line or None

    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if cmdline_path.exists():
        try:
            command_line = cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        except OSError:
            return None
        return command_line or None

    completed = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    command_line = completed.stdout.strip()
    return command_line or None


def _pid_matches_proxy(pid: int) -> bool:
    command_line = _get_process_command_line(pid)
    return command_line is not None and "codex_azure.server" in command_line


def _is_process_running(pid: int) -> bool:
    if platform_support.is_windows():
        return _get_process_command_line(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_windows_process(pid: int, force: bool) -> None:
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def _stop_proxy_process(timeout_seconds: float = 5.0) -> bool:
    pid, _ = _read_proxy_pid()
    if pid is None:
        return False
    if not _pid_matches_proxy(pid):
        raise RuntimeError(f"Refusing to stop unexpected process from proxy state file: {pid}")

    if platform_support.is_windows():
        _terminate_windows_process(pid, force=False)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            _remove_pid_files()
            return False

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _is_process_running(pid):
            _remove_pid_files()
            return True
        time.sleep(0.1)

    if platform_support.is_windows():
        _terminate_windows_process(pid, force=True)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            _remove_pid_files()
            return True
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if not _is_process_running(pid):
            _remove_pid_files()
            return True
        time.sleep(0.05)

    return False


def _stop_proxy() -> int:
    stopped = _stop_proxy_process()
    if stopped:
        print("Stopped Azure OpenAI proxy.")
        return 0
    if _is_proxy_healthy():
        raise RuntimeError(
            f"Proxy is still healthy at {_get_preferred_proxy_health_url()}, but no matching PID file was usable. Stop it manually."
        )
    print("Azure OpenAI proxy is not running.")
    return 0


def _restart_proxy() -> int:
    _stop_proxy_process()
    _start_proxy()
    print("Restarted Azure OpenAI proxy.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-azure",
        description="Start the Azure token-refreshing proxy and then launch codex.",
    )
    subparsers = parser.add_subparsers(dest="command")

    config_parser = subparsers.add_parser("config", help="Manage stored configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_command")

    show_parser = config_subparsers.add_parser("show-resource", help="Print the configured resource")
    show_parser.set_defaults(handler=lambda args: _print_resource())

    show_deployment_parser = config_subparsers.add_parser("show-deployment", help="Print the configured deployment")
    show_deployment_parser.set_defaults(handler=lambda args: _print_deployment())

    set_parser = config_subparsers.add_parser("set-resource", help="Set the configured resource")
    set_parser.add_argument("resource", nargs="?", help="Azure OpenAI resource URL")
    set_parser.set_defaults(handler=lambda args: _set_resource(args.resource))

    set_deployment_parser = config_subparsers.add_parser("set-deployment", help="Set the configured deployment")
    set_deployment_parser.add_argument("deployment", nargs="?", help="Azure OpenAI deployment name")
    set_deployment_parser.set_defaults(handler=lambda args: _set_deployment(args.deployment))

    clear_parser = config_subparsers.add_parser("clear-resource", help="Clear the stored resource")
    clear_parser.set_defaults(handler=lambda args: _clear_resource())

    clear_deployment_parser = config_subparsers.add_parser("clear-deployment", help="Clear the stored deployment")
    clear_deployment_parser.set_defaults(handler=lambda args: _clear_deployment())

    stop_parser = subparsers.add_parser("stop-proxy", help="Stop the background proxy")
    stop_parser.set_defaults(handler=lambda args: _stop_proxy())

    restart_parser = subparsers.add_parser("restart-proxy", help="Restart the background proxy")
    restart_parser.set_defaults(handler=lambda args: _restart_proxy())

    return parser


def _has_command(command: str) -> bool:
    return shutil.which(command) is not None


def _az_logged_in() -> bool:
    return subprocess.run(
        ["az", "account", "show"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _ensure_az_login() -> None:
    if _has_command("az") and not _az_logged_in():
        print("Azure CLI detected but not logged in; running az login...", file=sys.stderr)
        subprocess.run(["az", "login"], check=True)


def _clear_stale_runtime_proxy_state() -> None:
    runtime_state = load_proxy_runtime_state()
    if runtime_state is None:
        return
    if not _is_process_running(int(runtime_state["pid"])):
        clear_proxy_runtime_state()


def _is_proxy_healthy(host: str | None = None, port: int | None = None) -> bool:
    if host is None or port is None:
        host, port = get_preferred_proxy_endpoint()
    try:
        response = httpx.get(
            get_proxy_health_url(host, port),
            timeout=1.0,
            headers={LOCAL_AUTH_HEADER: ensure_local_auth_token()},
        )
        return response.is_success
    except httpx.HTTPError:
        return False


def _start_proxy() -> None:
    _clear_stale_runtime_proxy_state()
    if _is_proxy_healthy():
        proxy_base_url = _get_running_proxy_base_url() or _get_preferred_proxy_base_url()
        resource = get_effective_resource()
        if resource:
            _update_codex_proxy_config(resource, proxy_base_url)
        return

    resource = _ensure_resource()
    _ensure_deployment()
    _ensure_az_login()
    platform_support.ensure_private_dir(_get_pid_file().parent)

    print("Starting Azure OpenAI proxy...", file=sys.stderr)
    with platform_support.open_private_append_binary(_get_log_file()) as log_file:
        popen_kwargs = {
            "stdout": log_file,
            "stderr": subprocess.STDOUT,
            "env": {**os.environ, CODEX_LOCAL_AUTH_ENV: ensure_local_auth_token()},
        }
        if platform_support.is_windows():
            popen_kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            [sys.executable, "-m", "codex_azure.server"],
            **popen_kwargs,
        )

    platform_support.write_private_text(_get_pid_file(), f"{process.pid}\n")
    for path in platform_support.iter_proxy_pid_files():
        if path != _get_pid_file():
            path.unlink(missing_ok=True)

    for _ in range(60):
        runtime_state = load_proxy_runtime_state()
        if runtime_state is not None and int(runtime_state["pid"]) == process.pid:
            host = str(runtime_state["host"])
            port = int(runtime_state["port"])
            if _is_proxy_healthy(host, port):
                _update_codex_proxy_config(resource, get_proxy_base_url(host, port))
                return
        if process.poll() is not None:
            break
        time.sleep(0.25)

    if process.poll() is None:
        raise RuntimeError(f"Proxy failed to start. Check {_get_log_file()}")
    raise RuntimeError(f"Proxy exited before it became healthy. Check {_get_log_file()}")


def _launch_codex(args: list[str]) -> None:
    if platform_support.is_windows():
        completed = subprocess.run(["codex", *args], check=False)
        raise SystemExit(completed.returncode)
    os.execvp("codex", ["codex", *args])


def main() -> None:
    parser = _build_parser()
    args, remaining = parser.parse_known_args()

    if getattr(args, "handler", None) is not None:
        raise SystemExit(args.handler(args))

    if args.command is not None:
        parser.print_help(sys.stderr)
        raise SystemExit(2)

    _start_proxy()
    os.environ.setdefault(CODEX_DUMMY_API_KEY_ENV, CODEX_DUMMY_API_KEY_VALUE)
    os.environ.setdefault(CODEX_LOCAL_AUTH_ENV, ensure_local_auth_token())
    _launch_codex(remaining)


if __name__ == "__main__":
    main()
