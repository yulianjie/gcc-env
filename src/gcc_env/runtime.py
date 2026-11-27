"""Binary ABI inspection and the `ge doctor` compatibility checks."""

from __future__ import annotations

import glob
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .activation import toolchain_env
from .compiler import build_command
from .envs import Environment
from .errors import CommandError
from .util import compare_versions, max_version, run_capture, which

_VERSION_NAME_RE = re.compile(r"\b((?:GLIBC|GLIBCXX|CXXABI)_[0-9]+(?:\.[0-9]+)+)\b")
_ELF_MAGIC = b"\x7fELF"


@dataclass
class BinaryInfo:
    path: Path
    format: str
    dynamic: bool
    needed: list[str] = field(default_factory=list)
    rpath: list[str] = field(default_factory=list)
    runpath: list[str] = field(default_factory=list)
    glibc: list[str] = field(default_factory=list)
    glibcxx: list[str] = field(default_factory=list)
    cxxabi: list[str] = field(default_factory=list)

    @property
    def max_glibc(self) -> str | None:
        return max_version(self.glibc, "GLIBC_")

    @property
    def max_glibcxx(self) -> str | None:
        return max_version(self.glibcxx, "GLIBCXX_")

    @property
    def max_cxxabi(self) -> str | None:
        return max_version(self.cxxabi, "CXXABI_")

    @property
    def needs_libstdcxx(self) -> bool:
        return any(name.startswith("libstdc++") for name in self.needed)

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "format": self.format,
            "dynamic": self.dynamic,
            "needed": self.needed,
            "rpath": self.rpath,
            "runpath": self.runpath,
            "glibc": sorted(self.glibc),
            "glibcxx": sorted(self.glibcxx),
            "cxxabi": sorted(self.cxxabi),
            "max_glibc": self.max_glibc,
            "max_glibcxx": self.max_glibcxx,
            "max_cxxabi": self.max_cxxabi,
        }


def _tool(env: Environment, name: str) -> str | None:
    candidate = env.gcc_tools.get(name)
    if candidate and candidate.exists():
        return str(candidate)
    suffix = env.config.platform.exe
    found = which(f"{name}{suffix}")
    return str(found) if found else None


def _parse_dynamic(text: str) -> tuple[list[str], list[str], list[str], bool]:
    needed: list[str] = []
    rpath: list[str] = []
    runpath: list[str] = []
    dynamic = False
    for line in text.splitlines():
        if "(NEEDED)" in line:
            match = re.search(r"\[([^\]]+)\]", line)
            if match:
                needed.append(match.group(1))
                dynamic = True
        if "(RPATH)" in line or "(RUNPATH)" in line:
            match = re.search(r"\[([^\]]+)\]", line)
            if match:
                values = [
                    item.strip() for item in re.split(r"[:;]", match.group(1)) if item.strip()
                ]
                if "(RPATH)" in line:
                    rpath.extend(values)
                else:
                    runpath.extend(values)
                dynamic = True
    return needed, rpath, runpath, dynamic


def _read_elf(path: Path, env: Environment) -> BinaryInfo:
    readelf = _tool(env, "readelf")
    needed: list[str] = []
    rpath: list[str] = []
    runpath: list[str] = []
    dynamic = False
    output = ""
    if readelf:
        try:
            dynamic_text = run_capture([readelf, "-dW", str(path)], timeout=60, check=False)
            output = dynamic_text.stdout or ""
            needed, rpath, runpath, dynamic = _parse_dynamic(output)
        except CommandError:
            pass
    if not needed:
        # objdump -p is a useful fallback when a conda env lacks readelf.
        objdump = _tool(env, "objdump")
        if objdump:
            try:
                result = run_capture([objdump, "-p", str(path)], timeout=60)
                for line in result.stdout.splitlines():
                    match = re.search(r"DLL Name:\s*(\S+)", line)
                    if match:
                        needed.append(match.group(1))
                        dynamic = True
                for line in result.stdout.splitlines():
                    if "RPATH" in line or "RUNPATH" in line:
                        match = re.search(r"\[([^\]]+)\]", line)
                        if match:
                            paths = [
                                item.strip()
                                for item in re.split(r"[:;]", match.group(1))
                                if item.strip()
                            ]
                            (rpath if "RPATH" in line else runpath).extend(paths)
            except CommandError:
                pass

    glibc: list[str] = []
    glibcxx: list[str] = []
    cxxabi: list[str] = []
    if readelf:
        try:
            version_text = run_capture(
                [readelf, "--version-info", "-W", str(path)], timeout=120, check=False
            ).stdout
            for name in _VERSION_NAME_RE.findall(version_text):
                if name.startswith("GLIBCXX_"):
                    glibcxx.append(name)
                elif name.startswith("CXXABI_"):
                    cxxabi.append(name)
                elif name.startswith("GLIBC_"):
                    glibc.append(name)
        except CommandError:
            pass
    if not (glibc or glibcxx or cxxabi):
        # Last-resort extraction from the raw file. Version names live in the
        # .gnu.version_r section as NUL-terminated strings.
        try:
            raw = path.read_bytes()
        except OSError:
            raw = b""
        for match in re.finditer(rb"(GLIBCXX|CXXABI|GLIBC)_[0-9]+(?:\.[0-9]+)+", raw):
            name = match.group(0).decode("ascii", "ignore")
            if name.startswith("GLIBCXX_"):
                glibcxx.append(name)
            elif name.startswith("CXXABI_"):
                cxxabi.append(name)
            else:
                glibc.append(name)

    return BinaryInfo(
        path=path,
        format="elf",
        dynamic=dynamic,
        needed=sorted(set(needed)),
        rpath=sorted(set(rpath)),
        runpath=sorted(set(runpath)),
        glibc=sorted(set(glibc)),
        glibcxx=sorted(set(glibcxx)),
        cxxabi=sorted(set(cxxabi)),
    )


def _read_pe(path: Path, env: Environment) -> BinaryInfo:
    needed: list[str] = []
    objdump = _tool(env, "objdump")
    if objdump:
        try:
            result = run_capture([objdump, "-p", str(path)], timeout=60)
        except CommandError:
            result = None
        if result:
            for line in result.stdout.splitlines():
                match = re.search(r"DLL Name:\s*(\S+)", line)
                if match:
                    needed.append(match.group(1))
    return BinaryInfo(
        path=path,
        format="pe",
        dynamic=bool(needed),
        needed=sorted(set(needed)),
    )


def _read_macho(path: Path) -> BinaryInfo:
    needed: list[str] = []
    rpath: list[str] = []
    otool = which("otool")
    if otool:
        try:
            result = run_capture([str(otool), "-L", str(path)], timeout=60)
            for line in result.stdout.splitlines()[1:]:
                match = re.match(r"\s+(\S+)", line)
                if match:
                    needed.append(Path(match.group(1)).name)
        except CommandError:
            pass
        try:
            result = run_capture([str(otool), "-l", str(path)], timeout=60)
            lines = result.stdout.splitlines()
            for index, line in enumerate(lines):
                if "cmd LC_RPATH" in line:
                    for following in lines[index + 1 : index + 5]:
                        match = re.search(r"path\s+(\S+)", following)
                        if match:
                            rpath.append(match.group(1))
                            break
        except CommandError:
            pass
    return BinaryInfo(
        path=path,
        format="macho",
        dynamic=bool(needed),
        needed=sorted(set(needed)),
        rpath=sorted(set(rpath)),
    )


def inspect_binary(path: Path, env: Environment) -> BinaryInfo:
    try:
        header = path.read_bytes()[:8]
    except OSError as exc:
        raise CommandError([str(path)], 2, str(exc)) from exc
    if header.startswith(_ELF_MAGIC):
        return _read_elf(path, env)
    if header.startswith(b"MZ"):
        return _read_pe(path, env)
    if header[:4] in {
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
    }:
        return _read_macho(path)
    return BinaryInfo(path=path, format="unknown", dynamic=False)


def library_versions(path: Path) -> tuple[set[str], set[str]]:
    """Return (GLIBCXX, CXXABI) version strings exported by a libstdc++."""
    try:
        raw = path.read_bytes()
    except OSError:
        return set(), set()
    glibcxx = {m.group(0).decode("ascii") for m in re.finditer(rb"GLIBCXX_3\.4\.[0-9]+", raw)}
    cxxabi = {m.group(0).decode("ascii") for m in re.finditer(rb"CXXABI_1\.3\.[0-9]+", raw)}
    return glibcxx, cxxabi


def system_libstdcxx() -> dict | None:
    if os.name == "nt":
        return None
    patterns = [
        "/usr/lib/*/libstdc++.so.6",
        "/usr/lib64/libstdc++.so.6",
        "/usr/lib/libstdc++.so.6",
        "/lib/*/libstdc++.so.6",
        "/lib64/libstdc++.so.6",
        "/usr/local/lib*/libstdc++.so.6",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(Path(p) for p in glob.glob(pattern))
    ldconfig = which("ldconfig")
    if ldconfig:
        try:
            result = run_capture([str(ldconfig), "-p"], timeout=30, check=False)
            for line in result.stdout.splitlines():
                if "libstdc++.so.6" in line and "=>" in line:
                    value = line.split("=>", 1)[1].strip()
                    if value:
                        candidates.append(Path(value))
        except CommandError:
            pass
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved = str(candidate.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        glibcxx, cxxabi = library_versions(candidate)
        if glibcxx:
            return {
                "path": resolved,
                "max_glibcxx": max_version(glibcxx, "GLIBCXX_"),
                "max_cxxabi": max_version(cxxabi, "CXXABI_"),
            }
    return None


_PROBE = r"""
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>
#if __GNUC__ >= 13
#include <format>
#endif

int main() {
    std::vector<std::string> values{"gcc-env", "probe"};
    std::cout << values.size() << " "
              << std::filesystem::path("/tmp/probe").filename().string();
#if __GNUC__ >= 13
    std::cout << " " << std::format("{}", 42);
#endif
    std::cout << "\n";
    return 0;
}
"""


def doctor(
    env: Environment,
    *,
    binary: Path | None = None,
    target_glibc: str | None = None,
    runtime: str | None = None,
) -> dict:
    checks: list[dict] = []

    def add(status: str, message: str, hint_text: str | None = None) -> None:
        entry = {"status": status, "message": message}
        if hint_text:
            entry["hint"] = hint_text
        checks.append(entry)

    add("ok", f"environment {env.name} ({env.config.platform.label})")
    if env.cc.exists():
        add("ok", f"compiler: {env.cc}")
    else:
        add("error", f"compiler is missing: {env.cc}")

    probe_output = ""
    binary_path = binary
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if binary_path is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="ge-doctor-")
        temp_path = Path(temp_dir.name)
        source = temp_path / "probe.cpp"
        source.write_text(_PROBE, encoding="utf-8")
        binary_path = temp_path / ("probe.exe" if env.config.platform.is_windows else "probe")
        command = build_command(
            env,
            language="c++",
            args=["-std=c++20", "-O2", str(source), "-o", str(binary_path)],
            runtime=runtime,
        )
        try:
            run_capture(command, env=toolchain_env(env), timeout=180)
            add("ok", "compiled a C++20 filesystem/format probe")
        except CommandError as exc:
            add("error", f"probe compilation failed: {exc}")
            if temp_dir:
                temp_dir.cleanup()
            return _finish(env, checks, binary_path=None, info=None, probe_output="")

    try:
        info = inspect_binary(binary_path, env)
    except CommandError as exc:
        add("error", f"cannot inspect binary: {exc}")
        info = None
    if info is not None:
        add(
            "ok",
            f"binary format: {info.format}; dynamic: {'yes' if info.dynamic else 'no'}",
        )
        if info.max_glibc:
            add("ok", f"maximum GLIBC symbol: {info.max_glibc}")
        if info.max_glibcxx:
            add("ok", f"maximum GLIBCXX symbol: {info.max_glibcxx}")
        if info.max_cxxabi:
            add("ok", f"maximum CXXABI symbol: {info.max_cxxabi}")

        target = target_glibc or env.sysroot_version
        if target:
            if info.max_glibc and compare_versions(info.max_glibc, target) > 0:
                add(
                    "error",
                    f"binary needs GLIBC {info.max_glibc} but target is {target}",
                    "Rebuild with a matching --sysroot (for example 2.17) and avoid "
                    "linking system libraries built for a newer glibc.",
                )
            else:
                add("ok", f"GLIBC requirements fit the target ({target})")

        if info.needs_libstdcxx:
            if env.config.platform.is_windows:
                bundled = [
                    name
                    for name in info.needed
                    if name.lower().startswith(("libstdc++", "libgcc_s", "libwinpthread"))
                    and (binary_path.parent / name).is_file()
                ]
                if bundled:
                    add(
                        "ok",
                        "MinGW runtime DLLs are present next to the binary: "
                        + ", ".join(sorted(bundled)),
                    )
                else:
                    add(
                        "warn",
                        "binary depends on the MinGW libstdc++/libgcc runtime DLLs",
                        "Use '--runtime static' or 'ge bundle' to ship the DLLs beside the exe.",
                    )
            elif env.config.platform.is_macos:
                add(
                    "warn",
                    "binary depends on the GCC libstdc++ dylib",
                    "Use 'ge bundle' to copy the dylibs and rewrite the install names.",
                )
            else:
                system = system_libstdcxx()
                if system is None:
                    add(
                        "warn",
                        "binary depends on libstdc++.so.6 and no system libstdc++ was found",
                        "Use '--runtime static' or 'ge bundle' for portable binaries.",
                    )
                elif info.max_glibcxx and system.get("max_glibcxx"):
                    if compare_versions(info.max_glibcxx, system["max_glibcxx"]) > 0:
                        add(
                            "error",
                            f"binary requires {info.max_glibcxx}, but the system "
                            f"libstdc++ only provides {system['max_glibcxx']} "
                            f"({system['path']})",
                            "Rebuild with '--runtime static', bundle the runtime with "
                            "'ge bundle', or run it with the gcc-env activation active.",
                        )
                    else:
                        add(
                            "ok",
                            f"system libstdc++ provides {system['max_glibcxx']} "
                            f"(binary needs {info.max_glibcxx})",
                        )
                if (
                    info.max_cxxabi
                    and system
                    and system.get("max_cxxabi")
                    and compare_versions(info.max_cxxabi, system["max_cxxabi"]) > 0
                ):
                    add(
                        "error",
                        f"binary requires {info.max_cxxabi}, but the system "
                        f"libstdc++ only provides {system['max_cxxabi']}",
                        "Rebuild with '--runtime static' or use 'ge bundle'.",
                    )
        else:
            if env.config.platform.is_windows:
                add(
                    "ok",
                    "no MinGW runtime DLL dependency (static runtime); the PE imports "
                    "only the Windows UCRT",
                )
            elif env.config.platform.is_macos:
                add("ok", "no GCC libstdc++ dylib dependency (static runtime)")
            else:
                add(
                    "ok",
                    "no libstdc++.so.6 dependency (static runtime); portable across "
                    "systems with a compatible glibc",
                )

        absolute_rpaths = [
            value
            for value in [*info.rpath, *info.runpath]
            if value.startswith(str(env.conda_prefix))
        ]
        if absolute_rpaths:
            if info.needs_libstdcxx:
                add(
                    "warn",
                    "binary contains a development RPATH: "
                    + ", ".join(sorted(set(absolute_rpaths))),
                    "Use 'ge bundle' to copy runtime libraries and rewrite the RPATH for shipping.",
                )
            else:
                add(
                    "ok",
                    "development RPATH present but harmless with a static runtime",
                    "Run 'ge bundle' if you want to strip the build-machine path.",
                )

    # Basic environment/tooling checks.
    if env.python_enabled:
        python = env.python_path
        if python and python.exists():
            add("ok", f"Python venv: {python} ({env.python_version})")
        else:
            add("warn", "Python venv is enabled but the interpreter is missing")
        for tool in env.tools:
            tool_path = None
            if env.venv_bin():
                tool_name = f"{tool}.exe" if env.config.platform.is_windows else tool
                tool_path = env.venv_bin() / tool_name
            if tool_path and tool_path.exists():
                add("ok", f"uv-installed tool: {tool}")
            else:
                add("warn", f"uv tool is not present: {tool}")

    if binary_path and not binary:
        command = [str(binary_path)]
        try:
            result = run_capture(command, env=toolchain_env(env), timeout=60)
            probe_output = (result.stdout or "").strip()
            add("ok", f"probe runs successfully: {probe_output or '(no output)'}")
        except CommandError as exc:
            add("error", f"probe failed to run: {exc}")
        if temp_dir:
            temp_dir.cleanup()

    return _finish(env, checks, binary_path=binary_path, info=info, probe_output=probe_output)


def _finish(
    env: Environment,
    checks: list[dict],
    *,
    binary_path: Path | None,
    info: BinaryInfo | None,
    probe_output: str,
) -> dict:
    errors = sum(1 for check in checks if check["status"] == "error")
    warnings = sum(1 for check in checks if check["status"] == "warn")
    return {
        "ok": errors == 0,
        "env": env.summary(),
        "binary": str(binary_path) if binary_path else None,
        "probe_output": probe_output,
        "binary_info": info.as_dict() if info else None,
        "system_libstdcxx": system_libstdcxx(),
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }
