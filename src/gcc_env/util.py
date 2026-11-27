"""Small standard-library-only helpers used across gcc-env."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from .errors import CommandError, DownloadError, EnvError

# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

_COLOR = True
_USE_UNICODE = True

_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
}


def configure_streams(*, color: bool | None = None) -> None:
    """Configure stdout encoding and color support for this process."""
    global _COLOR, _USE_UNICODE
    if color is None:
        color = (
            sys.stderr.isatty()
            and os.environ.get("NO_COLOR") is None
            and os.environ.get("GE_NO_COLOR") is None
            and os.environ.get("TERM") != "dumb"
        )
    _COLOR = bool(color)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    _USE_UNICODE = "utf" in encoding or sys.platform != "win32"


def colored(text: str, *styles: str) -> str:
    if not _COLOR or not styles:
        return text
    codes = ";".join(_CODES[s] for s in styles if s in _CODES)
    if not codes:
        return text
    return f"\x1b[{codes}m{text}\x1b[0m"


def _emit(text: str, *, stream=sys.stderr) -> None:
    print(text, file=stream, flush=True)


def info(message: str) -> None:
    _emit(f"{colored('i', 'blue', 'bold')} {message}")


def success(message: str) -> None:
    _emit(f"{colored('ok', 'green', 'bold')} {message}")


def warn(message: str) -> None:
    _emit(f"{colored('!', 'yellow', 'bold')} {message}")


def error(message: str) -> None:
    _emit(f"{colored('x', 'red', 'bold')} {message}")


def step(message: str) -> None:
    _emit(f"{colored('->', 'cyan', 'bold')} {message}")


def hint(message: str) -> None:
    _emit(f"   {colored(message, 'dim')}")


def header(message: str) -> None:
    _emit("")
    _emit(colored(message, "bold"))
    _emit(colored("-" * max(8, len(message)), "dim"))


def print_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    """Print a simple aligned table to stdout."""
    text_rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in text_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(colored(h.ljust(widths[i]), "bold") for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in text_rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def _sym(ok: str, fallback: str) -> str:
    return ok if _USE_UNICODE else fallback


# These are functions so the Unicode decision is made at call time.
def mark_ok() -> str:
    return _sym("✓", "[ok]")


def mark_warn() -> str:
    return _sym("!", "[warn]")


def mark_fail() -> str:
    return _sym("x", "[fail]")


# ---------------------------------------------------------------------------
# Files, text, and paths
# ---------------------------------------------------------------------------


def format_bytes(size: int | float) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TiB"


def which(name: str, extra_path: Iterable[Path | str] | None = None) -> Path | None:
    path = os.environ.get("PATH", "")
    if extra_path:
        path = os.pathsep.join([str(p) for p in extra_path] + ([path] if path else []))
    found = shutil.which(name, path=path)
    return Path(found) if found else None


def resolve_command(
    command: Sequence[str | os.PathLike[str]],
    env: dict[str, str] | None = None,
) -> list[str]:
    """Resolve argv[0] against the *target* environment's PATH.

    On Windows, CreateProcess searches the parent's PATH, not the PATH passed
    in the child environment, so `ge run -- cmake` needs this explicit lookup.
    """
    args = [str(item) for item in command]
    if not args:
        return args
    program = args[0]
    if os.path.dirname(program):
        return args
    search_env = os.environ if env is None else env
    path = search_env.get("PATH", "")
    extensions = [""]
    if os.name == "nt":
        extensions = [
            ext.lower()
            for ext in search_env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
            if ext
        ]
        if Path(program).suffix:
            extensions = [""] + extensions
    for directory in path.split(os.pathsep):
        if not directory:
            continue
        for extension in extensions:
            candidate = Path(directory) / f"{program}{extension}"
            if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return [str(candidate), *args[1:]]
    return args


def read_text(path: Path, *, default: str | None = None) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temp = Path(handle.name)
    os.replace(temp, path)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EnvError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EnvError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EnvError(f"expected a JSON object in {path}")
    return value


def write_json(path: Path, value: dict) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=False) + "\n")


def rmtree(path: Path) -> None:
    """Remove a directory tree, including read-only files on Windows."""
    if not path.exists() and not path.is_symlink():
        return

    def _onerror(func, target, exc_info):  # type: ignore[no-untyped-def]
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            raise

    if path.is_symlink():
        path.unlink()
        return
    shutil.rmtree(path, onerror=_onerror)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path: Path, text: str) -> None:
    write_text(path, text)


def safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if root != target and root not in target.parents:
                raise DownloadError(f"unsafe path in archive {archive.name}: {member.name}")
            if member.issym() or member.islnk():
                # Symlinks are not needed for the bootstrap archives and can
                # escape the extraction root; reject them.
                continue
        tar.extractall(destination)


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (destination / member.filename).resolve()
            if root != target and root not in target.parents:
                raise DownloadError(f"unsafe path in archive {archive.name}: {member.filename}")
        zf.extractall(destination)


def find_named_files(root: Path, names: set[str]) -> list[Path]:
    matches: list[Path] = []
    for directory, _dirs, files in os.walk(root):
        for filename in files:
            if filename in names:
                matches.append(Path(directory) / filename)
    return matches


def executable(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt":
        return path.suffix.lower() in {".exe", ".cmd", ".bat", ""}
    return os.access(path, os.X_OK)


def hex_quote_ps(value: str) -> str:
    """Quote a string for PowerShell single-quoted literals."""
    return "'" + value.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Process execution
# ---------------------------------------------------------------------------


def run_capture(
    command: Sequence[str | os.PathLike[str]],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    args = resolve_command(command, env)
    try:
        result = subprocess.run(
            args,
            env=env,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            input=input_text,
        )
    except FileNotFoundError as exc:
        raise CommandError(args, 127, str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(args, 124, f"timed out after {timeout}s") from exc
    if check and result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        raise CommandError(args, result.returncode, output)
    return result


def run_stream(
    command: Sequence[str | os.PathLike[str]],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    check: bool = True,
) -> int:
    args = resolve_command(command, env)
    try:
        result = subprocess.run(args, env=env, cwd=str(cwd) if cwd else None)
    except FileNotFoundError as exc:
        raise CommandError(args, 127, str(exc)) from exc
    except KeyboardInterrupt:
        raise
    if check and result.returncode != 0:
        raise CommandError(args, result.returncode)
    return result.returncode


def run_inherit(
    command: Sequence[str | os.PathLike[str]],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> int:
    """Run a command with inherited stdio, returning its exit code."""
    return run_stream(command, env=env, cwd=cwd, check=False)


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def _download_once(url: str, destination: Path, label: str, timeout: float) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"gcc-env/{_version_string()}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else 0
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = destination.with_name(destination.name + ".part")
            downloaded = 0
            started = time.monotonic()
            last_update = 0.0
            with temp.open("wb") as out:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if sys.stderr.isatty() and now - last_update > 0.15:
                        last_update = now
                        if total:
                            pct = downloaded * 100 // total
                            text = (
                                f"\r   {label}: {format_bytes(downloaded)} / "
                                f"{format_bytes(total)} ({pct}%)"
                            )
                        else:
                            text = f"\r   {label}: {format_bytes(downloaded)}"
                        sys.stderr.write(text)
                        sys.stderr.flush()
            if sys.stderr.isatty():
                sys.stderr.write("\r" + " " * 78 + "\r")
                sys.stderr.flush()
            os.replace(temp, destination)
            elapsed = max(time.monotonic() - started, 0.01)
            speed = downloaded / elapsed
            detail = f"{label}: {format_bytes(downloaded)}"
            if total:
                detail += f" in {elapsed:.1f}s ({format_bytes(speed)}/s)"
            info(detail)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        temp = destination.with_name(destination.name + ".part")
        try:
            temp.unlink()
        except OSError:
            pass
        raise DownloadError(
            f"failed to download {url}: {exc}",
            hint="Check your network connection or set an HTTPS proxy. "
            "You can also pre-install micromamba/uv and put them on PATH.",
        ) from exc


def download(
    url: str,
    destination: Path,
    label: str | None = None,
    *,
    timeout: float = 60.0,
) -> Path:
    label = label or destination.name
    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            _download_once(url, destination, label, timeout)
            return destination
        except DownloadError:
            if attempt == attempts:
                raise
            warn(f"download failed, retrying ({attempt}/{attempts - 1})...")
            time.sleep(1.0)
    raise AssertionError("unreachable")


def _version_string() -> str:
    from . import __version__

    return __version__


# ---------------------------------------------------------------------------
# Versions and strings
# ---------------------------------------------------------------------------


_VERSION_RE = re.compile(r"(\d+(?:\.\d+){1,3})")


def version_tuple(value: str) -> tuple[int, ...]:
    match = _VERSION_RE.search(value)
    if not match:
        return ()
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def max_version(values: Iterable[str], prefix: str = "") -> str | None:
    candidates = [v for v in values if v.startswith(prefix) and version_tuple(v)]
    if not candidates:
        return None
    return max(candidates, key=version_tuple)


def compare_versions(left: str, right: str) -> int:
    """Return -1, 0, or 1 comparing dotted numeric versions."""
    a = version_tuple(left)
    b = version_tuple(right)
    return (a > b) - (a < b)


def shell_basename(shell: str) -> str:
    return Path(shell).name.lower().removesuffix(".exe")


def detect_shell() -> str:
    if os.name == "nt":
        return "powershell"
    shell = os.environ.get("SHELL", "")
    name = shell_basename(shell)
    if name in {"bash", "zsh", "fish", "sh", "dash", "ksh"}:
        return name
    return "bash"


def is_windows_path(path: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", path)) or path.startswith("\\\\")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def tail(text: str, lines: int = 20) -> str:
    return "\n".join(normalize_newlines(text).strip().splitlines()[-lines:])


# ---------------------------------------------------------------------------
# Interactive helpers and locking
# ---------------------------------------------------------------------------


def confirm(prompt: str, *, default: bool = False) -> bool:
    if not sys.stdin.isatty():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(prompt + suffix).strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in {"y", "yes"}


class FileLock:
    """A tiny cross-platform lock to avoid concurrent mutations of ~/.ge."""

    def __init__(self, path: Path, *, stale_after: float = 6 * 3600) -> None:
        self.path = path
        self.stale_after = stale_after
        self._fd: int | None = None

    def __enter__(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self._fd = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                os.write(self._fd, f"{os.getpid()} {time.time():.0f}\n".encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = self.stale_after + 1
                if age > self.stale_after:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                raise EnvError(
                    f"another gcc-env process is using {self.path.parent}",
                    hint=f"If no other ge command is running, delete {self.path} and retry.",
                ) from None
        raise EnvError(f"could not acquire lock: {self.path}")

    def __exit__(self, *_exc: object) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
        try:
            self.path.unlink()
        except OSError:
            pass
