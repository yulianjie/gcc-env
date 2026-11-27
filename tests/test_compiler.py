from __future__ import annotations

import os
from pathlib import Path

import pytest

from gcc_env.compiler import compiler_specs, discover_toolchain, runtime_flags
from gcc_env.config import Config


@pytest.mark.skipif(os.name == "nt", reason="fake compiler scripts are POSIX-only")
def test_discover_toolchain(tmp_path: Path, config: Config) -> None:
    prefix = tmp_path / "prefix"
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True)
    for name, _kind in (
        ("x86_64-conda-linux-gnu-gcc", "gcc"),
        ("x86_64-conda-linux-gnu-g++", "g++"),
        ("x86_64-conda-linux-gnu-ar", "ar"),
        ("x86_64-conda-linux-gnu-ld", "ld"),
    ):
        script = bin_dir / name
        script.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            "  -dumpversion) echo 14.3.0 ;;\n"
            "  -dumpmachine) echo x86_64-conda-linux-gnu ;;\n"
            "  -print-sysroot) echo '' ;;\n"
            "  *) echo 'gcc (GCC) 14.3.0' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        script.chmod(0o755)

    toolchain = discover_toolchain(prefix, config.platform)
    assert toolchain.version == "14.3.0"
    assert toolchain.target == "x86_64-conda-linux-gnu"
    assert toolchain.cc.name == "x86_64-conda-linux-gnu-gcc"
    assert toolchain.cxx.name == "x86_64-conda-linux-gnu-g++"
    assert toolchain.tools["ar"].name == "x86_64-conda-linux-gnu-ar"


def test_runtime_flags(config: Config) -> None:
    assert "-static-libstdc++" in runtime_flags(config.platform, "static")
    assert runtime_flags(config.platform, "shared") == []
    if config.platform.is_linux:
        assert any("$ORIGIN/../lib" in flag for flag in runtime_flags(config.platform, "bundle"))


def test_compiler_specs(config: Config) -> None:
    specs = compiler_specs(config.platform, "14.3", "2.17")
    assert any("=14.3" in spec for spec in specs)
    latest = compiler_specs(config.platform, "latest", None)
    assert all("=latest" not in spec for spec in latest)
    if config.platform.is_linux:
        assert "patchelf" in specs
        assert any(spec.startswith("sysroot_linux-") for spec in specs)
