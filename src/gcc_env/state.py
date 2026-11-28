"""Saved shell state for `ge activate` / `ge deactivate`.

A process cannot modify its parent shell's environment.  The shell hook calls
`ge activate`, which snapshots the relevant variables here, then emits shell
code for the hook to eval.  `ge deactivate` reads the snapshot, emits restore
code, and removes the snapshot.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

from .config import Config
from .errors import EnvError
from .util import write_json

# Variables that gcc-env may set while an environment is active.
MANAGED_VARS = (
    "PATH",
    "LD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "LIBRARY_PATH",
    "CMAKE_PREFIX_PATH",
    "PKG_CONFIG_PATH",
    "CC",
    "CXX",
    "AR",
    "AS",
    "LD",
    "NM",
    "RANLIB",
    "OBJDUMP",
    "READELF",
    "STRIP",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "CONDA_BUILD_SYSROOT",
    "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT",
    "GE_ROOT",
    "GE_ENV",
    "GE_GCC_VERSION",
    "GE_RUNTIME",
    "GE_SESSION",
    "GE_ACTIVE",
)

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def current_session() -> str | None:
    value = os.environ.get("GE_SESSION")
    if value and _SESSION_RE.match(value):
        return value
    return None


def ensure_session() -> str:
    existing = current_session()
    if existing:
        return existing
    return uuid.uuid4().hex


def state_path(config: Config, session: str) -> Path:
    if not _SESSION_RE.match(session):
        raise EnvError("invalid gcc-env shell session id")
    return config.state_dir / f"{session}.json"


def snapshot() -> dict[str, str]:
    return {name: os.environ[name] for name in MANAGED_VARS if name in os.environ}


def save_snapshot(config: Config, session: str, env_name: str) -> dict:
    """Persist the pre-activation environment once per shell session."""
    config.ensure_layout()
    path = state_path(config, session)
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("env"), dict):
                return existing
        except (OSError, json.JSONDecodeError):
            pass

    data = {
        "schema": 1,
        "session": session,
        "environment": env_name,
        "ppid": os.getppid(),
        "created_at": time.time(),
        "env": snapshot(),
    }
    write_json(path, data)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    _prune_states(config)
    return data


def load_state(config: Config, session: str | None = None) -> dict | None:
    if session is None:
        session = current_session()
    if session is None:
        return _find_by_parent(config)
    path = state_path(config, session)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("env"), dict):
        return None
    return data


def pop_state(config: Config, session: str | None = None) -> dict | None:
    if session is None:
        session = current_session()
    if session is None:
        return _pop_by_parent(config)
    data = load_state(config, session)
    if data is None:
        if session:
            return _pop_by_parent(config)
        return None
    try:
        state_path(config, session).unlink()
    except OSError:
        pass
    return data


def _iter_states(config: Config):
    if not config.state_dir.is_dir():
        return
    for path in config.state_dir.glob("*.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("env"), dict):
            yield path, data


def _find_by_parent(config: Config) -> dict | None:
    parent = os.getppid()
    candidates = [
        (float(data.get("created_at", 0)), data)
        for _path, data in _iter_states(config)
        if int(data.get("ppid", -1)) == parent
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _pop_by_parent(config: Config) -> dict | None:
    parent = os.getppid()
    newest: tuple[float, Path, dict] | None = None
    for path, data in _iter_states(config):
        if int(data.get("ppid", -1)) != parent:
            continue
        candidate = (float(data.get("created_at", 0)), path, data)
        if newest is None or candidate[0] > newest[0]:
            newest = candidate
    if newest is None:
        return None
    try:
        newest[1].unlink()
    except OSError:
        pass
    return newest[2]


def _prune_states(config: Config) -> None:
    cutoff = time.time() - 7 * 86400
    for path, data in list(_iter_states(config)):
        try:
            created = float(data.get("created_at", path.stat().st_mtime))
        except (OSError, TypeError, ValueError):
            created = 0
        if created < cutoff:
            try:
                path.unlink()
            except OSError:
                pass
