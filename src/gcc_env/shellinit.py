"""Shell integration: wrapper functions and profile installation."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .errors import UsageError
from .util import hex_quote_ps, run_capture, shell_basename, write_text

START = "# >>> gcc-env shell integration >>>"
END = "# <<< gcc-env shell integration <<<"

_FUNCTION_BASH = """\
# gcc-env shell integration (bash/zsh)
GE_CLI={binary}
ge() {{
    if [ "$#" -gt 0 ] && {{ [ "$1" = "activate" ] || [ "$1" = "deactivate" ]; }}; then
        local __ge_code
        __ge_code="$(GE_SHELL={shell} command "$GE_CLI" "$@")" || return $?
        if [ -n "$__ge_code" ]; then
            eval "$__ge_code"
        fi
    else
        command "$GE_CLI" "$@"
    fi
}}
if [ -z "${{GE_ACTIVE:-}}" ] && [ -z "${{GE_NO_AUTO_ACTIVATE:-}}" ]; then
    ge activate --auto >/dev/null 2>&1 || true
fi
"""

_FUNCTION_FISH = """\
# gcc-env shell integration (fish)
set -g __GE_CLI {binary}
function ge
    if test (count $argv) -gt 0; and contains -- $argv[1] activate deactivate
        set -lx GE_SHELL fish
        set -l __ge_code (command $__GE_CLI $argv)
        set -l __ge_status $status
        set -e GE_SHELL
        if test $__ge_status -ne 0
            return $__ge_status
        end
        if test (count $__ge_code) -gt 0
            printf '%s\\n' $__ge_code | source
        end
    else
        command $__GE_CLI $argv
    end
end
if not set -q GE_ACTIVE; and not set -q GE_NO_AUTO_ACTIVATE
    ge activate --auto >/dev/null 2>&1
end
"""

_FUNCTION_POWERSHELL = """\
# gcc-env shell integration (PowerShell)
$global:__GE_CLI = {binary}
function global:ge {{
    $geExe = $global:__GE_CLI
    if (-not $geExe) {{
        Write-Error "ge executable not found in PATH"
        return
    }}
    if ($args.Count -gt 0 -and ($args[0] -eq 'activate' -or $args[0] -eq 'deactivate')) {{
        $env:GE_SHELL = 'powershell'
        $code = & $geExe @args
        if ($LASTEXITCODE -ne 0) {{ return }}
        if ($code) {{ Invoke-Expression ($code -join [Environment]::NewLine) }}
    }} else {{
        & $geExe @args
    }}
}}
if (-not $env:GE_ACTIVE -and -not $env:GE_NO_AUTO_ACTIVATE) {{
    ge activate --auto 2>$null
}}
"""


def normalize_shell(shell: str) -> str:
    name = shell_basename(shell)
    if name in {"sh", "dash", "ksh"}:
        return "bash"
    if name in {"pwsh", "powershell", "powershell.exe"}:
        return "powershell"
    if name in {"bash", "zsh", "fish", "cmd", "cmd.exe"}:
        return name
    raise UsageError(f"unsupported shell: {shell}")


def render(shell: str, executable: str | None = None) -> str:
    shell = normalize_shell(shell)
    binary = executable or find_ge_executable() or "ge"
    if shell in {"bash", "zsh"}:
        quoted = "'" + binary.replace("'", "'\"'\"'") + "'"
        return _FUNCTION_BASH.format(shell=shell, binary=quoted)
    if shell == "fish":
        quoted = "'" + binary.replace("\\", "\\\\").replace("'", "\\'") + "'"
        return _FUNCTION_FISH.format(binary=quoted)
    if shell == "powershell":
        return _FUNCTION_POWERSHELL.format(binary=hex_quote_ps(binary))
    raise UsageError(
        "cmd.exe has no shell-function mechanism; use PowerShell or WSL",
        hint="Run 'ge activate --shell cmd' and use the printed set commands manually.",
    )


def _profile_from_pwsh() -> Path | None:
    exe = shutil.which("pwsh")
    if not exe:
        return None
    return _query_profile(exe)


def _profile_from_windows_powershell() -> Path | None:
    exe = shutil.which("powershell") or shutil.which("powershell.exe")
    if not exe:
        return None
    return _query_profile(exe)


def _query_profile(executable: str) -> Path | None:
    try:
        result = run_capture(
            [executable, "-NoLogo", "-NoProfile", "-Command", "$PROFILE"],
            timeout=30,
        )
    except Exception:
        return None
    value = result.stdout.strip().strip('"')
    if not value:
        return None
    return Path(value)


def _simple_profile(shell: str) -> Path:
    home = Path.home()
    if shell == "bash":
        return home / ".bashrc"
    if shell == "zsh":
        zdotdir = os.environ.get("ZDOTDIR")
        return (Path(zdotdir) if zdotdir else home) / ".zshrc"
    if shell == "fish":
        config_home = os.environ.get("XDG_CONFIG_HOME")
        base = Path(config_home) if config_home else home / ".config"
        return base / "fish" / "config.fish"
    raise UsageError(f"no profile path for shell: {shell}")


def default_profiles(shell: str) -> list[Path]:
    """Return all profiles that should receive the hook for this shell."""
    shell = normalize_shell(shell)
    if shell != "powershell":
        return [_simple_profile(shell)]

    profiles: list[Path] = []
    for getter in (_profile_from_pwsh, _profile_from_windows_powershell):
        profile = getter()
        if profile is not None:
            profiles.append(profile)

    # Cover Windows PowerShell 5.1 and PowerShell 7 even if one executable is
    # not currently on PATH (for example when installing from a restricted
    # terminal). The queried $PROFILE paths above win when available.
    if sys.platform == "win32":
        for edition in ("PowerShell", "WindowsPowerShell"):
            fallback = Path.home() / "Documents" / edition / "Microsoft.PowerShell_profile.ps1"
            if fallback not in profiles:
                profiles.append(fallback)

    if not profiles:
        profiles.append(Path.home() / ".config" / "powershell" / "profile.ps1")

    deduped: list[Path] = []
    seen: set[str] = set()
    for profile in profiles:
        key = os.path.normcase(os.path.abspath(str(profile)))
        if key not in seen:
            seen.add(key)
            deduped.append(profile)
    return deduped


def default_profile(shell: str) -> Path:
    shell = normalize_shell(shell)
    if shell == "powershell":
        return default_profiles(shell)[0]
    return _simple_profile(shell)


def _command_line(shell: str, executable: str | None = None) -> str:
    shell = normalize_shell(shell)
    binary = executable or "ge"
    if shell in {"bash", "zsh"}:
        quoted = "'" + binary.replace("'", "'\"'\"'") + "'"
        return f'eval "$({quoted} shell-init --shell {shell})"'
    if shell == "fish":
        quoted = "'" + binary.replace("\\", "\\\\").replace("'", "\\'") + "'"
        return f"{quoted} shell-init --shell fish | source"
    if shell == "powershell":
        return (
            f"Invoke-Expression ((& {hex_quote_ps(binary)} "
            "shell-init --shell powershell) -join [Environment]::NewLine)"
        )
    raise UsageError(f"shell not supported for installation: {shell}")


def find_ge_executable() -> str | None:
    """Find the installed console script so profile hooks work without PATH."""
    found = shutil.which("ge")
    if found:
        return str(Path(found).resolve())
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 and argv0.name.lower() in {"ge", "ge.exe"} and argv0.exists():
        return str(argv0.resolve())
    return None


def _replace_block(text: str, block: str) -> str:
    start = text.find(START)
    end = text.find(END)
    if start != -1 and end != -1 and end > start:
        end += len(END)
        return text[:start] + block + text[end:]
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        text += "\n"
    return text + block


def install(shell: str, profile: Path | None = None) -> Path:
    shell = normalize_shell(shell)
    destination = profile or default_profile(shell)
    destination.parent.mkdir(parents=True, exist_ok=True)
    executable = find_ge_executable()
    block = f"{START}\n{_command_line(shell, executable)}\n{END}\n"
    existing = (
        destination.read_text(encoding="utf-8", errors="replace") if destination.exists() else ""
    )
    updated = _replace_block(existing, block)
    write_text(destination, updated)
    return destination


def install_all(shell: str, profile: Path | None = None) -> list[Path]:
    shell = normalize_shell(shell)
    targets = [profile] if profile else default_profiles(shell)
    return [install(shell, target) for target in targets]


def uninstall(shell: str, profile: Path | None = None) -> Path:
    shell = normalize_shell(shell)
    destination = profile or default_profile(shell)
    if not destination.exists():
        return destination
    text = destination.read_text(encoding="utf-8", errors="replace")
    start = text.find(START)
    end = text.find(END)
    if start == -1 or end == -1 or end <= start:
        return destination
    end += len(END)
    while end < len(text) and text[end] in "\r\n":
        end += 1
    write_text(destination, text[:start].rstrip() + "\n" + text[end:])
    return destination


def uninstall_all(shell: str, profile: Path | None = None) -> list[Path]:
    shell = normalize_shell(shell)
    targets = [profile] if profile else default_profiles(shell)
    return [uninstall(shell, target) for target in targets]
