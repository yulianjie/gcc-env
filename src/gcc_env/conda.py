"""Thin wrapper around micromamba and conda prefix metadata."""

from __future__ import annotations

import os
from pathlib import Path

from .config import Config
from .util import run_stream, step


class Conda:
    def __init__(self, config: Config, micromamba: Path) -> None:
        self.config = config
        self.micromamba = micromamba

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["MAMBA_ROOT_PREFIX"] = str(self.config.mamba_root)
        env["CONDA_PKGS_DIRS"] = str(Path(self.config.mamba_root) / "pkgs")
        env.setdefault("MAMBA_NO_BANNER", "1")
        return env

    def create(self, prefix: Path, packages: list[str]) -> None:
        prefix.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.micromamba,
            "--no-rc",
            "create",
            "--yes",
            "--prefix",
            prefix,
            "--override-channels",
        ]
        for channel in self.config.channels:
            command.extend(["--channel", channel])
        command.extend(packages)

        step(f"solving conda-forge environment ({', '.join(packages)})")
        run_stream(command, env=self._env())

    def package_versions(self, prefix: Path) -> dict[str, str]:
        """Read installed package versions from conda-meta (no solver needed)."""
        versions: dict[str, str] = {}
        meta = prefix / "conda-meta"
        if not meta.is_dir():
            return versions
        import json

        for record in meta.glob("*.json"):
            try:
                data = json.loads(record.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            name = data.get("name")
            version = data.get("version")
            if isinstance(name, str) and isinstance(version, str):
                versions[name] = version
        return versions

    def remove(self, prefix: Path) -> None:
        """Conda prefixes are disposable; deleting the directory is enough."""
        if prefix.exists():
            from .util import rmtree

            rmtree(prefix)
