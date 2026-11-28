"""Filesystem layout and user configuration for gcc-env."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import UsageError
from .platforms import Platform, current_platform


@dataclass
class Defaults:
    gcc: str
    sysroot: str | None
    python: str
    runtime: str
    tools: tuple[str, ...]


def _platform_defaults(platform: Platform) -> Defaults:
    if platform.is_macos:
        # conda-forge's native GCC packages start at 15.2.
        gcc = "15"
        sysroot = None
    elif platform.is_windows:
        gcc = "14"
        sysroot = None
    else:
        gcc = "14"
        sysroot = "2.17"
    return Defaults(
        gcc=gcc,
        sysroot=sysroot,
        python="3.12",
        runtime="static",
        tools=("cmake", "ninja"),
    )


@dataclass
class Config:
    root: Path
    platform: Platform
    defaults: Defaults
    channels: tuple[str, ...] = ("conda-forge",)
    mamba_root: Path | None = None
    download_timeout: float = 60.0
    conda_packages: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mamba_root is None:
            self.mamba_root = self.root / "mamba"

    # -- path helpers -------------------------------------------------
    @property
    def bin_dir(self) -> Path:
        return self.root / "bin"

    @property
    def envs_dir(self) -> Path:
        return self.root / "envs"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def downloads_dir(self) -> Path:
        return self.cache_dir / "downloads"

    @property
    def uv_cache_dir(self) -> Path:
        return self.cache_dir / "uv"

    @property
    def uv_python_dir(self) -> Path:
        return self.root / "python"

    @property
    def config_path(self) -> Path:
        return self.root / "config.toml"

    @property
    def current_path(self) -> Path:
        return self.root / "current"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def lock_path(self) -> Path:
        return self.root / ".lock"

    @property
    def micromamba_path(self) -> Path:
        return self.bin_dir / f"micromamba{self.platform.exe}"

    @property
    def uv_path(self) -> Path:
        return self.bin_dir / f"uv{self.platform.exe}"

    def env_dir(self, name: str) -> Path:
        return self.envs_dir / name

    def ensure_layout(self) -> None:
        for directory in (
            self.root,
            self.bin_dir,
            self.envs_dir,
            self.cache_dir,
            self.downloads_dir,
            self.uv_cache_dir,
            self.uv_python_dir,
            self.mamba_root,
            self.state_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    # -- loading ------------------------------------------------------
    @classmethod
    def load(cls, root: str | os.PathLike[str] | None = None) -> Config:
        platform = current_platform()
        root_value = os.environ.get("GE_ROOT")
        default_root = Path(root_value).expanduser() if root_value else Path("~/.ge").expanduser()
        if root is not None:
            default_root = Path(root).expanduser()
        root_path = default_root.resolve()
        defaults = _platform_defaults(platform)

        data: dict = {}
        config_file = root_path / "config.toml"
        if config_file.exists():
            try:
                with config_file.open("rb") as handle:
                    loaded = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise UsageError(f"cannot read {config_file}: {exc}") from exc
            if not isinstance(loaded, dict):
                raise UsageError(f"{config_file} must contain a TOML table")
            data = loaded

        section = data.get("defaults", {})
        if section and not isinstance(section, dict):
            raise UsageError("[defaults] in config.toml must be a table")

        gcc = os.environ.get("GE_GCC") or str(section.get("gcc", defaults.gcc))
        sysroot_value = os.environ.get("GE_SYSROOT")
        if sysroot_value is None:
            sysroot_value = section.get("sysroot", defaults.sysroot)
        sysroot = None if sysroot_value in (None, "", "none", "None") else str(sysroot_value)
        python = os.environ.get("GE_PYTHON") or str(section.get("python", defaults.python))
        runtime = os.environ.get("GE_RUNTIME") or str(section.get("runtime", defaults.runtime))
        if runtime not in {"static", "shared", "bundle"}:
            raise UsageError(
                f"invalid runtime policy {runtime!r} in config.toml or environment",
                hint="Use static, shared, or bundle.",
            )

        tools_value = section.get("tools", list(defaults.tools))
        if isinstance(tools_value, str):
            tools = tuple(part.strip() for part in tools_value.split(",") if part.strip())
        elif isinstance(tools_value, (list, tuple)):
            tools = tuple(str(item).strip() for item in tools_value if str(item).strip())
        else:
            raise UsageError("[defaults].tools must be a list or a comma-separated string")

        channels_env = os.environ.get("GE_CONDA_CHANNELS")
        channel_value = section.get("channels", ["conda-forge"])
        if channels_env:
            channels = tuple(part.strip() for part in channels_env.split(",") if part.strip())
        elif isinstance(channel_value, str):
            channels = tuple(part.strip() for part in channel_value.split(",") if part.strip())
        elif isinstance(channel_value, (list, tuple)):
            channels = tuple(str(item) for item in channel_value)
        else:
            raise UsageError("[defaults].channels must be a list or a comma-separated string")
        if not channels:
            channels = ("conda-forge",)

        mamba_root_value = os.environ.get("GE_MAMBA_ROOT")
        if mamba_root_value:
            mamba_root = Path(mamba_root_value).expanduser().resolve()
        else:
            configured = section.get("mamba_root")
            mamba_root = (
                Path(str(configured)).expanduser().resolve() if configured else root_path / "mamba"
            )

        timeout_value = os.environ.get("GE_DOWNLOAD_TIMEOUT") or section.get("download_timeout", 60)
        try:
            timeout = float(timeout_value)
        except (TypeError, ValueError) as exc:
            raise UsageError("download_timeout must be a number") from exc

        config = cls(
            root=root_path,
            platform=platform,
            defaults=Defaults(gcc, sysroot, python, runtime, tools),
            channels=channels,
            mamba_root=mamba_root,
            download_timeout=timeout,
            conda_packages={},
        )
        return config

    # -- display ------------------------------------------------------
    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "platform": {
                "system": self.platform.system,
                "machine": self.platform.machine,
                "conda_subdir": self.platform.conda_subdir,
            },
            "defaults": {
                "gcc": self.defaults.gcc,
                "sysroot": self.defaults.sysroot,
                "python": self.defaults.python,
                "runtime": self.defaults.runtime,
                "tools": list(self.defaults.tools),
            },
            "channels": list(self.channels),
            "mamba_root": str(self.mamba_root),
            "download_timeout": self.download_timeout,
            "config_file": str(self.config_path),
        }
