from __future__ import annotations

from pathlib import Path

import pytest

from gcc_env.config import Config
from gcc_env.errors import UsageError


def test_config_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GE_ROOT", str(tmp_path / "root"))
    config = Config.load()
    assert config.root == (tmp_path / "root").resolve()
    assert config.platform.conda_subdir
    assert config.defaults.python == "3.12"
    assert config.defaults.runtime == "static"
    assert config.channels == ("conda-forge",)


def test_config_file_and_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "config.toml").write_text(
        """
[defaults]
gcc = "13.3"
sysroot = "2.28"
python = "3.11"
runtime = "bundle"
tools = ["cmake"]
channels = ["https://example.invalid/conda"]
download_timeout = 12
""",
        encoding="utf-8",
    )
    config = Config.load(root)
    assert config.defaults.gcc == "13.3"
    assert config.defaults.sysroot == "2.28"
    assert config.defaults.python == "3.11"
    assert config.defaults.runtime == "bundle"
    assert config.defaults.tools == ("cmake",)
    assert config.channels == ("https://example.invalid/conda",)
    assert config.download_timeout == 12

    monkeypatch.setenv("GE_GCC", "15")
    monkeypatch.setenv("GE_CONDA_CHANNELS", "conda-forge,https://mirror.invalid")
    config = Config.load(root)
    assert config.defaults.gcc == "15"
    assert config.channels == ("conda-forge", "https://mirror.invalid")


def test_invalid_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "config.toml").write_text("[defaults]\nruntime = 'broken'\n", encoding="utf-8")
    with pytest.raises(UsageError):
        Config.load(root)


def test_invalid_toml(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "config.toml").write_text("[defaults\n", encoding="utf-8")
    with pytest.raises(UsageError):
        Config.load(root)
