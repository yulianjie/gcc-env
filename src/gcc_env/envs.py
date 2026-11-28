"""Environment creation, metadata, and selection."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .bootstrap import Bootstrap
from .compiler import Toolchain, compiler_specs, default_env_name, discover_toolchain
from .conda import Conda
from .config import Config
from .errors import EnvError, UsageError
from .util import FileLock, info, read_json, rmtree, step, success, warn, write_json
from .uvtools import Uv

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_GCC_SPEC_RE = re.compile(r"^(?:latest|\d+(?:\.\d+){0,2})$")
_SYSROOT_RE = re.compile(r"^\d+(?:\.\d+){1,2}$")


def validate_env_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise UsageError(
            f"invalid environment name: {name!r}",
            hint="Use letters, digits, '.', '_' and '-', starting with a letter or digit.",
        )
    if name.endswith((".", " ")):
        raise UsageError("environment names cannot end with a dot or space")
    if name.lower() in _WINDOWS_RESERVED:
        raise UsageError(f"{name!r} is a reserved name on Windows")
    return name


def normalize_gcc_spec(value: str) -> str:
    spec = value.strip()
    if spec.startswith("="):
        spec = spec[1:]
    if not _GCC_SPEC_RE.match(spec):
        raise UsageError(
            f"invalid GCC version {value!r}",
            hint="Examples: 13, 13.3, 13.3.0, latest.",
        )
    return spec


@dataclass
class Environment:
    config: Config
    name: str
    root: Path
    meta: dict

    # -- paths --------------------------------------------------------
    @property
    def conda_prefix(self) -> Path:
        return self.root / "conda"

    @property
    def venv_dir(self) -> Path:
        return self.root / "venv"

    @property
    def meta_path(self) -> Path:
        return self.root / "env.json"

    @property
    def shim_dir(self) -> Path:
        return self.root / "bin"

    def _abs(self, value: str | None) -> Path | None:
        if not value:
            return None
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    @property
    def cc(self) -> Path:
        path = self._abs(self.meta["gcc"]["cc"])
        assert path is not None
        return path

    @property
    def cxx(self) -> Path:
        path = self._abs(self.meta["gcc"]["cxx"])
        assert path is not None
        return path

    @property
    def gcc_version(self) -> str:
        return str(self.meta["gcc"]["version"])

    @property
    def gcc_target(self) -> str:
        return str(self.meta["gcc"].get("target", ""))

    @property
    def requested_gcc(self) -> str:
        return str(self.meta["gcc"].get("requested", ""))

    @property
    def sysroot_version(self) -> str | None:
        value = self.meta.get("sysroot", {}).get("version")
        return str(value) if value else None

    @property
    def sysroot(self) -> Path | None:
        return self._abs(self.meta.get("sysroot", {}).get("path"))

    @property
    def runtime(self) -> str:
        return str(self.meta.get("runtime", self.config.defaults.runtime))

    @property
    def python_enabled(self) -> bool:
        return bool(self.meta.get("python", {}).get("enabled"))

    @property
    def python_path(self) -> Path | None:
        if not self.python_enabled:
            return None
        return Uv.python(self.venv_dir)

    @property
    def python_version(self) -> str | None:
        if not self.python_enabled:
            return None
        value = self.meta.get("python", {}).get("version")
        return str(value) if value else None

    @property
    def tools(self) -> list[str]:
        value = self.meta.get("python", {}).get("tools", [])
        return [str(item) for item in value] if isinstance(value, list) else []

    @property
    def packages(self) -> dict[str, str]:
        value = self.meta.get("conda", {}).get("packages", {})
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}

    @property
    def gcc_tools(self) -> dict[str, Path]:
        raw = self.meta.get("gcc", {}).get("tools", {})
        tools: dict[str, Path] = {}
        if isinstance(raw, dict):
            for name, value in raw.items():
                path = self._abs(str(value))
                if path is not None:
                    tools[str(name)] = path
        return tools

    def toolchain(self) -> Toolchain:
        return Toolchain(
            cc=self.cc,
            cxx=self.cxx,
            version=self.gcc_version,
            target=self.gcc_target,
            sysroot=self.sysroot,
        )

    def venv_bin(self) -> Path | None:
        return Uv.bin_dir(self.venv_dir) if self.python_enabled else None

    def ensure_shims(self) -> Path:
        """Expose bare cc/gcc/c++/g++ commands for the active environment."""
        import shlex
        import stat

        self.shim_dir.mkdir(parents=True, exist_ok=True)
        commands = {
            "c": "cc",
            "cc": "cc",
            "gcc": "cc",
            "c++": "c++",
            "g++": "c++",
        }
        for name, kind in commands.items():
            posix = (
                "#!/bin/sh\n"
                "# Generated by gcc-env; delegates to the active toolchain.\n"
                f'exec ge {kind} --env {shlex.quote(self.name)} -- "$@"\n'
            )
            path = self.shim_dir / name
            path.write_text(posix, encoding="utf-8", newline="\n")
            if os.name != "nt":
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if self.config.platform.is_windows:
                batch = (
                    f'@echo off\r\nge {kind} --env "{self.name}" -- %*\r\nexit /b %ERRORLEVEL%\r\n'
                )
                (self.shim_dir / f"{name}.cmd").write_text(batch, encoding="utf-8", newline="")
        return self.shim_dir

    def summary(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.root),
            "platform": self.meta.get("platform"),
            "gcc": self.gcc_version,
            "gcc_requested": self.requested_gcc,
            "target": self.gcc_target,
            "sysroot": self.sysroot_version,
            "runtime": self.runtime,
            "python": self.python_version,
            "tools": self.tools,
            "created_at": self.meta.get("created_at"),
        }

    def to_json(self) -> dict:
        return self.summary()

    def __str__(self) -> str:
        return self.name


class EnvManager:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.bootstrap = Bootstrap(config)

    # -- discovery ----------------------------------------------------
    def env_root(self, name: str) -> Path:
        return self.config.env_dir(validate_env_name(name))

    def load(self, name: str) -> Environment:
        root = self.env_root(name)
        if not root.is_dir():
            raise EnvError(
                f"environment not found: {name}",
                hint="Run 'ge list' to see installed environments.",
            )
        meta = read_json(root / "env.json")
        env = Environment(self.config, name, root, meta)
        recorded_platform = meta.get("platform")
        if recorded_platform and recorded_platform != self.config.platform.conda_subdir:
            raise EnvError(
                f"environment {name!r} was created for {recorded_platform}, "
                f"but this is {self.config.platform.conda_subdir}",
                hint="Conda toolchains are platform-specific. Create a separate environment.",
            )
        return env

    def list(self) -> list[Environment]:
        envs: list[Environment] = []
        if not self.config.envs_dir.is_dir():
            return envs
        for root in sorted(self.config.envs_dir.iterdir(), key=lambda p: p.name.lower()):
            if not root.is_dir() or root.name.startswith("."):
                continue
            if not (root / "env.json").is_file():
                continue
            try:
                envs.append(self.load(root.name))
            except EnvError:
                continue
        return envs

    # -- selection ----------------------------------------------------
    def current(self) -> Environment:
        name = ""
        try:
            name = self.config.current_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        if name:
            try:
                return self.load(name)
            except EnvError:
                warn(f"current environment {name!r} no longer exists; ignoring it")
        envs = self.list()
        if len(envs) == 1:
            return envs[0]
        if not envs:
            raise EnvError(
                "no environment selected",
                hint="Run 'ge setup' or 'ge create gcc14 --use'.",
            )
        raise EnvError(
            "no environment selected",
            hint="Run 'ge use NAME' or pass an environment name.",
        )

    def use(self, name: str) -> None:
        env = self.load(name)
        self.config.ensure_layout()
        with FileLock(self.config.lock_path):
            self.config.current_path.write_text(env.name + "\n", encoding="utf-8")
        success(f"current environment: {env.name}")

    def clear_current(self, name: str) -> None:
        try:
            current = self.config.current_path.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if current == name:
            try:
                self.config.current_path.unlink()
            except OSError:
                pass

    # -- creation -----------------------------------------------------
    def create(
        self,
        name: str | None = None,
        *,
        gcc: str | None = None,
        sysroot: str | None | object = ...,
        python: str | None = None,
        tools: tuple[str, ...] | None = None,
        runtime: str | None = None,
        with_python: bool = True,
        use: bool = False,
        force: bool = False,
    ) -> Environment:
        gcc_spec = normalize_gcc_spec(gcc or self.config.defaults.gcc)
        env_name = validate_env_name(name or default_env_name(gcc_spec))
        runtime_value = runtime or self.config.defaults.runtime
        if runtime_value not in {"static", "shared", "bundle"}:
            raise UsageError("runtime must be static, shared, or bundle")
        python_value = python or self.config.defaults.python
        tools_value = tuple(tools) if tools is not None else self.config.defaults.tools

        sysroot_value = self.config.defaults.sysroot if sysroot is ... else sysroot
        if self.config.platform.is_linux:
            if sysroot_value in (None, "", "none"):
                sysroot_value = None
            elif not _SYSROOT_RE.match(str(sysroot_value)):
                raise UsageError(f"invalid glibc/sysroot version: {sysroot_value!r}")
        else:
            if sysroot_value not in (None, "", "none"):
                raise UsageError(
                    "the --sysroot option only applies to Linux",
                    hint="macOS and Windows use their native SDK / MinGW sysroot.",
                )
            sysroot_value = None

        self.config.ensure_layout()
        env_root = self.config.env_dir(env_name)
        with FileLock(self.config.lock_path):
            if env_root.exists():
                if not force:
                    raise EnvError(
                        f"environment {env_name!r} already exists",
                        hint=f"Use 'ge create {env_name} --force' to recreate it.",
                    )
                warn(f"removing existing environment {env_name}")
                rmtree(env_root)

            self.config.envs_dir.mkdir(parents=True, exist_ok=True)
            env_root.mkdir(parents=True)
            try:
                micromamba = self.bootstrap.micromamba()
                conda = Conda(self.config, micromamba)
                specs = compiler_specs(
                    self.config.platform, gcc_spec, str(sysroot_value) if sysroot_value else None
                )
                conda.create(env_root / "conda", specs)
                step("discovering compiler...")
                toolchain = discover_toolchain(env_root / "conda", self.config.platform)
                info(toolchain.describe())

                python_info: dict = {"enabled": False}
                if with_python:
                    uv_path = self.bootstrap.uv()
                    uv = Uv(self.config, uv_path)
                    uv.venv(env_root / "venv", python_value)
                    uv.pip_install(env_root / "venv", list(tools_value))
                    python_info = {
                        "enabled": True,
                        "requested": python_value,
                        "version": Uv.python_version(Uv.python(env_root / "venv")),
                        "venv": "venv",
                        "tools": list(tools_value),
                        "uv": uv.version(),
                    }

                sysroot_path = toolchain.sysroot
                conda_packages = conda.package_versions(env_root / "conda")
                meta = {
                    "schema": 1,
                    "name": env_name,
                    "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "platform": self.config.platform.conda_subdir,
                    "gcc": {
                        "requested": gcc_spec,
                        "version": toolchain.version,
                        "target": toolchain.target,
                        "cc": _relative(toolchain.cc, env_root),
                        "cxx": _relative(toolchain.cxx, env_root),
                        "tools": {
                            name: _relative(path, env_root)
                            for name, path in toolchain.tools.items()
                        },
                    },
                    "sysroot": {
                        "version": str(sysroot_value) if sysroot_value else None,
                        "path": _relative(sysroot_path, env_root) if sysroot_path else None,
                    },
                    "conda": {
                        "packages": conda_packages,
                        "channels": list(self.config.channels),
                    },
                    "python": python_info,
                    "runtime": runtime_value,
                }
                write_json(env_root / "env.json", meta)
                env = Environment(self.config, env_name, env_root, meta)
                env.ensure_shims()
            except BaseException:
                rmtree(env_root)
                raise

            if use:
                self.config.current_path.write_text(env_name + "\n", encoding="utf-8")
            success(
                f"created {env_name}: {toolchain.describe()}"
                + (f", Python {python_info.get('version')}" if with_python else "")
            )
            if not use:
                info(f'activate it with: eval "$(ge activate {env_name})"')
                info(f"or make it default with: ge use {env_name}")
            return env

    # -- removal ------------------------------------------------------
    def remove(self, name: str, *, yes: bool = False) -> None:
        env = self.load(name)
        if not yes:
            from .util import confirm

            if not confirm(f"Remove environment {env.name!r} ({env.root})?"):
                info("cancelled")
                return
        with FileLock(self.config.lock_path):
            rmtree(env.root)
            self.clear_current(env.name)
        success(f"removed environment {env.name}")


def _relative(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:
        return str(path)
