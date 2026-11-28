from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gcc_env.activation import activation_script, deactivation_script, toolchain_env
from gcc_env.cli import main
from gcc_env.config import Config
from gcc_env.state import load_state, save_snapshot

from .conftest import make_environment


def test_activation_script_has_session(config: Config) -> None:
    env = make_environment(config)
    env.ensure_shims()
    script = activation_script(env, shell="bash", session="abc123")
    assert "export GE_SESSION=abc123" in script
    assert "export GE_ACTIVE=1" in script
    assert str(env.shim_dir) in script


def test_deactivation_restores_snapshot(config: Config) -> None:
    script = deactivation_script(
        {
            "PATH": "/usr/bin:/bin",
            "CC": "original-cc",
            "GE_ROOT": str(config.root),
        },
        shell="bash",
    )
    assert "export PATH=/usr/bin:/bin" in script
    assert "export CC=original-cc" in script
    assert "unset CXX" in script
    assert "unset GE_ACTIVE" in script
    assert "unset GE_SESSION" in script


def test_state_snapshot_round_trip(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GE_ROOT", str(config.root))
    monkeypatch.setenv("CC", "original-cc")
    env = make_environment(config)
    state = save_snapshot(config, "session1", env.name)
    assert state["env"]["CC"] == "original-cc"
    loaded = load_state(config, "session1")
    assert loaded is not None
    assert loaded["environment"] == env.name
    data = json.loads((config.state_dir / "session1.json").read_text(encoding="utf-8"))
    assert data["ppid"] == os.getppid()


def test_cli_activate_and_deactivate(
    config: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GE_ROOT", str(config.root))
    monkeypatch.setenv("CC", "original-cc")
    monkeypatch.delenv("GE_SESSION", raising=False)
    make_environment(config)
    assert main(["activate", "gcc14", "--shell", "bash"]) == 0
    script = capsys.readouterr().out
    assert "export GE_SESSION=" in script
    session_line = next(
        line for line in script.splitlines() if line.startswith("export GE_SESSION=")
    )
    session = session_line.split("=", 1)[1].strip("'")
    assert (config.state_dir / f"{session}.json").is_file()

    monkeypatch.setenv("GE_SESSION", session)
    assert main(["deactivate", "--shell", "bash"]) == 0
    restored = capsys.readouterr().out
    assert "export CC=original-cc" in restored
    assert not (config.state_dir / f"{session}.json").exists()


def test_shell_init_install(tmp_path: Path) -> None:
    from gcc_env.shellinit import install, render, uninstall

    profile = tmp_path / "profile.sh"
    profile.write_text("# existing\n", encoding="utf-8")
    assert "GE_SHELL=bash" in render("bash")
    install("bash", profile)
    install("bash", profile)
    text = profile.read_text(encoding="utf-8")
    assert text.count("gcc-env shell integration") == 2
    assert "shell-init --shell bash" in text
    uninstall("bash", profile)
    assert "gcc-env shell integration" not in profile.read_text(encoding="utf-8")


def test_shims(config: Config) -> None:
    env = make_environment(config)
    env.ensure_shims()
    for name, expected in (
        ("c", "ge cc"),
        ("cc", "ge cc"),
        ("gcc", "ge cc"),
        ("c++", "ge c++"),
        ("g++", "ge c++"),
    ):
        text = (env.shim_dir / name).read_text(encoding="utf-8")
        assert expected in text
        assert "gcc14" in text
    values = toolchain_env(env, base={"PATH": "/usr/bin"})
    assert str(env.shim_dir) in values["PATH"]
