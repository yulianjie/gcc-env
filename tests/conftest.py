from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gcc_env.config import Config, Defaults
from gcc_env.envs import Environment
from gcc_env.platforms import current_platform


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    for name in (
        "GE_GCC",
        "GE_SYSROOT",
        "GE_PYTHON",
        "GE_RUNTIME",
        "GE_CONDA_CHANNELS",
        "GE_MAMBA_ROOT",
        "GE_UV",
        "GE_MICROMAMBA",
    ):
        monkeypatch.delenv(name, raising=False)
    platform = current_platform()
    defaults = Defaults(
        gcc="14",
        sysroot="2.17" if platform.is_linux else None,
        python="3.12",
        runtime="static",
        tools=("cmake", "ninja"),
    )
    cfg = Config(root=tmp_path / "ge", platform=platform, defaults=defaults)
    cfg.ensure_layout()
    return cfg


def make_environment(config: Config, name: str = "gcc14") -> Environment:
    root = config.env_dir(name)
    conda = root / "conda"
    bin_dir = conda / ("Library/bin" if config.platform.is_windows else "bin")
    bin_dir.mkdir(parents=True)
    exe = config.platform.exe
    cc = bin_dir / f"x86_64-conda-linux-gnu-gcc{exe}"
    cxx = bin_dir / f"x86_64-conda-linux-gnu-g++{exe}"
    cc.write_text("", encoding="utf-8")
    cxx.write_text("", encoding="utf-8")
    (conda / "lib").mkdir(exist_ok=True)
    venv_bin = root / "venv" / ("Scripts" if config.platform.is_windows else "bin")
    venv_bin.mkdir(parents=True)
    python_name = "python.exe" if config.platform.is_windows else "python"
    (venv_bin / python_name).write_text("", encoding="utf-8")
    sysroot = None
    if config.platform.is_linux:
        sysroot = conda / "x86_64-conda-linux-gnu" / "sysroot"
        (sysroot / "usr" / "include").mkdir(parents=True)
    meta = {
        "schema": 1,
        "name": name,
        "platform": config.platform.conda_subdir,
        "created_at": "2026-01-01T00:00:00+00:00",
        "gcc": {
            "requested": "14",
            "version": "14.3.0",
            "target": "x86_64-conda-linux-gnu",
            "cc": os.path.relpath(cc, root),
            "cxx": os.path.relpath(cxx, root),
            "tools": {},
        },
        "sysroot": {
            "version": "2.17" if sysroot else None,
            "path": os.path.relpath(sysroot, root) if sysroot else None,
        },
        "conda": {"packages": {"gcc_linux-64": "14.3.0"}, "channels": ["conda-forge"]},
        "python": {
            "enabled": True,
            "requested": "3.12",
            "version": "3.12.0",
            "venv": "venv",
            "tools": ["cmake", "ninja"],
        },
        "runtime": "static",
    }
    (root / "env.json").write_text(json.dumps(meta), encoding="utf-8")
    return Environment(config, name, root, meta)
