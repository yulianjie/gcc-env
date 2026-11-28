"""Command-line interface for gcc-env (`ge`)."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from . import __version__
from .activation import activation_script, deactivation_script, prompt_label, shell_environment
from .bootstrap import Bootstrap
from .bundle import bundle as bundle_binary
from .cmake import render_toolchain, write_toolchain
from .compiler import build_command
from .config import Config
from .envs import Environment, EnvManager, validate_env_name
from .errors import GeError, UsageError
from .runtime import doctor as run_doctor
from .shellinit import install as install_shell_hook
from .shellinit import render as render_shell_hook
from .shellinit import uninstall as uninstall_shell_hook
from .state import current_session, ensure_session, pop_state, save_snapshot
from .util import (
    configure_streams,
    detect_shell,
    error,
    header,
    hint,
    info,
    mark_fail,
    mark_ok,
    mark_warn,
    print_table,
    run_inherit,
    shell_basename,
    success,
    which,
)

DESCRIPTION = """\
gcc-env (ge) manages isolated GCC/C++ toolchains on Linux, macOS and Windows.

It installs compilers from conda-forge with micromamba, creates a Python
virtual environment with uv for build tooling (cmake, ninja, ...), and provides
one-command switching plus portable-runtime helpers for libstdc++.
"""

EXAMPLES = """\
examples:
  ge setup                         bootstrap uv + micromamba and create "default"
  ge shell-init --install          enable direct activation in this shell
  ge create gcc13 --gcc 13.3.0     create a specific GCC environment
  ge activate gcc13                activate it (after shell-init)
  c++ --version                    bare compiler now uses the environment
  ge deactivate                    restore the previous shell environment
  ge use gcc13                     make it the current environment
  ge shell                         open a shell with CC/CXX configured
  ge c++ -- -std=c++20 main.cpp -o app
  eval "$(ge activate gcc13)"      fallback without the shell hook
  ge doctor                        check libstdc++/glibc portability
  ge bundle ./app ./dist           copy runtime libraries for shipping

Run 'ge <command> --help' for command-specific help.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ge",
        description=DESCRIPTION,
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"gcc-env {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("setup", help="bootstrap tools and create the default environment")
    p.add_argument("--name", default="default")
    p.add_argument("--gcc")
    p.add_argument("--sysroot")
    p.add_argument("--python")
    p.add_argument("--no-python", action="store_true")
    p.add_argument("--tools")
    p.add_argument("--runtime", choices=["static", "shared", "bundle"])
    p.add_argument("--recreate", action="store_true", help="recreate an existing default env")

    p = sub.add_parser("bootstrap", help="download micromamba and uv only")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("create", help="create a GCC environment")
    p.add_argument("name", nargs="?", help="environment name (default: gcc-<version>)")
    p.add_argument("--gcc", help="GCC version: 14, 14.3, 14.3.0, or latest")
    p.add_argument("--sysroot", help="Linux sysroot/glibc target, e.g. 2.17 (default 2.17)")
    p.add_argument("--python", help="Python version for the uv venv (default from config)")
    p.add_argument("--no-python", action="store_true", help="do not create a uv venv")
    p.add_argument("--tools", help="comma-separated uv tools (default: cmake,ninja)")
    p.add_argument("--runtime", choices=["static", "shared", "bundle"])
    p.add_argument("--use", action="store_true", help="make the new environment current")
    p.add_argument("--force", action="store_true", help="recreate an existing environment")

    p = sub.add_parser("list", aliases=["ls"], help="list environments")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("info", help="show environment details")
    p.add_argument("name", nargs="?")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("use", aliases=["switch"], help="select the current environment")
    p.add_argument("name")

    p = sub.add_parser("current", help="print the current environment name")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("remove", aliases=["rm"], help="delete an environment")
    p.add_argument("name")
    p.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")

    p = sub.add_parser("shell", help="open a shell with the toolchain activated")
    p.add_argument("name", nargs="?")
    p.add_argument("--shell", help="shell executable or bash/zsh/fish/pwsh/cmd")
    p.add_argument("--rc", action="store_true", help="load the shell's normal startup files")
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser(
        "run",
        help="run a command inside an environment (use '--' before the command)",
        add_help=False,
    )

    p = sub.add_parser("activate", help="activate an environment in the current shell")
    p.add_argument("name", nargs="?")
    p.add_argument(
        "--shell",
        default="auto",
        help="bash, zsh, fish, powershell, cmd, or auto (default)",
    )

    p = sub.add_parser("deactivate", help="restore the shell environment before activation")
    p.add_argument(
        "--shell",
        default="auto",
        help="bash, zsh, fish, powershell, cmd, or auto (default)",
    )

    p = sub.add_parser(
        "shell-init",
        help="print or install the shell hook that makes 'ge activate' work directly",
    )
    p.add_argument(
        "--shell",
        default="auto",
        help="bash, zsh, fish, powershell, or auto (default)",
    )
    p.add_argument(
        "--install",
        action="store_true",
        help="append a managed block to the shell profile",
    )
    p.add_argument("--uninstall", action="store_true", help="remove the managed profile block")
    p.add_argument("--profile", type=Path, help="override the profile path")

    p = sub.add_parser(
        "doctor",
        help="check compiler, libstdc++, and glibc portability",
        description=(
            "Compile and inspect a C++ probe (or an existing binary) to verify "
            "the compiler, glibc target, libstdc++ symbol versions, and RPATH."
        ),
    )
    p.add_argument("name", nargs="?")
    p.add_argument("--binary", type=Path, help="inspect an existing binary instead of a probe")
    p.add_argument("--target-glibc", help="target glibc version, e.g. 2.17")
    p.add_argument("--runtime", choices=["static", "shared", "bundle"])
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("bundle", help="copy a binary and its GCC runtime into a portable directory")
    p.add_argument("binary", type=Path)
    p.add_argument("--dest", type=Path, help="destination directory")
    p.add_argument("--env", help="environment that built the binary")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("toolchain", help="write or print a CMake toolchain file")
    p.add_argument("name", nargs="?")
    p.add_argument("--output", type=Path)
    p.add_argument("--print", dest="print_only", action="store_true")
    p.add_argument("--runtime", choices=["static", "shared", "bundle"])

    p = sub.add_parser("config", help="show effective configuration")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("version", help="print the gcc-env version")
    p.add_argument("--json", action="store_true")

    # Compiler subcommands are handled by a small dedicated parser in
    # _compiler_command(); these entries exist for discovery in --help.
    for name, help_text in (
        ("cc", "compile C with the selected environment"),
        ("c++", "compile C++ with the selected environment"),
        ("gcc", "alias for 'ge cc'"),
        ("g++", "alias for 'ge c++'"),
    ):
        p = sub.add_parser(name, help=help_text, add_help=False)

    return parser


def _parse_tools(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    tools = tuple(part.strip() for part in value.replace(" ", ",").split(",") if part.strip())
    return tools


def _config_from_env() -> Config:
    return Config.load()


def _manager() -> EnvManager:
    return EnvManager(_config_from_env())


def _resolve(manager: EnvManager, name: str | None) -> Environment:
    return manager.load(name) if name else manager.current()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_setup(args, config: Config, manager: EnvManager) -> int:
    boot = Bootstrap(config)
    boot.ensure()
    name = validate_env_name(args.name)
    if manager.env_root(name).exists() and not args.recreate:
        manager.load(name)
        manager.use(name)
        info(f"environment {name} already exists; selected it")
        return 0
    manager.create(
        name,
        gcc=args.gcc,
        sysroot=args.sysroot if args.sysroot is not None else ...,
        python=args.python,
        tools=_parse_tools(args.tools),
        runtime=args.runtime,
        with_python=not args.no_python,
        use=True,
        force=args.recreate,
    )
    return 0


def cmd_bootstrap(args, config: Config, manager: EnvManager) -> int:
    boot = Bootstrap(config)
    boot.ensure()
    versions = boot.versions()
    mamba = boot.micromamba()
    uv = boot.uv()
    if args.json:
        print(json.dumps({"config": config.as_dict(), "versions": versions}, indent=2))
    else:
        header("gcc-env bootstrap")
        print(f"root        : {config.root}")
        print(f"platform    : {config.platform.label} ({config.platform.conda_subdir})")
        print(f"micromamba  : {mamba}")
        print(f"uv          : {uv}")
        print(f"versions    : {versions['uv']}, {versions['micromamba']}")
        print(f"channels    : {', '.join(config.channels)}")
        print(f"config file : {config.config_path}")
    return 0


def cmd_create(args, config: Config, manager: EnvManager) -> int:
    manager.create(
        args.name,
        gcc=args.gcc,
        sysroot=args.sysroot if args.sysroot is not None else ...,
        python=args.python,
        tools=_parse_tools(args.tools),
        runtime=args.runtime,
        with_python=not args.no_python,
        use=args.use,
        force=args.force,
    )
    return 0


def cmd_list(args, config: Config, manager: EnvManager) -> int:
    envs = manager.list()
    try:
        current_name = manager.current().name
    except GeError:
        current_name = None
    if args.json:
        print(
            json.dumps(
                {
                    "root": str(config.root),
                    "current": current_name,
                    "environments": [env.to_json() for env in envs],
                },
                indent=2,
            )
        )
        return 0
    if not envs:
        info(f"no environments under {config.envs_dir}")
        hint("Run 'ge setup' to create the default GCC environment.")
        return 0
    header(f"gcc-env environments ({config.root})")
    rows = []
    for env in envs:
        rows.append(
            [
                env.name,
                env.gcc_version,
                env.gcc_target,
                env.sysroot_version or "-",
                env.python_version or "-",
                env.runtime,
                "*" if env.name == current_name else "",
            ]
        )
    print_table(
        ["NAME", "GCC", "TARGET", "GLIBC", "PYTHON", "RUNTIME", "CUR"],
        rows,
    )
    return 0


def _details(env: Environment) -> dict:
    return {
        "name": env.name,
        "path": str(env.root),
        "platform": env.meta.get("platform"),
        "gcc": {
            "requested": env.requested_gcc,
            "version": env.gcc_version,
            "target": env.gcc_target,
            "cc": str(env.cc),
            "cxx": str(env.cxx),
            "sysroot": str(env.sysroot) if env.sysroot else None,
        },
        "runtime": env.runtime,
        "python": {
            "enabled": env.python_enabled,
            "version": env.python_version,
            "venv": str(env.venv_dir) if env.python_enabled else None,
            "tools": env.tools,
        },
        "conda": {
            "prefix": str(env.conda_prefix),
            "packages": env.packages,
        },
        "created_at": env.meta.get("created_at"),
        "activate": (
            "Invoke-Expression ((ge activate "
            f"{env.name} --shell powershell) -join [Environment]::NewLine)"
            if env.config.platform.is_windows
            else f'eval "$(ge activate {env.name})"'
        ),
        "toolchain_file": str(env.root / "ge-toolchain.cmake"),
    }


def cmd_info(args, config: Config, manager: EnvManager) -> int:
    env = _resolve(manager, args.name)
    details = _details(env)
    if args.json:
        print(json.dumps(details, indent=2))
        return 0
    header(f"environment {env.name}")
    print(f"path        : {env.root}")
    print(f"platform    : {env.meta.get('platform')}")
    print(f"gcc         : {env.gcc_version}  (requested {env.requested_gcc})")
    print(f"target      : {env.gcc_target}")
    print(f"cc          : {env.cc}")
    print(f"c++         : {env.cxx}")
    if env.config.platform.is_linux:
        print(f"sysroot     : {env.sysroot or '-'}  (glibc target {env.sysroot_version or '-'})")
    else:
        print(f"sysroot     : {env.sysroot or '-'}")
    print(f"runtime     : {env.runtime}")
    if env.python_enabled:
        print(f"python      : {env.python_version or 'unknown'} ({env.venv_dir})")
        print(f"tools       : {', '.join(env.tools) or '-'}")
    else:
        print("python      : disabled")
    gcc_packages = {
        name: version
        for name, version in env.packages.items()
        if name.startswith(("gcc", "gxx", "sysroot", "binutils", "libstdcxx"))
    }
    if gcc_packages:
        print("conda       : " + ", ".join(f"{k}={v}" for k, v in sorted(gcc_packages.items())))
    print()
    print(f"activate    : {details['activate']}")
    print(f"cmake       : ge toolchain {env.name}")
    print(f"doctor      : ge doctor {env.name}")
    return 0


def cmd_use(args, config: Config, manager: EnvManager) -> int:
    manager.use(args.name)
    return 0


def cmd_current(args, config: Config, manager: EnvManager) -> int:
    env = manager.current()
    if args.json:
        print(json.dumps(env.to_json(), indent=2))
    else:
        print(env.name)
    return 0


def cmd_remove(args, config: Config, manager: EnvManager) -> int:
    manager.remove(args.name, yes=args.yes)
    return 0


def cmd_activate(args, config: Config, manager: EnvManager) -> int:
    env = _resolve(manager, args.name)
    env.ensure_shims()
    shell = args.shell
    if shell == "auto":
        shell = detect_shell()
    session = ensure_session()
    save_snapshot(config, session, env.name)
    script = activation_script(env, shell=shell, session=session)
    sys.stdout.write(script)
    if not os.environ.get("GE_SHELL") and sys.stderr.isatty():
        hint(
            "Shell integration is not active; this only printed shell code. "
            "Run 'ge shell-init --install' once to enable bare 'ge activate'."
        )
    return 0


def cmd_deactivate(args, config: Config, manager: EnvManager) -> int:
    shell = args.shell
    if shell == "auto":
        shell = detect_shell()
    session = current_session()
    state = pop_state(config, session)
    if state is None:
        info("no active gcc-env environment")
        return 0
    sys.stdout.write(deactivation_script(state["env"], shell=shell))
    if not os.environ.get("GE_SHELL") and sys.stderr.isatty():
        hint(
            "Shell integration is not active; this only printed shell code. "
            "Run 'ge shell-init --install' once to enable bare 'ge deactivate'."
        )
    return 0


def cmd_shell_init(args, config: Config, manager: EnvManager) -> int:
    shell = args.shell
    if shell == "auto":
        shell = detect_shell()
    if args.install and args.uninstall:
        raise UsageError("--install and --uninstall are mutually exclusive")
    if args.install or args.uninstall:
        if args.uninstall:
            destination = uninstall_shell_hook(shell, args.profile)
            info(f"removed gcc-env shell integration from {destination}")
        else:
            destination = install_shell_hook(shell, args.profile)
            success(f"installed gcc-env shell integration in {destination}")
            info("Restart the shell or source the profile to enable 'ge activate'.")
        print(destination)
        return 0
    sys.stdout.write(render_shell_hook(shell))
    return 0


def _shell_command(shell: str | None, *, rc: bool) -> list[str]:
    if shell is None:
        if os.name == "nt":
            for candidate in ("pwsh", "powershell", "cmd"):
                found = which(candidate)
                if found:
                    return _shell_argv(str(found), rc=rc)
            raise UsageError("no PowerShell or cmd executable was found")
        shell = os.environ.get("SHELL") or "/bin/bash"

    if os.sep in shell or (os.altsep and os.altsep in shell) or Path(shell).suffix == ".exe":
        path = Path(shell)
        if not path.exists():
            raise UsageError(f"shell not found: {shell}")
        return _shell_argv(str(path), rc=rc)

    names = {
        "bash": ["bash"],
        "zsh": ["zsh"],
        "fish": ["fish"],
        "sh": ["sh"],
        "pwsh": ["pwsh"],
        "powershell": ["powershell"],
        "cmd": ["cmd"],
        "cmd.exe": ["cmd"],
    }
    if shell not in names:
        found = which(shell)
        if not found:
            raise UsageError(f"unsupported shell: {shell}")
        return _shell_argv(str(found), rc=rc)
    found = which(names[shell][0])
    if not found:
        raise UsageError(f"shell not found on PATH: {shell}")
    return _shell_argv(str(found), rc=rc)


def _shell_argv(executable: str, *, rc: bool) -> list[str]:
    base = shell_basename(executable)
    if base == "bash" and not rc:
        return [executable, "--noprofile", "--norc", "-i"]
    if base == "zsh" and not rc:
        return [executable, "-f", "-i"]
    if base == "fish" and not rc:
        return [executable, "--no-config", "-i"]
    if base in {"pwsh", "powershell"} and not rc:
        return [executable, "-NoLogo", "-NoProfile"]
    if base in {"cmd", "cmd.exe"}:
        return [executable, "/K"]
    return [executable]


def cmd_shell(args, config: Config, manager: EnvManager) -> int:
    env = _resolve(manager, args.name)
    env.ensure_shims()
    command = _shell_command(args.shell, rc=args.rc)
    env_vars = shell_environment(env)
    if not args.quiet:
        header(f"gcc-env shell: {env.name}")
        info(prompt_label(env))
        info(f"CC={env.cc}")
        info("type 'exit' to leave the activated shell")
        print()
    return run_inherit(command, env=env_vars)


def cmd_doctor(args, config: Config, manager: EnvManager) -> int:
    env = _resolve(manager, args.name)
    result = run_doctor(
        env,
        binary=args.binary,
        target_glibc=args.target_glibc,
        runtime=args.runtime,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        header(f"ge doctor: {env.name}")
        for check in result["checks"]:
            status = check["status"]
            marker = {"ok": mark_ok(), "warn": mark_warn(), "error": mark_fail()}.get(status, "-")
            print(f"{marker:>4}  {check['message']}")
            if check.get("hint"):
                print(f"      {check['hint']}")
        print()
        if result["ok"]:
            success(
                f"doctor passed with {result['warnings']} warning(s)"
                if result["warnings"]
                else "doctor passed"
            )
        else:
            error(f"doctor found {result['errors']} error(s), {result['warnings']} warning(s)")
    return 0 if result["ok"] else 1


def cmd_bundle(args, config: Config, manager: EnvManager) -> int:
    env = manager.load(args.env) if args.env else manager.current()
    destination = bundle_binary(env, args.binary, args.dest, force=args.force)
    if not sys.stdout.isatty():
        print(destination)
    return 0


def cmd_toolchain(args, config: Config, manager: EnvManager) -> int:
    env = _resolve(manager, args.name)
    if args.print_only:
        sys.stdout.write(render_toolchain(env, runtime=args.runtime))
        return 0
    destination = write_toolchain(env, args.output, runtime=args.runtime)
    success(f"wrote {destination}")
    info(f"cmake -S . -B build -DCMAKE_TOOLCHAIN_FILE={destination}")
    print(destination)
    return 0


def cmd_config(args, config: Config, manager: EnvManager) -> int:
    data = config.as_dict()
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    header("gcc-env configuration")
    print(f"root             : {data['root']}")
    print(
        f"platform         : {data['platform']['system']} "
        f"{data['platform']['machine']} ({data['platform']['conda_subdir']})"
    )
    defaults = data["defaults"]
    print(f"default gcc      : {defaults['gcc']}")
    print(f"default sysroot  : {defaults['sysroot']}")
    print(f"default python   : {defaults['python']}")
    print(f"default runtime  : {defaults['runtime']}")
    print(f"default tools    : {', '.join(defaults['tools'])}")
    print(f"conda channels   : {', '.join(data['channels'])}")
    print(f"mamba root       : {data['mamba_root']}")
    print(f"config file      : {data['config_file']}")
    print()
    print("Environment variables: GE_ROOT, GE_GCC, GE_SYSROOT, GE_PYTHON,")
    print("GE_RUNTIME, GE_CONDA_CHANNELS, GE_MAMBA_ROOT, GE_UV, GE_MICROMAMBA.")
    return 0


def cmd_version(args, config: Config, manager: EnvManager) -> int:
    if args.json:
        print(json.dumps({"version": __version__, "python": sys.version.split()[0]}, indent=2))
    else:
        print(f"gcc-env {__version__}")
    return 0


# ---------------------------------------------------------------------------
# Manual parsers for compiler and run commands
# ---------------------------------------------------------------------------


def _compiler_help(kind: str, prog: str) -> str:
    language = "C++" if kind == "c++" else "C"
    return f"""\
usage: {prog} [ge options] [--] <compiler arguments...>

Compile {language} with a gcc-env toolchain. Arguments after the first
compiler argument (or after --) are passed to the compiler unchanged.

ge options:
  --env NAME          environment to use (default: current)
  --runtime MODE      static, shared, or bundle (default: environment policy)
  --print-command     print the full command without executing it
  -h, --help          show this help

examples:
  {prog} -- -std=c++20 main.cpp -o app
  {prog} --env gcc13 -- -O2 -c main.cpp
"""


def _compiler_command(kind: str, tokens: list[str]) -> int:
    prog = f"ge {kind}"
    env_name: str | None = None
    runtime: str | None = None
    print_command = False
    if any(token in {"-h", "--help"} for token in tokens[:1]):
        print(_compiler_help(kind, prog))
        return 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token in {"--env", "-e"}:
            if index + 1 >= len(tokens):
                raise UsageError("--env needs a value")
            env_name = tokens[index + 1]
            index += 2
            continue
        if token.startswith("--env="):
            env_name = token.split("=", 1)[1]
            index += 1
            continue
        if token == "--runtime":
            if index + 1 >= len(tokens):
                raise UsageError("--runtime needs a value")
            runtime = tokens[index + 1]
            index += 2
            continue
        if token.startswith("--runtime="):
            runtime = token.split("=", 1)[1]
            index += 1
            continue
        if token == "--print-command":
            print_command = True
            index += 1
            continue
        break
    compiler_args = tokens[index:]
    if not compiler_args:
        raise UsageError("no compiler arguments given", hint=f"Try '{prog} --help'.")

    manager = _manager()
    env = _resolve(manager, env_name)
    command = build_command(
        env,
        language="c++" if kind == "c++" else "c",
        args=compiler_args,
        runtime=runtime,
    )
    if print_command:
        print(shlex.join(command))
        return 0
    return run_inherit(command, env=shell_environment(env))


def _run_help() -> str:
    return """\
usage: ge run [NAME] [--] <command> [args...]

Run a command with CC, CXX, PATH, and the runtime library path configured.
Without NAME, the current environment is used.

examples:
  ge run -- gcc --version
  ge run gcc13 -- cmake --version
  ge run --env gcc14 -- python -c "print(1)"
"""


def _run_command_manual(tokens: list[str]) -> int:
    if tokens and tokens[0] in {"-h", "--help"}:
        print(_run_help())
        return 0
    env_name: str | None = None
    if tokens and tokens[0] in {"--env", "-e"}:
        if len(tokens) < 2:
            raise UsageError("--env needs a value")
        env_name = tokens[1]
        tokens = tokens[2:]
    elif tokens and tokens[0].startswith("--env="):
        env_name = tokens[0].split("=", 1)[1]
        tokens = tokens[1:]
    elif tokens and tokens[0] == "--":
        tokens = tokens[1:]
    elif tokens and not tokens[0].startswith("-") and "--" in tokens:
        env_name = tokens[0]
        sep = tokens.index("--")
        tokens = tokens[sep + 1 :]
    elif tokens and tokens[0] == "--":
        tokens = tokens[1:]

    if tokens and tokens[0] == "--":
        tokens = tokens[1:]
    if not tokens:
        raise UsageError("no command given", hint=_run_help().splitlines()[0])

    manager = _manager()
    env = _resolve(manager, env_name)
    env.ensure_shims()
    return run_inherit(tokens, env=shell_environment(env))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    configure_streams()
    if argv and argv[0] in {"cc", "c++", "gcc", "g++"}:
        try:
            return _compiler_command(argv[0], argv[1:])
        except GeError as exc:
            error(exc.message)
            if exc.hint:
                hint(exc.hint)
            return exc.exit_code
        except KeyboardInterrupt:
            return 130
    if argv and argv[0] == "run":
        try:
            return _run_command_manual(argv[1:])
        except GeError as exc:
            error(exc.message)
            if exc.hint:
                hint(exc.hint)
            return exc.exit_code
        except KeyboardInterrupt:
            return 130

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = Config.load()
        config.ensure_layout()
        manager = EnvManager(config)
        dispatch = {
            "setup": cmd_setup,
            "bootstrap": cmd_bootstrap,
            "create": cmd_create,
            "list": cmd_list,
            "ls": cmd_list,
            "info": cmd_info,
            "use": cmd_use,
            "switch": cmd_use,
            "current": cmd_current,
            "remove": cmd_remove,
            "rm": cmd_remove,
            "shell": cmd_shell,
            "activate": cmd_activate,
            "deactivate": cmd_deactivate,
            "shell-init": cmd_shell_init,
            "doctor": cmd_doctor,
            "bundle": cmd_bundle,
            "toolchain": cmd_toolchain,
            "config": cmd_config,
            "version": cmd_version,
        }
        handler = dispatch.get(args.command)
        if handler is None:
            raise UsageError(f"unknown command: {args.command}")
        return handler(args, config, manager)
    except GeError as exc:
        error(exc.message)
        if exc.hint:
            hint(exc.hint)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
