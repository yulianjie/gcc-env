from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def ge_root() -> Path:
    configured = os.environ.get("GE_INTEGRATION_ROOT") or os.environ.get("GE_ROOT")
    return Path(configured or "~/.ge").expanduser()


def ge_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GE_ROOT"] = str(ge_root())
    env["NO_COLOR"] = "1"
    env["GE_DOWNLOAD_TIMEOUT"] = "120"
    return env


def run_ge(*args: str, check: bool = True, timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "gcc_env", *args],
        env=ge_env(),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"ge {' '.join(args)} failed with {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def remove_env(name: str) -> None:
    run_ge("remove", name, "--yes", check=False, timeout=120)


@pytest.fixture(scope="module")
def gcc13_env() -> str:
    if sys.platform != "linux":
        pytest.skip("full integration assertions currently target Linux")
    name = "ge-it-gcc13"
    remove_env(name)
    result = run_ge(
        "create",
        name,
        "--gcc",
        "13.3.0",
        "--sysroot",
        "2.17",
        "--python",
        "3.12",
        "--tools",
        "cmake,ninja",
        "--use",
        timeout=1800,
    )
    assert "created" in result.stderr or "created" in result.stdout
    yield name
    remove_env(name)


@pytest.fixture(scope="module")
def gcc14_env(gcc13_env: str) -> str:
    name = "ge-it-gcc14"
    remove_env(name)
    run_ge("create", name, "--gcc", "14.3.0", "--no-python", timeout=1800)
    yield name
    remove_env(name)


def test_info_and_list(gcc13_env: str) -> None:
    info = json.loads(run_ge("info", gcc13_env, "--json").stdout)
    assert info["gcc"]["version"] == "13.3.0"
    assert info["gcc"]["sysroot"].endswith("sysroot")
    assert info["python"]["enabled"] is True
    assert info["python"]["tools"] == ["cmake", "ninja"]

    listing = json.loads(run_ge("list", "--json").stdout)
    names = {item["name"] for item in listing["environments"]}
    assert gcc13_env in names
    assert listing["current"] == gcc13_env


def test_switch_versions(gcc13_env: str, gcc14_env: str) -> None:
    run_ge("use", gcc13_env)
    assert run_ge("current").stdout.strip() == gcc13_env

    run_ge("use", gcc14_env)
    assert run_ge("current").stdout.strip() == gcc14_env
    version = run_ge("c++", "--env", gcc14_env, "--", "-dumpversion").stdout.strip()
    assert version == "14.3.0"

    run_ge("use", gcc13_env)
    version = run_ge("c++", "--env", gcc13_env, "--", "-dumpversion").stdout.strip()
    assert version == "13.3.0"


def test_static_binary_is_portable(tmp_path: Path, gcc13_env: str) -> None:
    source = tmp_path / "main.cpp"
    source.write_text(
        """
#include <format>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>
int main() {
    std::vector<std::string> v{"gcc-env", "static"};
    std::cout << std::format("{} {}\\n", v.size(), std::filesystem::path("/tmp").string());
}
""",
        encoding="utf-8",
    )
    binary = tmp_path / "app"
    run_ge(
        "c++",
        "--env",
        gcc13_env,
        "--",
        "-std=c++20",
        "-O2",
        str(source),
        "-o",
        str(binary),
    )
    output = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    assert output.returncode == 0
    assert "2 /tmp" in output.stdout

    dynamic = subprocess.run(
        ["readelf", "-d", str(binary)], capture_output=True, text=True, timeout=60
    )
    assert "libstdc++.so.6" not in dynamic.stdout
    version_info = subprocess.run(
        ["readelf", "--version-info", "-W", str(binary)],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert "GLIBC_2.17" in version_info or "GLIBC_2.14" in version_info

    doctor = json.loads(run_ge("doctor", gcc13_env, "--binary", str(binary), "--json").stdout)
    assert doctor["ok"] is True
    assert doctor["binary_info"]["needed"]
    assert not any("libstdc++" in name for name in doctor["binary_info"]["needed"])


def test_cmake_toolchain(tmp_path: Path, gcc13_env: str) -> None:
    (tmp_path / "CMakeLists.txt").write_text(
        """
cmake_minimum_required(VERSION 3.20)
project(ge_test CXX)
add_executable(app main.cpp)
target_compile_features(app PRIVATE cxx_std_20)
""",
        encoding="utf-8",
    )
    (tmp_path / "main.cpp").write_text(
        "#include <format>\n#include <iostream>\n"
        'int main(){ std::cout << std::format("cmake {}\\n", 42); }\n',
        encoding="utf-8",
    )
    toolchain = Path(run_ge("toolchain", gcc13_env).stdout.strip())
    assert toolchain.is_file()
    build = tmp_path / "build"
    run_ge(
        "run",
        gcc13_env,
        "--",
        "cmake",
        "-S",
        str(tmp_path),
        "-B",
        str(build),
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
    )
    run_ge("run", gcc13_env, "--", "cmake", "--build", str(build))
    output = subprocess.run([str(build / "app")], capture_output=True, text=True, timeout=60)
    assert output.returncode == 0
    assert "cmake 42" in output.stdout


def test_bundle_shared_runtime(tmp_path: Path, gcc13_env: str) -> None:
    source = tmp_path / "hello.cpp"
    source.write_text(
        '#include <iostream>\nint main(){ std::cout << "bundled\\n"; }\n', encoding="utf-8"
    )
    binary = tmp_path / "hello"
    run_ge(
        "c++",
        "--env",
        gcc13_env,
        "--runtime",
        "shared",
        "--",
        str(source),
        "-o",
        str(binary),
    )
    destination = tmp_path / "dist"
    run_ge("bundle", str(binary), "--env", gcc13_env, "--dest", str(destination))
    assert (destination / "bin" / "hello").exists()
    assert (destination / "lib" / "libstdc++.so.6").exists()
    assert (destination / "lib" / "libgcc_s.so.1").exists()

    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    output = subprocess.run(
        [str(destination / "bin" / "hello")], capture_output=True, text=True, env=env, timeout=60
    )
    assert output.returncode == 0
    assert "bundled" in output.stdout
    rpath = subprocess.run(
        ["readelf", "-d", str(destination / "bin" / "hello")],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert "$ORIGIN/../lib" in rpath
    assert str(ge_root()) not in rpath


def test_activate_script(gcc13_env: str) -> None:
    script = run_ge("activate", gcc13_env, "--shell", "bash").stdout
    assert "export CC=" in script
    assert "export CXX=" in script
    assert "GE_ENV=gcc13" in script or f"GE_ENV={gcc13_env}" in script


def test_platform_smoke(tmp_path: Path) -> None:
    """Cross-platform create/compile/run/doctor smoke test for CI."""
    if sys.platform == "linux":
        pytest.skip("Linux platform coverage is provided by the full integration tests")
    if os.environ.get("GE_RUN_PLATFORM_SMOKE") != "1":
        pytest.skip("set GE_RUN_PLATFORM_SMOKE=1 to run the platform smoke test")
    name = f"ge-it-smoke-{sys.platform}"
    remove_env(name)
    try:
        run_ge("create", name, "--no-python", timeout=1800)
        source = tmp_path / "smoke.cpp"
        source.write_text(
            "#include <filesystem>\n#include <iostream>\n"
            'int main(){ std::cout << std::filesystem::path("ok").string() << "\\n"; }\n',
            encoding="utf-8",
        )
        binary = tmp_path / ("smoke.exe" if os.name == "nt" else "smoke")
        run_ge(
            "c++",
            "--env",
            name,
            "--",
            "-std=c++17",
            str(source),
            "-o",
            str(binary),
        )
        output = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
        assert output.returncode == 0
        assert "ok" in output.stdout
        doctor = json.loads(run_ge("doctor", name, "--binary", str(binary), "--json").stdout)
        assert doctor["ok"] is True, doctor
    finally:
        remove_env(name)
