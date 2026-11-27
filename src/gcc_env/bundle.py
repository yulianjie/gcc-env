"""Create a relocatable application bundle with the GCC runtime libraries."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .envs import Environment
from .errors import CommandError, EnvError
from .runtime import inspect_binary
from .util import info, run_capture, step, success, warn, which

_SYSTEM_LIBS = {
    "libc.so.6",
    "libm.so.6",
    "libdl.so.2",
    "libpthread.so.0",
    "librt.so.1",
    "libresolv.so.2",
    "libutil.so.1",
    "libanl.so.1",
    "ld-linux-x86-64.so.2",
    "ld-linux-aarch64.so.1",
    "libSystem.B.dylib",
    "libc++.1.dylib",
    "libobjc.A.dylib",
}
_WINDOWS_SKIP = {
    "kernel32.dll",
    "user32.dll",
    "gdi32.dll",
    "advapi32.dll",
    "shell32.dll",
    "ole32.dll",
    "oleaut32.dll",
    "ws2_32.dll",
    "msvcrt.dll",
    "ucrtbase.dll",
    "ntdll.dll",
    "mpr.dll",
    "comdlg32.dll",
    "comctl32.dll",
    "bcrypt.dll",
    "crypt32.dll",
    "winmm.dll",
    "shlwapi.dll",
    "version.dll",
    "imm32.dll",
    "vcruntime140.dll",
    "msvcp140.dll",
}


def _library_search_dirs(env: Environment) -> list[Path]:
    dirs: list[Path] = []
    if env.config.platform.is_windows:
        dirs.extend(
            [
                env.conda_prefix / "Library" / "bin",
                env.conda_prefix / "Library" / "mingw-w64" / "bin",
                env.conda_prefix / "bin",
            ]
        )
    else:
        dirs.extend(
            [
                env.conda_prefix / "lib",
                env.conda_prefix / "lib64",
            ]
        )
    dirs.extend([env.conda_prefix / "Library" / "lib"])
    return [directory for directory in dirs if directory.is_dir()]


def find_runtime_library(env: Environment, filename: str) -> Path | None:
    for directory in _library_search_dirs(env):
        candidate = directory / filename
        if candidate.exists():
            return candidate.resolve()
    return None


def _copy_binary(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | 0o755)


def _patchelf(env: Environment) -> str | None:
    candidate = env.conda_prefix / "bin" / "patchelf"
    if candidate.exists():
        return str(candidate)
    found = which("patchelf")
    return str(found) if found else None


def bundle(
    env: Environment,
    binary: Path,
    destination: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    binary = binary.resolve()
    if not binary.is_file():
        raise EnvError(f"binary not found: {binary}")
    if destination is None:
        destination = Path.cwd() / f"{binary.stem}-bundle"
    else:
        destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        if not force:
            raise EnvError(
                f"destination is not empty: {destination}",
                hint="Pass --force to replace it.",
            )
        from .util import rmtree

        rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    info_obj = inspect_binary(binary, env)
    if env.config.platform.is_windows:
        return _bundle_windows(env, binary, destination, info_obj)
    if env.config.platform.is_macos:
        return _bundle_macos(env, binary, destination, info_obj)
    return _bundle_linux(env, binary, destination, info_obj)


def _bundle_linux(env: Environment, binary: Path, destination: Path, info_obj) -> Path:
    bin_dir = destination / "bin"
    lib_dir = destination / "lib"
    bin_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / binary.name
    _copy_binary(binary, target)

    copied: list[str] = []
    for filename in info_obj.needed:
        if filename in _SYSTEM_LIBS or filename.startswith("ld-linux"):
            continue
        source = find_runtime_library(env, filename)
        if source is None:
            continue
        _copy_binary(source, lib_dir / filename)
        copied.append(filename)

    patchelf = _patchelf(env)
    if patchelf:
        step("rewriting RPATH to $ORIGIN/../lib")
        run_capture([patchelf, "--set-rpath", "$ORIGIN/../lib", str(target)], timeout=60)
    else:
        warn("patchelf is not available; the binary keeps its development RPATH")
        warn("install patchelf in the environment or on PATH and rerun `ge bundle`")

    success(f"bundle written to {destination}")
    if copied:
        info(f"runtime libraries: {', '.join(copied)}")
    else:
        info("no environment libraries were required (fully/static runtime)")
    info(f"run with: {target}")
    return destination


def _bundle_macos(env: Environment, binary: Path, destination: Path, info_obj) -> Path:
    bin_dir = destination / "bin"
    lib_dir = destination / "lib"
    bin_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / binary.name
    _copy_binary(binary, target)

    install_name_tool = which("install_name_tool")
    otool = which("otool")
    copied: list[str] = []
    for filename in info_obj.needed:
        if filename in _SYSTEM_LIBS:
            continue
        source = find_runtime_library(env, filename)
        if source is None:
            continue
        _copy_binary(source, lib_dir / filename)
        copied.append(filename)

    if install_name_tool:
        for filename in copied:
            old_paths = [
                path for path in (info_obj.needed_paths or []) if Path(path).name == filename
            ]
            if not old_paths:
                old_paths = [str(env.conda_prefix / "lib" / filename), filename]
            for old in old_paths:
                if old.startswith("@rpath/"):
                    continue
                try:
                    run_capture(
                        [str(install_name_tool), "-change", old, f"@rpath/{filename}", str(target)],
                        timeout=60,
                        check=False,
                    )
                except CommandError:
                    pass
        try:
            current = (
                run_capture([str(otool), "-l", str(target)], timeout=60).stdout if otool else ""
            )
        except CommandError:
            current = ""
        if "@loader_path/../lib" not in current:
            run_capture(
                [
                    str(install_name_tool),
                    "-add_rpath",
                    "@loader_path/../lib",
                    str(target),
                ],
                timeout=60,
                check=False,
            )
        codesign = which("codesign")
        if codesign:
            run_capture(
                [str(codesign), "--force", "--sign", "-", str(target)],
                timeout=120,
                check=False,
            )
            for filename in copied:
                run_capture(
                    [str(codesign), "--force", "--sign", "-", str(lib_dir / filename)],
                    timeout=120,
                    check=False,
                )
    else:
        warn("install_name_tool is not available; use 'xcode-select --install'")

    success(f"bundle written to {destination}")
    if copied:
        info(f"runtime libraries: {', '.join(copied)}")
    info(f"run with: {target}")
    return destination


def _bundle_windows(env: Environment, binary: Path, destination: Path, info_obj) -> Path:
    bin_dir = destination / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / binary.name
    _copy_binary(binary, target)

    copied: list[str] = []
    for filename in info_obj.needed:
        if filename.lower() in _WINDOWS_SKIP or filename.lower().startswith("api-ms-win-"):
            continue
        source = find_runtime_library(env, filename)
        if source is None:
            # MinGW runtime DLLs sometimes live directly in Library/bin with
            # a different capitalization.
            for directory in _library_search_dirs(env):
                for candidate in directory.glob(filename):
                    source = candidate.resolve()
                    break
                if source:
                    break
        if source is None:
            continue
        _copy_binary(source, bin_dir / filename)
        copied.append(filename)

    success(f"bundle written to {destination}")
    if copied:
        info(f"runtime DLLs: {', '.join(copied)}")
    else:
        info("no environment DLLs were required (fully/static runtime)")
    info(f"run with: {target}")
    return destination
