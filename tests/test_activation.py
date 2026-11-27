from __future__ import annotations

import os

from gcc_env.activation import activation_script, toolchain_env
from gcc_env.config import Config

from .conftest import make_environment


def test_toolchain_env(config: Config) -> None:
    env = make_environment(config)
    values = toolchain_env(env, base={"PATH": "/usr/bin", "HOME": "/home/x"})
    assert values["GE_ENV"] == "gcc14"
    assert values["CC"] == str(env.cc)
    assert values["CXX"] == str(env.cxx)
    if config.platform.is_windows:
        assert str(env.conda_prefix / "Library" / "bin") in values["PATH"]
    else:
        assert str(env.conda_prefix / "bin") in values["PATH"]
    assert str(env.venv_bin()) in values["PATH"]
    assert values["VIRTUAL_ENV"] == str(env.venv_dir)
    assert values["HOME"] == "/home/x"
    if config.platform.is_linux:
        assert values["LD_LIBRARY_PATH"].startswith(str(env.conda_prefix / "lib"))
        assert values["CONDA_BUILD_SYSROOT"] == str(env.sysroot)


def test_path_dedupe(config: Config) -> None:
    env = make_environment(config)
    first = toolchain_env(env, base={"PATH": "/usr/bin"})["PATH"]
    second = toolchain_env(env, base={"PATH": first})["PATH"]
    entries = second.split(os.pathsep)
    assert len(entries) == len(set(entries))


def test_switching_cleans_previous_env(config: Config) -> None:
    first = make_environment(config, "gcc13")
    second = make_environment(config, "gcc14")
    base = toolchain_env(first, base={"PATH": "/usr/bin"})
    switched = toolchain_env(second, base=base)
    assert str(first.conda_prefix) not in switched["PATH"]
    assert str(first.venv_dir) not in switched["PATH"]
    assert str(second.conda_prefix) in switched["PATH"]
    if config.platform.is_linux:
        assert str(first.conda_prefix) not in switched["LD_LIBRARY_PATH"]
        assert str(second.conda_prefix) in switched["LD_LIBRARY_PATH"]


def test_bash_activation(config: Config) -> None:
    env = make_environment(config)
    script = activation_script(env, shell="bash")
    assert "export CC=" in script
    assert "export PATH=" in script
    assert "hash -r" in script
    # Values with spaces must be quoted safely.
    assert "export VIRTUAL_ENV=" in script


def test_powershell_activation(config: Config) -> None:
    env = make_environment(config)
    script = activation_script(env, shell="powershell")
    assert "$env:CC = " in script
    assert "$env:PATH = " in script


def test_fish_activation(config: Config) -> None:
    env = make_environment(config)
    script = activation_script(env, shell="fish")
    assert "set -gx CC " in script
    assert "set -gx PATH " in script
