"""Platform and architecture detection.

The conda subdir names double as micromamba platform names, while uv uses its
own target triples for standalone release downloads.
"""

from __future__ import annotations

import platform as _platform
import sys
from dataclasses import dataclass

from .errors import PlatformError


@dataclass(frozen=True)
class Platform:
    system: str  # "linux", "darwin", "windows"
    machine: str  # "x86_64" or "aarch64"
    conda_subdir: str
    uv_target: str
    exe: str

    @property
    def is_linux(self) -> bool:
        return self.system == "linux"

    @property
    def is_macos(self) -> bool:
        return self.system == "darwin"

    @property
    def is_windows(self) -> bool:
        return self.system == "windows"

    @property
    def label(self) -> str:
        names = {"linux": "Linux", "darwin": "macOS", "windows": "Windows"}
        arches = {"x86_64": "x86_64", "aarch64": "arm64"}
        return f"{names[self.system]} {arches[self.machine]}"


def _normalize_machine(machine: str) -> str:
    value = machine.lower()
    if value in {"x86_64", "amd64", "x64"}:
        return "x86_64"
    if value in {"aarch64", "arm64"}:
        return "aarch64"
    raise PlatformError(
        f"unsupported CPU architecture: {machine}",
        hint="gcc-env supports x86_64 and arm64 (aarch64) on Linux/macOS, and x86_64 on Windows.",
    )


def current_platform() -> Platform:
    system_name = _platform.system().lower()
    machine = _normalize_machine(_platform.machine())

    if sys.platform.startswith("linux") or system_name == "linux":
        if machine == "aarch64":
            return Platform("linux", machine, "linux-aarch64", "aarch64-unknown-linux-gnu", "")
        return Platform("linux", machine, "linux-64", "x86_64-unknown-linux-gnu", "")

    if sys.platform == "darwin" or system_name == "darwin":
        if machine == "aarch64":
            return Platform("darwin", machine, "osx-arm64", "aarch64-apple-darwin", "")
        return Platform("darwin", machine, "osx-64", "x86_64-apple-darwin", "")

    if sys.platform == "win32" or system_name == "windows":
        if machine != "x86_64":
            raise PlatformError(
                f"unsupported Windows architecture: {machine}",
                hint="Native Windows GCC is currently available for x86_64 only. "
                "Use WSL for Linux toolchains on Windows on ARM.",
            )
        return Platform("windows", machine, "win-64", "x86_64-pc-windows-msvc", ".exe")

    raise PlatformError(
        f"unsupported operating system: {_platform.system()}",
        hint="gcc-env supports Linux, macOS, and Windows (including WSL).",
    )
