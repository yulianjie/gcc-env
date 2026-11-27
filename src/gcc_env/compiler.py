"""Discover the actual compiler executables inside a conda prefix."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import EnvError
from .platforms import Platform
from .util import executable, run_capture

_C_RE = re.compile(r"^(?:(?P<target>.+?)-)?(?:gcc|cc)(?P<suffix>-\d+(?:\.\d+)*)?$")
_CXX_RE = re.compile(r"^(?:(?P<target>.+?)-)?(?:g\+\+|c\+\+)(?P<suffix>-\d+(?:\.\d+)*)?$")
_VERSION_RE = re.compile(r"(?<![\d.])(\d+\.\d+(?:\.\d+)?)")
_VERSION_FROM_NAME_RE = re.compile(r"-(\d+(?:\.\d+){1,2})$")


@dataclass
class Toolchain:
    cc: Path
    cxx: Path
    version: str
    target: str
    sysroot: Path | None = None
    tools: dict[str, Path] = field(default_factory=dict)

    def describe(self) -> str:
        return f"GCC {self.version} ({self.target})"


def _candidate_dirs(prefix: Path, platform: Platform) -> list[Path]:
    names = ["bin"]
    if platform.is_windows:
        names = [
            "Library/bin",
            "Library/mingw-w64/bin",
            "Library/usr/bin",
            "bin",
            "Scripts",
        ]
    dirs = [prefix / name for name in names]
    return [directory for directory in dirs if directory.is_dir()]


def _activation_vars(prefix: Path) -> dict[str, Path]:
    """Parse CC/CXX exported by conda-forge's compiler activation scripts."""
    found: dict[str, Path] = {}
    activation = prefix / "etc" / "conda" / "activate.d"
    if not activation.is_dir():
        return found
    patterns = [
        re.compile(r'"(CC|CXX),([^"\n]+)"'),
        re.compile(r'(?:export|set)\s+"?(CC|CXX)=([^"\r\n]+)"?'),
        re.compile(r'\$env:(CC|CXX)\s*=\s*"([^"]+)"'),
    ]
    for script in activation.iterdir():
        if not script.is_file():
            continue
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in patterns:
            for name, value in pattern.findall(text):
                value = value.strip().strip('"')
                value = value.replace("${CONDA_PREFIX}", str(prefix))
                value = value.replace("$CONDA_PREFIX", str(prefix))
                value = value.replace("${PREFIX}", str(prefix))
                if "$" in value or "%" in value:
                    continue
                path = Path(value)
                if not path.is_absolute():
                    path = prefix / path
                if path.exists():
                    found.setdefault(name, path)
    return found


def _strip_exe(name: str) -> str:
    return name[:-4] if name.lower().endswith(".exe") else name


def _scan_candidates(prefix: Path, platform: Platform):
    c_candidates: dict[tuple[str, str], Path] = {}
    cxx_candidates: dict[tuple[str, str], Path] = {}
    for directory in _candidate_dirs(prefix, platform):
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            if not path.is_file() or not executable(path):
                continue
            name = _strip_exe(path.name)
            c_match = _C_RE.match(name)
            cxx_match = _CXX_RE.match(name)
            if c_match:
                key = (c_match.group("target") or "", c_match.group("suffix") or "")
                c_candidates.setdefault(key, path)
            elif cxx_match:
                key = (cxx_match.group("target") or "", cxx_match.group("suffix") or "")
                cxx_candidates.setdefault(key, path)
    return c_candidates, cxx_candidates


def _probe(path: Path, cache: dict[Path, tuple[str, str] | None]) -> tuple[str, str] | None:
    if path in cache:
        return cache[path]
    try:
        dumpversion = run_capture([path, "-dumpversion"], timeout=30)
        version = dumpversion.stdout.strip().splitlines()[0].strip()
        if not _VERSION_RE.match(version):
            version = ""
    except Exception:
        version = ""
    try:
        dumpmachine = run_capture([path, "-dumpmachine"], timeout=30)
        target = dumpmachine.stdout.strip().splitlines()[0].strip()
    except Exception:
        target = ""
    if not version:
        try:
            result = run_capture([path, "--version"], timeout=30)
            first = (result.stdout or result.stderr).splitlines()[0]
            match = _VERSION_RE.search(first)
            version = match.group(1) if match else ""
            if not target:
                target_match = re.search(r"target:\s*(\S+)", result.stdout or result.stderr)
                if target_match:
                    target = target_match.group(1)
        except Exception:
            cache[path] = None
            return None
    if not version:
        cache[path] = None
        return None
    cache[path] = (version, target)
    return version, target


def _pair_score(target: str, version: str, path: Path) -> tuple:
    target_bonus = 10 if target else 0
    if any(token in target for token in ("conda", "w64", "mingw", "apple", "linux-gnu")):
        target_bonus += 5
    return (target_bonus, tuple(int(p) for p in version.split(".") if p.isdigit()), str(path))


def discover_toolchain(prefix: Path, platform: Platform) -> Toolchain:
    cache: dict[Path, tuple[str, str] | None] = {}
    activated = _activation_vars(prefix)

    cc = activated.get("CC")
    cxx = activated.get("CXX")
    if cc and not cxx:
        cxx = Path(str(cc).replace("gcc", "g++"))
    if cxx and not cc:
        cc = Path(str(cxx).replace("g++", "gcc"))
    if cc and cxx and (_probe(cc, cache) and _probe(cxx, cache)):
        version, target = _probe(cc, cache) or ("unknown", "")
    else:
        c_candidates, cxx_candidates = _scan_candidates(prefix, platform)
        paired: list[tuple[tuple, Path, Path, str, str]] = []
        for key, c_path in c_candidates.items():
            cxx_path = cxx_candidates.get(key)
            if cxx_path is None:
                continue
            c_info = _probe(c_path, cache)
            cxx_info = _probe(cxx_path, cache)
            if not c_info or not cxx_info:
                continue
            version, target = c_info
            if not target:
                target = cxx_info[1]
            paired.append((_pair_score(key[0], version, c_path), c_path, cxx_path, version, target))
        if not paired:
            # Last resort: match any C and C++ executable that agree on target.
            for c_path in c_candidates.values():
                c_info = _probe(c_path, cache)
                if not c_info:
                    continue
                for cxx_path in cxx_candidates.values():
                    cxx_info = _probe(cxx_path, cache)
                    if cxx_info and cxx_info[0].split(".")[:2] == c_info[0].split(".")[:2]:
                        paired.append(
                            (
                                _pair_score("", c_info[0], c_path),
                                c_path,
                                cxx_path,
                                c_info[0],
                                c_info[1],
                            )
                        )
        if not paired:
            listing = []
            for directory in _candidate_dirs(prefix, platform):
                try:
                    listing.extend(sorted(p.name for p in directory.iterdir())[:12])
                except OSError:
                    pass
            raise EnvError(
                "the conda environment was created but no GCC compiler was found",
                hint=(
                    "Expected a gcc/g++ executable under "
                    f"{', '.join(str(p) for p in _candidate_dirs(prefix, platform))}."
                    + (f" Found: {', '.join(listing)}" if listing else "")
                ),
            )
        paired.sort(reverse=True)
        _, cc, cxx, version, target = paired[0]

    assert cc is not None and cxx is not None
    if not target:
        try:
            target = run_capture([cc, "-dumpmachine"], timeout=30).stdout.strip()
        except Exception:
            target = ""

    tools = _find_binutils(prefix, platform, target)
    sysroot = _find_sysroot(prefix, cc, target)
    return Toolchain(
        cc=cc,
        cxx=cxx,
        version=version,
        target=target,
        sysroot=sysroot,
        tools=tools,
    )


def _find_sysroot(prefix: Path, cc: Path, target: str) -> Path | None:
    try:
        printed = run_capture([cc, "-print-sysroot"], timeout=30).stdout.strip()
    except Exception:
        printed = ""
    candidates: list[Path] = []
    if printed:
        candidates.append(Path(printed))
    if target:
        candidates.append(prefix / target / "sysroot")
    candidates.append(prefix / "sysroot")
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "usr" / "include").is_dir():
            return candidate
    if target and prefix.is_dir():
        for child in prefix.iterdir():
            if (
                child.is_dir()
                and (child / "sysroot" / "usr" / "include").is_dir()
                and target.split("-")[0] in child.name
            ):
                return child / "sysroot"
    return None


def _find_binutils(
    prefix: Path,
    platform: Platform,
    target: str,
) -> dict[str, Path]:
    tools: dict[str, Path] = {}
    directories = _candidate_dirs(prefix, platform)
    prefixes = [f"{target}-"] if target else []
    prefixes.append("")
    for tool in ("ar", "ld", "nm", "ranlib", "objdump", "readelf", "strip", "as"):
        for directory in directories:
            for name_prefix in prefixes:
                candidate = directory / f"{name_prefix}{tool}{platform.exe}"
                if candidate.exists() and candidate.is_file():
                    tools[tool] = candidate
                    break
            if tool in tools:
                break
    return tools


def compiler_specs(platform: Platform, gcc: str, sysroot: str | None) -> list[str]:
    suffix = platform.conda_subdir.split("-", 1)[1]  # 64, aarch64, arm64

    def pin(name: str, version: str | None) -> str:
        if not version or version == "latest":
            return name
        return f"{name}={version}"

    if platform.is_linux:
        specs = [
            pin(f"gcc_linux-{suffix}", gcc),
            pin(f"gxx_linux-{suffix}", gcc),
        ]
        if sysroot:
            specs.append(pin(f"sysroot_linux-{suffix}", sysroot))
        specs.append("patchelf")
        return specs
    if platform.is_macos:
        # gcc_osx-* is the native conda-forge GCC toolchain.
        return [
            pin(f"gcc_osx-{suffix}", gcc),
            pin(f"gxx_osx-{suffix}", gcc),
        ]
    if platform.is_windows:
        return [pin("gcc_win-64", gcc), pin("gxx_win-64", gcc)]
    raise EnvError(f"unsupported platform: {platform.conda_subdir}")


def default_env_name(gcc: str) -> str:
    spec = gcc.strip()
    if spec.startswith("="):
        spec = spec[1:]
    if not spec or spec == "latest":
        return "gcc-latest"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", spec).strip("-")
    return f"gcc-{cleaned}" if cleaned else "gcc"


_COMPILE_ONLY_FLAGS = (
    "-c",
    "-S",
    "-E",
    "-M",
    "-MM",
    "-fsyntax-only",
    "--version",
    "-dumpversion",
    "-dumpmachine",
    "-print-search-dirs",
    "-print-sysroot",
    "-print-file-name",
    "-print-prog-name",
)


def is_link_invocation(args: list[str]) -> bool:
    for arg in args:
        if arg in _COMPILE_ONLY_FLAGS:
            return False
        if arg.startswith("-M") and not arg.startswith("-m"):
            return False
    return True


def runtime_flags(platform: Platform, runtime: str, *, shared: bool = False) -> list[str]:
    if runtime == "static":
        if platform.is_windows and not shared:
            # A fully static MinGW runtime removes the libstdc++/libgcc/
            # libwinpthread DLL dependencies altogether.
            return ["-static", "-static-libstdc++", "-static-libgcc"]
        return ["-static-libstdc++", "-static-libgcc"]
    if runtime == "bundle":
        if platform.is_macos:
            return ["-Wl,-rpath,@loader_path/../lib"]
        if platform.is_windows:
            return []
        return ["-Wl,-rpath,$ORIGIN/../lib"]
    return []


def build_command(
    env,
    *,
    language: str,
    args: list[str],
    runtime: str | None = None,
    compiler: Path | None = None,
) -> list[str]:
    """Build the compiler command, injecting runtime policy flags on link."""
    from .envs import Environment

    assert isinstance(env, Environment)
    selected = runtime or env.runtime
    if compiler is None:
        compiler = env.cxx if language == "c++" else env.cc
    flags: list[str] = []
    if is_link_invocation(args):
        shared = any(arg in {"-shared", "-Wl,-shared"} for arg in args)
        flags = runtime_flags(env.config.platform, selected, shared=shared)
    return [str(compiler), *flags, *args]
