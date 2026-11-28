# gcc-env (`ge`)

[![CI](https://github.com/yulianjie/gcc-env/actions/workflows/ci.yml/badge.svg)](https://github.com/yulianjie/gcc-env/actions/workflows/ci.yml)
[![Release](https://github.com/yulianjie/gcc-env/actions/workflows/release.yml/badge.svg)](https://github.com/yulianjie/gcc-env/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A cross-platform GCC/C++ toolchain manager built on conda-forge and uv.**

`ge` installs complete GCC toolchains into isolated prefixes under `~/.ge`,
gives each one a uv-managed Python build environment (CMake, Ninja, ...), and
makes switching compilers a one-liner. It also takes care of the infamous
`libstdc++.so.6` / `GLIBCXX_3.4.xx not found` deployment problem.

```console
$ ge setup
$ ge create gcc13 --gcc 13.3.0 --use
$ ge c++ -- -std=c++20 main.cpp -o app
$ ge doctor
```

Works natively on **Linux**, **macOS**, and **Windows** (MinGW-w64), and inside
**WSL** like any other Linux distribution.

---

## Why another toolchain manager?

Installing several GCC versions on one machine usually means fighting with the
system compiler, ABI-incompatible `libstdc++`, and build scripts that cache
`CC`. `ge` keeps everything self-contained:

- **Real GCC, not a shim.** Compilers come from conda-forge's `gcc_*`/`gxx_*`
  packages. Linux uses the `sysroot_linux-64` package (default glibc **2.17**),
  so new compilers can still produce binaries for old distributions.
- **One prefix per version.** `~/.ge/envs/<name>` contains the conda toolchain
  and a separate uv virtual environment. Nothing is installed system-wide.
- **uv for build tooling.** Each environment gets its own Python venv with
  `cmake` and `ninja` (configurable), installed by uv and isolated per GCC.
- **Portable output.** `ge c++` links the C++ runtime statically by default and
  `ge bundle` can produce a relocatable directory with `libstdc++` next to your
  application instead of depending on the target machine.
- **Bare compiler names.** After activation, `c`, `cc`, `gcc`, `c++`, and `g++`
  are available directly (no `ge` prefix) through per-environment shims.
- **Explainable diagnosis.** `ge doctor` compiles a probe, reads `GLIBC_*`,
  `GLIBCXX_*`, and `CXXABI_*` symbol requirements, compares them with the target
  glibc and the system libstdc++, and tells you exactly what will break.

## Requirements

- Python **3.11+** (to run `ge` itself)
- Network access to conda-forge and GitHub for the first bootstrap
- ~1 GB disk space per toolchain (shared package caches reduce this)

`ge` downloads its own **micromamba** and (if missing) **uv** into `~/.ge/bin`.
You do not need conda, Mamba, or a system GCC installed.

## Installation

Install as a standalone uv tool (recommended):

```console
uv tool install gcc-env
```

Until the package is on PyPI, install directly from GitHub:

```console
uv tool install "git+https://github.com/yulianjie/gcc-env"
```

Or from a release wheel:

```console
uv tool install ./gcc_env-0.1.0-py3-none-any.whl
```

Verify:

```console
ge --version
ge bootstrap
```

## Quick start

```console
# Download uv/micromamba, create "default" (GCC 14 on Linux/Windows,
# GCC 15 on macOS) with Python 3.12 + cmake + ninja, and select it.
ge setup

# One-time shell integration: makes `ge activate` modify the current shell
# directly instead of merely printing shell code.
ge shell-init --install
# Restart the shell (or source your profile), then:
ge create gcc13 --gcc 13.3.0
ge activate gcc13

# Compiler names are now available without the `ge` prefix.
c++ --version
c++ -std=c++20 main.cpp -o app

ge deactivate
```

`ge activate` must be able to modify the **current** shell. A normal executable
cannot do that, so `ge shell-init --install` adds a small wrapper function to
your profile (`bash`/`zsh`/`fish`/PowerShell). Without the hook, use the
explicit fallback:

```console
eval "$(ge activate gcc13)"
```

The hook is idempotent and can be removed with `ge shell-init --uninstall`.
`ge deactivate` restores the environment from before the first activation in
that shell (it also removes the per-environment compiler shims from `PATH`).

PowerShell users can install/print the hook directly:

```powershell
ge shell-init --install
Invoke-Expression ((ge shell-init --shell powershell) -join [Environment]::NewLine)
```

More commands:

```console
ge list
ge use gcc13
ge shell
ge run gcc13 -- cmake --version
ge create gcc15 --gcc 15.2.0
# Linux only: choose a newer glibc/sysroot target.
ge create gcc15-glibc228 --gcc 15.2.0 --sysroot 2.28
```

`ge` also ships `ge cc`, `ge gcc` and `ge g++` aliases. Advanced compiler flags
for `ge` itself must come before the first compiler argument:

```console
ge c++ --env gcc13 --runtime shared -- -O2 main.cpp -o app
ge c++ --print-command -- -std=c++20 main.cpp -o app
```

## Commands

| Command | Description |
| --- | --- |
| `ge setup` | Bootstrap micromamba/uv, create and select the default environment. |
| `ge bootstrap` | Download/verify micromamba and uv only. |
| `ge create [NAME]` | Create an environment (`--gcc`, `--sysroot`, `--python`, `--no-python`, `--tools`, `--runtime`, `--use`, `--force`). |
| `ge list` / `ge ls` | List environments and show the current one. |
| `ge info [NAME]` | Show compiler paths, versions, sysroot, Python, and conda packages. |
| `ge use NAME` / `ge switch` | Select the default environment for new commands. |
| `ge current` | Print the current environment name. |
| `ge remove NAME` | Delete an environment (asks for confirmation; `--yes` for scripts). |
| `ge shell [NAME]` | Spawn a clean interactive shell with the toolchain activated. |
| `ge run [NAME] -- CMD` | Run one command with `CC`, `CXX`, `PATH`, and runtime paths set. |
| `ge activate [NAME]` | Activate in the current shell (requires `ge shell-init --install`). |
| `ge deactivate` | Restore the shell environment from before activation. |
| `ge shell-init` | Print the shell hook; `--install`/`--uninstall` manage the profile block. |
| `ge cc` / `ge c++` | Invoke the environment C/C++ compiler with the runtime policy applied. |
| `ge doctor [NAME]` | Check compiler, glibc target, libstdc++, RPATH, and uv tools. |
| `ge bundle BINARY` | Copy a binary plus the required GCC runtime into a relocatable folder. |
| `ge toolchain [NAME]` | Write a CMake toolchain file (`--print` for stdout). |
| `ge config` | Show effective defaults, channels, and paths. |

Use `ge COMMAND --help` for details.

## How it works

```text
~/.ge/
├── bin/                 # private micromamba, sometimes uv
├── cache/               # downloads and uv cache
├── python/              # uv-managed Python interpreters
├── mamba/               # micromamba root prefix and package cache
├── envs/
│   └── gcc13/
│       ├── conda/       # conda-forge GCC, binutils, sysroot, patchelf
│       ├── venv/        # uv venv with cmake/ninja (per toolchain)
│       ├── bin/         # c/cc/gcc/c++/g++ shims for bare compiler names
│       └── env.json     # exact resolved versions and paths
├── state/               # per-shell activation snapshots for ge deactivate
├── current              # name of the selected environment
└── config.toml          # optional defaults
```

`ge create` does three things:

1. Downloads micromamba (once) and runs a conda solve against **conda-forge**
   with an explicit toolchain package, for example on Linux:

   ```text
   gcc_linux-64=13.3.0  gxx_linux-64=13.3.0
   sysroot_linux-64=2.17  patchelf
   ```

2. Discovers the actual compiler executables from the prefix (including the
   target-prefixed names used by conda-forge).

3. Runs `uv venv --seed --python 3.12` in `venv/` and
   `uv pip install cmake ninja` for build tooling. Use `--no-python` if you only
   need the compiler.

Activation reconstructs a minimal, clean environment: it prepends the
environment's shim directory, venv, and toolchain `bin` directories, exports
`CC`/`CXX`/`AR`/`LD`/..., sets `CMAKE_PREFIX_PATH` and
`CONDA_BUILD_SYSROOT`, and prepends the toolchain `lib` directory to the runtime
loader path. It deliberately does **not** import conda-build's default
`-march=nocona`/`-O2` flags, so your project's flags remain yours.

The shim directory contains `c`, `cc`, `gcc`, `c++`, and `g++` wrappers that
delegate to `ge cc` / `ge c++`, so the bare compiler names respect the same
runtime policy. `ge shell-init --install` is what lets `ge activate`/`ge deactivate`
change the parent shell; without it, use `eval "$(ge activate ...)"`.

### Why micromamba *and* uv?

They solve different problems. conda-forge supplies the native GCC toolchain,
binutils, sysroot, and runtime libraries; uv supplies isolated Python tooling
and Python interpreters. Using uv alone cannot install native conda packages,
and using conda alone would duplicate a Python environment per compiler without
uv's speed and lockfile ecosystem. `ge` composes them.

## The `libstdc++.so.6` problem

A binary linked with GCC 13/14/15 may require `GLIBCXX_3.4.32` (or newer), while
the target machine's `/usr/lib/.../libstdc++.so.6` only provides an older
version. The usual results are:

```text
./app: /lib/x86_64-linux-gnu/libstdc++.so.6: version `GLIBCXX_3.4.32' not found
./app: /lib/x86_64-linux-gnu/libstdc++.so.6: version `CXXABI_1.3.15' not found
```

Setting `LD_LIBRARY_PATH` globally or shipping a newer libstdc++ "somewhere"
often breaks other applications. `ge` gives you three explicit policies:

| Policy | What happens | Best for |
| --- | --- | --- |
| `static` (default) | Adds `-static-libstdc++ -static-libgcc` when linking. The resulting binary has no libstdc++ dependency. On Windows it also uses `-static`. | Shipping standalone tools; maximum compatibility. |
| `shared` | Normal dynamic linking. conda's GCC adds an RPATH to the environment's `lib`, so it runs locally. | Debugging, plugins, shared-library-heavy projects. |
| `bundle` | Dynamic linking plus a relative RPATH (`$ORIGIN/../lib` on Linux, `@loader_path/../lib` on macOS). Then `ge bundle` copies the exact runtime libraries. | Applications that must ship their C++ runtime but stay dynamically linked. |

Choose per command or when creating the environment:

```console
ge create gcc14 --gcc 14
ge create gcc14-shared --gcc 14 --runtime shared

ge c++ --env gcc14 --runtime static -- main.cpp -o app
ge c++ --env gcc14 --runtime shared -- main.cpp -o app-shared
ge c++ --env gcc14 --runtime bundle -- main.cpp -o app-bundle
```

`ge doctor` is the source of truth:

```console
$ ge doctor gcc13
   ✓  environment gcc13 (Linux x86_64)
   ✓  compiler: ~/.ge/envs/gcc13/conda/bin/x86_64-conda-linux-gnu-cc
   ✓  compiled a C++20 filesystem/format probe
   ✓  maximum GLIBC symbol: GLIBC_2.17
   ✓  GLIBC requirements fit the target (2.17)
   ✓  no libstdc++.so.6 dependency (static runtime); portable across ...
   ✓  Python venv: .../venv/bin/python (3.12.3)
   ✓  uv-installed tool: cmake
   ✓  uv-installed tool: ninja
ok doctor passed
```

Inspect an existing binary instead of compiling a probe:

```console
ge doctor gcc13 --binary ./app
ge doctor gcc13 --binary ./app --target-glibc 2.17 --json
```

`ge doctor` exits non-zero only on errors, making it usable in CI. It reports:

- the maximum `GLIBC_*` requirement versus the sysroot target;
- the maximum `GLIBCXX_*` / `CXXABI_*` requirement versus the system
  libstdc++ (on Linux);
- development RPATHs;
- MinGW runtime DLL dependencies on Windows;
- missing Python/uv tooling.

### Creating a portable bundle

```console
ge c++ --env gcc13 --runtime bundle -- app.cpp -o app
ge bundle ./app --env gcc13 --dest ./dist

find ./dist
# dist/bin/app
# dist/lib/libstdc++.so.6
# dist/lib/libgcc_s.so.1
```

On Linux, `ge bundle` uses `patchelf` (installed in the environment) to replace
the absolute development RPATH with `$ORIGIN/../lib`. On macOS it uses
`install_name_tool` and ad-hoc codesigning when available. On Windows it copies
the required MinGW DLLs next to the `.exe`.

## CMake

`ge toolchain` writes a CMake toolchain file with the compiler paths, static
runtime flags, prefix paths, and a relative install RPATH for bundle builds:

```console
ge toolchain gcc13
# ~/.ge/envs/gcc13/ge-toolchain.cmake

ge run gcc13 -- cmake -S . -B build \
  -DCMAKE_TOOLCHAIN_FILE=$HOME/.ge/envs/gcc13/ge-toolchain.cmake
ge run gcc13 -- cmake --build build
```

The environment's venv provides `cmake` and `ninja`, so no system CMake is
required.

## Configuration

Optional `~/.ge/config.toml`:

```toml
[defaults]
gcc = "14"
sysroot = "2.17"     # Linux only; "none" to use the default sysroot
python = "3.12"
runtime = "static"   # static | shared | bundle
tools = ["cmake", "ninja"]
channels = ["conda-forge"]
download_timeout = 60
```

Common environment variables:

| Variable | Meaning |
| --- | --- |
| `GE_ROOT` | Data directory (default `~/.ge`). |
| `GE_GCC`, `GE_SYSROOT`, `GE_PYTHON`, `GE_RUNTIME` | Override defaults. |
| `GE_CONDA_CHANNELS` | Comma-separated channel URLs; useful for mirrors. |
| `GE_MAMBA_ROOT` | micromamba root/cache directory. |
| `GE_UV`, `GE_MICROMAMBA` | Use specific external binaries. |
| `GE_UV_URL`, `GE_MICROMAMBA_URL` | Override bootstrap download URLs. |
| `GE_DOWNLOAD_TIMEOUT` | Bootstrap/network timeout in seconds. |
| `NO_COLOR` / `GE_NO_COLOR` | Disable ANSI colors. |

### Using a conda mirror

`ge` passes channels explicitly and uses `--no-rc`, so a user `.condarc` cannot
silently change the solve. Point it at a mirror explicitly:

```console
GE_CONDA_CHANNELS="https://mirrors.example.edu/conda-forge,conda-forge" ge create gcc13
```

## Platform notes

### Linux

- Default sysroot: **glibc 2.17**, the conda-forge default, for broad
  compatibility. Use `--sysroot 2.28` or `2.39` when you need newer glibc APIs.
- Native `x86_64` and `aarch64` are supported.
- WSL is a normal Linux platform; the same commands work unchanged.

### macOS

- conda-forge's native GCC packages start at **15.2**, so the default is GCC 15.
  Use `--gcc 15.2.0` or newer explicitly; older GCC versions are not available
  for macOS from conda-forge.
- `arm64` and `x86_64` are supported.
- The static runtime policy is the default. Use `ge bundle` for dynamically
  linked applications; `install_name_tool` and `codesign` require Xcode command
  line tools.

### Windows

- Native **MinGW-w64 (UCRT)** GCC is installed from `gcc_win-64`/`gxx_win-64`.
  This is not MSVC: it produces native Windows executables with the MinGW ABI.
- `x86_64` only. Use WSL for Linux toolchains on Windows on ARM.
- The default static runtime produces an `.exe` whose only imports are Windows
  UCRT functions, making it easy to distribute.
- `ge shell` starts PowerShell (or `cmd` if PowerShell is unavailable) with the
  environment applied. `ge activate --shell powershell` prints code for
  `Invoke-Expression`.

## CI/CD

The repository contains:

- `ci.yml`: unit tests on Linux/macOS/Windows and Python 3.11–3.13, lint, build,
  plus real toolchain integration tests on Linux and platform smoke tests on
  macOS/Windows.
- `release.yml`: on a `v*` tag, builds an sdist and wheel, verifies the wheel,
  creates a **GitHub Release** with the artifacts and SHA-256 checksums, and can
  optionally publish to PyPI when the `PYPI_PUBLISH` repository variable and
  `PYPI_API_TOKEN` secret are configured.

Create a release:

```console
git tag v0.1.0
git push origin v0.1.0
```

## Development

```console
git clone https://github.com/yulianjie/gcc-env
cd gcc-env
uv sync --dev
uv run pytest -m "not integration"
uv run ruff check src tests
uv run pytest -m integration   # network + several hundred MB
```

The integration tests create real environments (`ge-it-*`) under `~/.ge` and
remove them afterward. Set `GE_INTEGRATION_ROOT` to use another root.

Project layout:

```text
src/gcc_env/
├── cli.py          # argparse CLI and manual parsers for cc/c++/run
├── envs.py         # environment metadata, create/remove/use
├── compiler.py     # conda specs, compiler discovery, runtime flags
├── conda.py        # micromamba wrapper
├── uvtools.py      # uv venv + pip integration
├── activation.py   # environment variables and shell snippets
├── runtime.py      # ELF/PE/Mach-O inspection, ge doctor
├── bundle.py       # relocatable runtime bundling
├── cmake.py        # CMake toolchain generation
└── bootstrap.py    # micromamba/uv download and verification
```

## Limitations

- Each environment owns a full toolchain prefix; this is intentional isolation,
  not a lightweight multiplexer. Package caches are shared.
- The Python venv is optional (`--no-python`), but `ge doctor` tool checks and
  `ge toolchain` CMake/Ninja convenience depend on it.
- A statically linked libstdc++ is excellent for executables, but shared
  libraries and plugin boundaries may require `shared`/`bundle` instead.
- glibc itself is still a system dependency on Linux. The 2.17 sysroot gives
  broad compatibility, but it cannot make a binary work on a machine older than
  that glibc.
- macOS GCC from conda-forge is newer than the usual Homebrew GCC versions;
  Apple's Clang remains the platform default for Apple-specific frameworks.

## License

MIT. See [LICENSE](LICENSE).
