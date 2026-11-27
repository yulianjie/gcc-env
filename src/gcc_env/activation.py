"""Build environment variables and shell activation snippets."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from .envs import Environment
from .util import hex_quote_ps, shell_basename

# Variables that describe the toolchain, in a stable display order.
_EXPORT_ORDER = [
    "GE_ROOT",
    "GE_ENV",
    "GE_GCC_VERSION",
    "GE_RUNTIME",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT",
    "CC",
    "CXX",
    "CPP",
    "AR",
    "AS",
    "LD",
    "NM",
    "RANLIB",
    "OBJDUMP",
    "READELF",
    "STRIP",
    "CMAKE_PREFIX_PATH",
    "PKG_CONFIG_PATH",
    "LIBRARY_PATH",
    "LD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "CONDA_BUILD_SYSROOT",
    "PATH",
]


def _dedupe_paths(entries: list[str], *, windows: bool) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not entry:
            continue
        key = os.path.normcase(os.path.normpath(entry)) if windows else entry
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _prepend(existing: str | None, entries: list[str], *, windows: bool) -> str:
    parts = [str(entry) for entry in entries if entry]
    if existing:
        parts.extend(existing.split(os.pathsep))
    return os.pathsep.join(_dedupe_paths(parts, windows=windows))


def _is_within(path: str, root: Path) -> bool:
    try:
        candidate = os.path.abspath(path)
        base = os.path.abspath(str(root))
        return os.path.commonpath([candidate, base]) == base
    except (OSError, ValueError):
        return False


def _strip_previous_env(base_env: dict[str, str]) -> None:
    """Remove paths from a previously activated ge environment.

    This makes repeated `eval "$(ge activate ...)"` switch cleanly instead of
    leaving an older toolchain behind the new one in PATH/LD_LIBRARY_PATH.
    """
    old_name = base_env.get("GE_ENV")
    old_root = base_env.get("GE_ROOT")
    if not old_name or not old_root:
        return
    old_env_dir = Path(old_root) / "envs" / old_name
    for variable in (
        "PATH",
        "LD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "LIBRARY_PATH",
        "CMAKE_PREFIX_PATH",
        "PKG_CONFIG_PATH",
    ):
        value = base_env.get(variable)
        if not value:
            continue
        parts = value.split(os.pathsep)
        kept = [part for part in parts if part and not _is_within(part, old_env_dir)]
        if kept:
            base_env[variable] = os.pathsep.join(kept)
        else:
            base_env.pop(variable, None)
    base_env.pop("VIRTUAL_ENV", None)
    base_env.pop("VIRTUAL_ENV_PROMPT", None)


def toolchain_env(env: Environment, *, base: dict[str, str] | None = None) -> dict[str, str]:
    base_env = dict(os.environ if base is None else base)
    windows = env.config.platform.is_windows
    prefix = env.conda_prefix
    _strip_previous_env(base_env)

    bin_dirs: list[Path] = []
    if env.python_enabled:
        venv_bin = env.venv_bin()
        if venv_bin:
            bin_dirs.append(venv_bin)
    if windows:
        bin_dirs.extend(
            [
                prefix / "Library" / "bin",
                prefix / "Library" / "mingw-w64" / "bin",
                prefix / "Library" / "usr" / "bin",
                prefix / "bin",
                prefix / "Scripts",
            ]
        )
    else:
        bin_dirs.append(prefix / "bin")
    bin_dirs.append(env.config.bin_dir)

    path_value = _prepend(
        base_env.get("PATH"),
        [str(path) for path in bin_dirs if path.is_dir()],
        windows=windows,
    )
    base_env["PATH"] = path_value

    if windows:
        prefix_paths = [prefix / "Library" / "lib", prefix / "Library" / "bin"]
    else:
        prefix_paths = [prefix / "lib", prefix / "lib64"]

    cma_prefix = [str(prefix)]
    if env.sysroot:
        cma_prefix.append(str(env.sysroot / "usr"))
    base_env["CMAKE_PREFIX_PATH"] = _prepend(
        base_env.get("CMAKE_PREFIX_PATH"), cma_prefix, windows=windows
    )

    pkgconfig = [str(prefix / "lib" / "pkgconfig"), str(prefix / "share" / "pkgconfig")]
    if env.sysroot:
        pkgconfig.append(str(env.sysroot / "usr" / "lib" / "pkgconfig"))
    base_env["PKG_CONFIG_PATH"] = _prepend(
        base_env.get("PKG_CONFIG_PATH"), pkgconfig, windows=windows
    )

    library_path = [str(path) for path in prefix_paths if path.is_dir()]
    base_env["LIBRARY_PATH"] = _prepend(base_env.get("LIBRARY_PATH"), library_path, windows=windows)
    if not windows:
        base_env["LD_LIBRARY_PATH"] = _prepend(
            base_env.get("LD_LIBRARY_PATH"), library_path, windows=windows
        )
    if env.config.platform.is_macos:
        base_env["DYLD_FALLBACK_LIBRARY_PATH"] = _prepend(
            base_env.get("DYLD_FALLBACK_LIBRARY_PATH"), library_path, windows=windows
        )

    base_env["GE_ROOT"] = str(env.config.root)
    base_env["GE_ENV"] = env.name
    base_env["GE_GCC_VERSION"] = env.gcc_version
    base_env["GE_RUNTIME"] = env.runtime
    base_env["CONDA_PREFIX"] = str(prefix)
    base_env["CONDA_DEFAULT_ENV"] = env.name
    base_env["CC"] = str(env.cc)
    base_env["CXX"] = str(env.cxx)
    for name, path in env.gcc_tools.items():
        key = name.upper()
        if key in {"AR", "AS", "LD", "NM", "RANLIB", "OBJDUMP", "READELF", "STRIP"}:
            base_env[key] = str(path)
    if env.sysroot and env.config.platform.is_linux:
        base_env["CONDA_BUILD_SYSROOT"] = str(env.sysroot)

    if env.python_enabled:
        base_env["VIRTUAL_ENV"] = str(env.venv_dir)
        base_env["VIRTUAL_ENV_PROMPT"] = f"({env.name}) "
        base_env.pop("PYTHONHOME", None)
    else:
        base_env.pop("VIRTUAL_ENV", None)
        base_env.pop("VIRTUAL_ENV_PROMPT", None)
    return base_env


def _posix_quote(value: str) -> str:
    return shlex.quote(value)


def _fish_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def activation_script(
    env: Environment,
    *,
    shell: str,
    base: dict[str, str] | None = None,
) -> str:
    values = toolchain_env(env, base=base)
    shell = shell_basename(shell)
    if shell in {"sh", "dash", "ksh"}:
        shell = "bash"

    if shell in {"bash", "zsh"}:
        lines = [f"# gcc-env: {env.name} (GCC {env.gcc_version})"]
        for key in _EXPORT_ORDER:
            if key in values:
                lines.append(f"export {key}={_posix_quote(values[key])}")
            elif key in {"VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT"}:
                lines.append(f"unset {key}")
        lines.append("hash -r 2>/dev/null || true")
        return "\n".join(lines) + "\n"

    if shell == "fish":
        lines = [f"# gcc-env: {env.name} (GCC {env.gcc_version})"]
        for key in _EXPORT_ORDER:
            if key not in values:
                if key in {"VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT"}:
                    lines.append(f"set -e {key}")
                continue
            if key == "PATH":
                parts = values[key].split(os.pathsep)
                joined = " ".join(_fish_quote(part) for part in parts if part)
                lines.append(f"set -gx PATH {joined}")
            else:
                lines.append(f"set -gx {key} {_fish_quote(values[key])}")
        return "\n".join(lines) + "\n"

    if shell in {"powershell", "pwsh"}:
        lines = [f"# gcc-env: {env.name} (GCC {env.gcc_version})"]
        for key in _EXPORT_ORDER:
            if key in values:
                lines.append(f"$env:{key} = {hex_quote_ps(values[key])}")
            elif key in {"VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT"}:
                lines.append(f"Remove-Item Env:{key} -ErrorAction SilentlyContinue")
        return "\n".join(lines) + "\n"

    if shell in {"cmd", "cmd.exe"}:
        lines = [f"@rem gcc-env: {env.name} (GCC {env.gcc_version})"]
        for key in _EXPORT_ORDER:
            if key in values:
                lines.append(f'set "{key}={values[key]}"')
            elif key in {"VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT"}:
                lines.append(f'set "{key}="')
        return "\r\n".join(lines) + "\r\n"

    raise ValueError(f"unsupported shell: {shell}")


def shell_environment(env: Environment) -> dict[str, str]:
    """Environment for ge run/ge shell/compiler wrappers."""
    return toolchain_env(env)


def prompt_label(env: Environment) -> str:
    return f"ge:{env.name} gcc-{env.gcc_version}"
