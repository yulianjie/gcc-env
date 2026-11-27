from __future__ import annotations

import pytest

from gcc_env.compiler import default_env_name, is_link_invocation
from gcc_env.envs import normalize_gcc_spec, validate_env_name
from gcc_env.errors import UsageError


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("14", "gcc-14"),
        ("14.3", "gcc-14.3"),
        ("14.3.0", "gcc-14.3.0"),
        ("latest", "gcc-latest"),
        ("=15", "gcc-15"),
    ],
)
def test_default_env_name(spec: str, expected: str) -> None:
    assert default_env_name(spec) == expected


@pytest.mark.parametrize("name", ["gcc14", "gcc-14.3", "a_b", "A.b-c", "x1"])
def test_valid_names(name: str) -> None:
    assert validate_env_name(name) == name


@pytest.mark.parametrize("name", ["", ".hidden", "-bad", "bad name", "con", "a/b", "x."])
def test_invalid_names(name: str) -> None:
    with pytest.raises(UsageError):
        validate_env_name(name)


@pytest.mark.parametrize("spec", ["14", "14.3", "14.3.0", "latest", "=13"])
def test_normalize_gcc_spec(spec: str) -> None:
    assert normalize_gcc_spec(spec)


@pytest.mark.parametrize("spec", ["", "gcc14", "14.3.0.1", "main"])
def test_invalid_gcc_spec(spec: str) -> None:
    with pytest.raises(UsageError):
        normalize_gcc_spec(spec)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], True),
        (["-O2", "main.cpp", "-o", "app"], True),
        (["-c", "main.cpp"], False),
        (["-E", "main.cpp"], False),
        (["-fsyntax-only", "main.cpp"], False),
        (["-dumpversion"], False),
        (["-print-sysroot"], False),
        (["-MD", "-MF", "x.d", "-c", "x.c"], False),
        (["-march=native", "x.c"], True),
    ],
)
def test_link_invocation(args: list[str], expected: bool) -> None:
    assert is_link_invocation(args) is expected
