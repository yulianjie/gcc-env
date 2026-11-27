"""uv integration: one isolated Python build environment per toolchain."""

from __future__ import annotations

import os
from pathlib import Path

from .config import Config
from .errors import CommandError
from .util import run_capture, run_stream, step


class Uv:
    def __init__(self, config: Config, uv: Path) -> None:
        self.config = config
        self.uv = uv

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["UV_CACHE_DIR"] = str(self.config.uv_cache_dir)
        env["UV_PYTHON_INSTALL_DIR"] = str(self.config.uv_python_dir)
        env["UV_NO_MODIFY_PATH"] = "1"
        env.setdefault("UV_PYTHON_DOWNLOADS", "automatic")
        return env

    def venv(self, destination: Path, python: str) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.uv,
            "venv",
            "--seed",
            "--python",
            python,
            str(destination),
        ]
        step(f"creating uv virtual environment (Python {python})")
        run_stream(command, env=self._env())
        return self.python(destination)

    def pip_install(self, venv: Path, packages: list[str]) -> None:
        if not packages:
            return
        python = self.python(venv)
        command = [
            self.uv,
            "pip",
            "install",
            "--python",
            str(python),
            *packages,
        ]
        step(f"installing Python tooling with uv: {', '.join(packages)}")
        run_stream(command, env=self._env())

    def pip_list(self, venv: Path) -> dict[str, str]:
        python = self.python(venv)
        try:
            result = run_capture(
                [self.uv, "pip", "list", "--python", str(python), "--format=json"],
                env=self._env(),
                timeout=60,
            )
        except CommandError:
            return {}
        import json

        try:
            entries = json.loads(result.stdout)
        except ValueError:
            return {}
        versions: dict[str, str] = {}
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("name"):
                    versions[str(entry["name"]).lower()] = str(entry.get("version", ""))
        return versions

    def version(self) -> str:
        try:
            result = run_capture([self.uv, "--version"], env=self._env(), timeout=30)
        except CommandError:
            return "unknown"
        return (result.stdout or result.stderr).strip()

    @staticmethod
    def python(venv: Path) -> Path:
        if os.name == "nt":
            return venv / "Scripts" / "python.exe"
        return venv / "bin" / "python"

    @staticmethod
    def bin_dir(venv: Path) -> Path:
        if os.name == "nt":
            return venv / "Scripts"
        return venv / "bin"

    @staticmethod
    def python_version(python: Path) -> str:
        try:
            result = run_capture(
                [
                    python,
                    "-c",
                    "import platform; print(platform.python_version())",
                ],
                timeout=30,
            )
        except CommandError:
            return "unknown"
        return result.stdout.strip()
