from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gcc_env.cli import main

from .conftest import make_environment


@pytest.fixture
def cli_root(monkeypatch: pytest.MonkeyPatch, config) -> Path:
    monkeypatch.setenv("GE_ROOT", str(config.root))
    return config.root


@pytest.fixture
def current_env(cli_root: Path, config) -> Path:
    env = make_environment(config)
    config.current_path.write_text(env.name + "\n", encoding="utf-8")
    return config.root


def test_version(capsys: pytest.CaptureFixture[str], cli_root: Path) -> None:
    assert main(["version"]) == 0
    assert "gcc-env" in capsys.readouterr().out


def test_list_json_empty(capsys: pytest.CaptureFixture[str], cli_root: Path) -> None:
    assert main(["list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["environments"] == []


def test_activate_and_current(
    capsys: pytest.CaptureFixture[str], config, current_env: Path
) -> None:
    assert main(["current"]) == 0
    assert capsys.readouterr().out.strip() == "gcc14"

    assert main(["activate", "gcc14", "--shell", "bash"]) == 0
    script = capsys.readouterr().out
    assert "export CC=" in script


def test_run_command(capfd: pytest.CaptureFixture[str], current_env: Path) -> None:
    if os.name == "nt":
        code = main(["run", "--", "cmd", "/c", "echo hello-ge"])
    else:
        code = main(["run", "--", "sh", "-c", "echo hello-ge"])
    assert code == 0
    assert "hello-ge" in capfd.readouterr().out


def test_info_json(capsys: pytest.CaptureFixture[str], config, current_env: Path) -> None:
    assert main(["info", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "gcc14"
    assert data["gcc"]["version"] == "14.3.0"


def test_doctor_help(capsys: pytest.CaptureFixture[str], cli_root: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["doctor", "--help"])
    assert exc.value.code == 0
    assert "libstdc++" in capsys.readouterr().out
