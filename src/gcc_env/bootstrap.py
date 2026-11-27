"""Bootstrap the two external engines used by gcc-env: micromamba and uv."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .errors import CommandError, DownloadError
from .util import (
    download,
    find_named_files,
    info,
    run_capture,
    safe_extract_tar,
    safe_extract_zip,
    success,
)


def _chmod_exec(path: Path) -> None:
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@dataclass
class Bootstrap:
    config: Config

    @property
    def platform(self):
        return self.config.platform

    def uv(self, *, install: bool = True) -> Path:
        explicit = os.environ.get("GE_UV")
        if explicit:
            path = Path(explicit).expanduser()
            self._validate(path, ["--version"], "uv")
            return path

        if self.config.uv_path.exists():
            return self.config.uv_path

        found = shutil.which("uv")
        if found:
            return Path(found)

        if not install:
            raise DownloadError(
                "uv was not found",
                hint="Install uv from https://docs.astral.sh/uv/ or run 'ge setup'.",
            )
        return self._download_uv()

    def micromamba(self, *, install: bool = True) -> Path:
        explicit = os.environ.get("GE_MICROMAMBA")
        if explicit:
            path = Path(explicit).expanduser()
            self._validate(path, ["--version"], "micromamba")
            return path

        if self.config.micromamba_path.exists():
            return self.config.micromamba_path

        found = shutil.which("micromamba")
        if found:
            return Path(found)

        if not install:
            raise DownloadError(
                "micromamba was not found",
                hint="Run 'ge setup' to download a private micromamba automatically.",
            )
        return self._download_micromamba()

    def ensure(self) -> tuple[Path, Path]:
        self.config.ensure_layout()
        uv = self.uv()
        mamba = self.micromamba()
        return uv, mamba

    # -- validation ---------------------------------------------------
    def _validate(self, path: Path, args: list[str], label: str) -> str:
        if not path.exists():
            raise DownloadError(f"{label} was not found at {path}")
        try:
            result = run_capture([path, *args], timeout=30)
        except CommandError as exc:
            raise DownloadError(
                f"{label} at {path} is not runnable: {exc}",
                hint="Delete it and run 'ge setup' again.",
            ) from exc
        return result.stdout.strip() or result.stderr.strip()

    # -- downloads ----------------------------------------------------
    def _download_uv(self) -> Path:
        version = os.environ.get("GE_UV_VERSION", "latest")
        if os.environ.get("GE_UV_URL"):
            url = os.environ["GE_UV_URL"]
        elif version == "latest":
            url = (
                "https://github.com/astral-sh/uv/releases/latest/download/"
                f"uv-{self.platform.uv_target}"
                + (".zip" if self.platform.is_windows else ".tar.gz")
            )
        else:
            url = (
                f"https://github.com/astral-sh/uv/releases/download/{version}/"
                f"uv-{self.platform.uv_target}"
                + (".zip" if self.platform.is_windows else ".tar.gz")
            )
        suffix = ".zip" if url.endswith(".zip") else ".tar.gz"
        archive = self.config.downloads_dir / f"uv-{self.platform.uv_target}{suffix}"
        info(f"downloading uv for {self.platform.label}...")
        download(url, archive, label="uv", timeout=self.config.download_timeout)

        with tempfile.TemporaryDirectory(dir=self.config.downloads_dir) as temp:
            temp_path = Path(temp)
            if suffix == ".zip":
                safe_extract_zip(archive, temp_path)
            else:
                safe_extract_tar(archive, temp_path)
            names = {"uv.exe"} if self.platform.is_windows else {"uv"}
            matches = find_named_files(temp_path, names)
            if not matches:
                raise DownloadError(f"could not find the uv binary in {archive.name}")
            source = matches[0]
            self.config.bin_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, self.config.uv_path)
            _chmod_exec(self.config.uv_path)

        version_text = self._validate(self.config.uv_path, ["--version"], "uv")
        success(f"uv installed at {self.config.uv_path} ({version_text})")
        return self.config.uv_path

    def _download_micromamba(self) -> Path:
        if os.environ.get("GE_MICROMAMBA_URL"):
            url = os.environ["GE_MICROMAMBA_URL"]
        else:
            url = f"https://micro.mamba.pm/api/micromamba/{self.platform.conda_subdir}/latest"
        archive = self.config.downloads_dir / f"micromamba-{self.platform.conda_subdir}.tar.bz2"
        info(f"downloading micromamba for {self.platform.label}...")
        download(url, archive, label="micromamba", timeout=self.config.download_timeout)

        with tempfile.TemporaryDirectory(dir=self.config.downloads_dir) as temp:
            temp_path = Path(temp)
            safe_extract_tar(archive, temp_path)
            names = {"micromamba.exe"} if self.platform.is_windows else {"micromamba"}
            matches = find_named_files(temp_path, names)
            if not matches:
                raise DownloadError(f"could not find micromamba in {archive.name}")
            source = sorted(matches, key=lambda p: len(p.parts))[0]
            self.config.bin_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, self.config.micromamba_path)
            _chmod_exec(self.config.micromamba_path)

        version_text = self._validate(self.config.micromamba_path, ["--version"], "micromamba")
        success(f"micromamba installed at {self.config.micromamba_path} ({version_text})")
        return self.config.micromamba_path

    def versions(self) -> dict[str, str]:
        uv = self.uv(install=False)
        mamba = self.micromamba(install=False)
        uv_version = self._validate(uv, ["--version"], "uv")
        mamba_version = self._validate(mamba, ["--version"], "micromamba")
        return {"uv": uv_version, "micromamba": mamba_version}
