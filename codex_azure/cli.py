import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .app import PROXY_HOST, PROXY_PORT
from .config import clear_stored_resource, get_effective_resource, set_stored_resource


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


def _print_resource() -> int:
    resource = get_effective_resource()
    if not resource:
        print("No Azure OpenAI resource configured.", file=sys.stderr)
        return 1
    print(resource)
    return 0


def _set_resource(value: str | None) -> int:
    if value is None:
        if not sys.stdin.isatty():
            raise RuntimeError("A resource URL is required when stdin is not interactive.")
        value = input("Azure OpenAI resource URL: ").strip()
    resource = set_stored_resource(value)
    print(f"Stored Azure OpenAI resource: {resource}")
    return 0


def _clear_resource() -> int:
    clear_stored_resource()
    print("Cleared stored Azure OpenAI resource.")
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

    set_parser = config_subparsers.add_parser("set-resource", help="Set the configured resource")
    set_parser.add_argument("resource", nargs="?", help="Azure OpenAI resource URL")
    set_parser.set_defaults(handler=lambda args: _set_resource(args.resource))

    clear_parser = config_subparsers.add_parser("clear-resource", help="Clear the stored resource")
    clear_parser.set_defaults(handler=lambda args: _clear_resource())

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
        response = httpx.get(f"{PROXY_URL}/healthz", timeout=1.0)
        return response.is_success
    except httpx.HTTPError:
        return False


def _start_proxy() -> None:
    if _is_proxy_healthy():
        return

    _ensure_resource()
    _ensure_az_login()
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("Starting Azure OpenAI proxy...", file=sys.stderr)
    with LOG_FILE.open("ab") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "codex_azure.server"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    PID_FILE.write_text(f"{process.pid}\n")

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
    os.execvp("codex", ["codex", *remaining])