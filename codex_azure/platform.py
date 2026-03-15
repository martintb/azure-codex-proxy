import os
import stat
from pathlib import Path

from platformdirs import PlatformDirs


APP_NAME = "codex-azure"
LEGACY_PROXY_BASENAME = "azure-openai-proxy"
OWNER_ONLY_FILE_MODE = 0o600
OWNER_ONLY_DIR_MODE = 0o700


def is_windows() -> bool:
    return os.name == "nt"


def _platform_dirs() -> PlatformDirs:
    return PlatformDirs(APP_NAME, appauthor=False)


def get_proxy_config_dir() -> Path:
    return Path(_platform_dirs().user_config_dir)


def get_proxy_config_file() -> Path:
    return get_proxy_config_dir() / "config.json"


def get_proxy_cache_dir() -> Path:
    return Path(_platform_dirs().user_cache_dir)


def get_proxy_pid_file() -> Path:
    return get_proxy_cache_dir() / f"{LEGACY_PROXY_BASENAME}.pid"


def get_proxy_log_file() -> Path:
    return get_proxy_cache_dir() / f"{LEGACY_PROXY_BASENAME}.log"


def get_legacy_proxy_config_file() -> Path:
    return Path.home() / ".config" / APP_NAME / "config.json"


def get_legacy_proxy_pid_file() -> Path:
    return Path.home() / ".cache" / f"{LEGACY_PROXY_BASENAME}.pid"


def get_legacy_proxy_log_file() -> Path:
    return Path.home() / ".cache" / f"{LEGACY_PROXY_BASENAME}.log"


def iter_proxy_pid_files() -> tuple[Path, ...]:
    paths: list[Path] = []
    for path in (get_proxy_pid_file(), get_legacy_proxy_pid_file()):
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def get_codex_config_dir() -> Path:
    return Path.home() / ".codex"


def get_codex_config_file() -> Path:
    return get_codex_config_dir() / "config.toml"


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if is_windows():
        return
    try:
        os.chmod(path, OWNER_ONLY_DIR_MODE)
    except PermissionError:
        pass


def write_private_text(path: Path, content: str) -> None:
    ensure_private_dir(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, OWNER_ONLY_FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    if is_windows():
        return
    try:
        os.chmod(path, OWNER_ONLY_FILE_MODE)
    except PermissionError:
        pass


def open_private_append_binary(path: Path):
    ensure_private_dir(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(path, flags, OWNER_ONLY_FILE_MODE)
    handle = os.fdopen(fd, "ab")
    if is_windows():
        return handle
    try:
        os.chmod(path, OWNER_ONLY_FILE_MODE)
    except PermissionError:
        pass
    return handle


def assert_secure_private_file(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        raise RuntimeError(f"Refusing to use symlink for sensitive file: {path}")
    if is_windows():
        return
    stat_result = path.stat()
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return
    if stat_result.st_uid != getuid():
        raise RuntimeError(f"Refusing to use insecure file not owned by current user: {path}")
    if stat_result.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError(f"Refusing to use writable-by-others file: {path}")
