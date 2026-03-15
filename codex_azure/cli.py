import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .app import LOCAL_AUTH_HEADER, PROXY_HOST, PROXY_PORT
from .config import (
    CODEX_DUMMY_API_KEY_ENV,
    CODEX_DUMMY_API_KEY_VALUE,
    CODEX_LOCAL_AUTH_ENV,
    clear_stored_deployment,
    clear_stored_resource,
    ensure_local_auth_token,
    get_effective_deployment,
    get_effective_resource,
    set_stored_deployment,
    set_stored_resource,
    update_codex_config,
)


PROXY_URL = f"http://{PROXY_HOST}:{PROXY_PORT}"
PID_FILE = Path.home() / ".cache" / "azure-openai-proxy.pid"
LOG_FILE = Path.home() / ".cache" / "azure-openai-proxy.log"


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
    update_codex_config(resource)
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
    codex_config_path = update_codex_config(resource)
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
    codex_config_path = update_codex_config(resource)
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
        update_codex_config(resource)
    print("Cleared stored Azure OpenAI deployment.")
    return 0


def _read_proxy_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        stat_result = PID_FILE.stat()
        if stat_result.st_uid != os.getuid() or stat_result.st_mode & 0o022:
            raise RuntimeError(f"Refusing to use insecure PID file: {PID_FILE}")
        return int(PID_FILE.read_text().strip())
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        return None


def _remove_pid_file() -> None:
    PID_FILE.unlink(missing_ok=True)


def _pid_matches_proxy(pid: int) -> bool:
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if cmdline_path.exists():
        try:
            cmdline = cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        except OSError:
            return False
        return "codex_azure.server" in cmdline
    return True


def _stop_proxy_process(timeout_seconds: float = 5.0) -> bool:
    pid = _read_proxy_pid()
    if pid is None:
        return False
    if not _pid_matches_proxy(pid):
        raise RuntimeError(f"Refusing to stop unexpected process from PID file: {pid}")

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _remove_pid_file()
        return False

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            _remove_pid_file()
            return True
        time.sleep(0.1)

    os.kill(pid, signal.SIGKILL)
    deadline = time.time() + 1.0
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            _remove_pid_file()
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
            f"Proxy is still healthy at {PROXY_URL}, but no matching PID file was usable. Stop it manually."
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
    return subprocess.run(
        ["/usr/bin/env", "bash", "-lc", f"command -v {command}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


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


def _is_proxy_healthy() -> bool:
    try:
        response = httpx.get(
            f"{PROXY_URL}/healthz",
            timeout=1.0,
            headers={LOCAL_AUTH_HEADER: ensure_local_auth_token()},
        )
        return response.is_success
    except httpx.HTTPError:
        return False


def _start_proxy() -> None:
    if _is_proxy_healthy():
        return

    _ensure_resource()
    _ensure_deployment()
    _ensure_az_login()
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("Starting Azure OpenAI proxy...", file=sys.stderr)
    with LOG_FILE.open("ab") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "codex_azure.server"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, CODEX_LOCAL_AUTH_ENV: ensure_local_auth_token()},
        )

    os.chmod(PID_FILE.parent, 0o700)
    PID_FILE.write_text(f"{process.pid}\n")
    os.chmod(PID_FILE, 0o600)
    if not LOG_FILE.exists():
        LOG_FILE.touch(mode=0o600)
    else:
        os.chmod(LOG_FILE, 0o600)

    for _ in range(60):
        if _is_proxy_healthy():
            return
        time.sleep(0.25)

    raise RuntimeError(f"Proxy failed to start. Check {LOG_FILE}")


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
    os.execvp("codex", ["codex", *remaining])


if __name__ == "__main__":
    main()
