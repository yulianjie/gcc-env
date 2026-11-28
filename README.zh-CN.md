# gcc-env（`ge`）

[![CI](https://github.com/yulianjie/gcc-env/actions/workflows/ci.yml/badge.svg)](https://github.com/yulianjie/gcc-env/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**基于 conda-forge 和 uv 的跨平台 GCC/C++ 工具链管理器。**

`ge` 把每个 GCC 版本安装到 `~/.ge` 下的独立前缀中，为每个工具链创建由 uv
管理的 Python 构建环境（CMake、Ninja 等），切换编译器只需一条命令。同时它
专门处理 Linux 上常见的 `libstdc++.so.6` / `GLIBCXX_3.4.xx not found` 部署问题。

```console
$ ge setup
$ ge create gcc13 --gcc 13.3.0 --use
$ ge c++ -- -std=c++20 main.cpp -o app
$ ge doctor
```

原生支持 **Linux**、**macOS** 和 **Windows（MinGW-w64）**，在 **WSL** 中与普通
Linux 完全一致。

## 为什么需要它？

在同一台机器上安装多个 GCC 通常要处理系统编译器冲突、`libstdc++` ABI 不兼容、
构建脚本缓存 `CC` 等问题。`ge` 将每个工具链完全隔离：

- **真正的 GCC**：编译器来自 conda-forge 的 `gcc_*` / `gxx_*` 包。Linux 默认
  使用 `sysroot_linux-64`（glibc **2.17**），新编译器也能产出兼容旧发行版的
  二进制。
- **每个版本独立前缀**：`~/.ge/envs/<name>` 同时包含 conda 工具链和独立的 uv
  虚拟环境，不污染系统。
- **uv 管理构建工具**：每个环境有自己的 Python venv，由 uv 安装 `cmake`、
  `ninja`（可配置）。
- **可分发产物**：`ge c++` 默认静态链接 C++ 运行时；`ge bundle` 可把
  `libstdc++` 等运行库复制到应用目录，生成可重定位分发包。
- **裸编译器名**：激活后可直接使用 `c`、`cc`、`gcc`、`c++`、`g++`，无需
  `ge` 前缀。
- **可解释的诊断**：`ge doctor` 会编译探针，读取 `GLIBC_*`、`GLIBCXX_*`、
  `CXXABI_*` 符号需求，与目标 glibc 和系统 libstdc++ 比较，告诉你为什么会在
  目标机器上崩溃。

## 安装

推荐使用 uv 安装为独立工具：

```console
uv tool install gcc-env
```

在尚未发布到 PyPI 前，可直接从 GitHub 安装：

```console
uv tool install "git+https://github.com/yulianjie/gcc-env"
```

或从 Release wheel 安装：

```console
uv tool install ./gcc_env-0.1.0-py3-none-any.whl
```

`ge` 会自动把 **micromamba** 和（必要时）**uv** 下载到 `~/.ge/bin`，不需要系统
安装 conda、Mamba 或 GCC。

## 快速开始

```console
# 下载 uv/micromamba，创建默认环境（Linux/Windows 为 GCC 14，
# macOS 为 GCC 15），包含 Python 3.12 + cmake + ninja，并设为当前环境。
ge setup

# 一次性安装 shell 集成：让 ge activate 直接修改当前 shell，
# 而不是仅仅打印激活代码。
ge shell-init --install
# 重启 shell（或 source 配置文件）后：
ge create gcc13 --gcc 13.3.0
ge activate gcc13

# 激活后可直接使用编译器名，不需要 ge 前缀。
c++ --version
c++ -std=c++20 main.cpp -o app

ge deactivate
```

`ge activate` 必须修改**当前 shell**。普通可执行程序无法做到这一点，因此
`ge shell-init --install` 会在 profile（bash/zsh/fish/PowerShell）中安装一个
轻量 wrapper 函数。没有安装 hook 时，使用显式方式：

```console
eval "$(ge activate gcc13)"
```

hook 是幂等的，可用 `ge shell-init --uninstall` 移除。`ge deactivate` 会恢复到
该 shell 第一次激活前的环境，并把编译器 shim 从 `PATH` 移除。

其他常用命令：

```console
ge list
ge use gcc13
ge shell
ge run gcc13 -- cmake --version
ge create gcc15 --gcc 15.2.0
# 仅 Linux：指定更新的 glibc/sysroot 目标
ge create gcc15-glibc228 --gcc 15.2.0 --sysroot 2.28
```

`ge` 还提供 `ge cc`、`ge gcc`、`ge g++`。`ge` 自己的选项必须放在第一个编译器
参数之前：

```console
ge c++ --env gcc13 --runtime shared -- -O2 main.cpp -o app
ge c++ --print-command -- -std=c++20 main.cpp -o app
```

## 常用命令

| 命令 | 说明 |
| --- | --- |
| `ge setup` | 下载 micromamba/uv，创建并选中默认环境。 |
| `ge bootstrap` | 只下载/检查 micromamba 和 uv。 |
| `ge create [NAME]` | 创建环境（`--gcc`、`--sysroot`、`--python`、`--no-python`、`--tools`、`--runtime`、`--use`、`--force`）。 |
| `ge list` / `ge ls` | 列出环境并标记当前环境。 |
| `ge info [NAME]` | 显示编译器路径、版本、sysroot、Python 和 conda 包。 |
| `ge use NAME` / `ge switch` | 切换默认环境。 |
| `ge current` | 打印当前环境名。 |
| `ge remove NAME` | 删除环境（默认需要确认，脚本中加 `--yes`）。 |
| `ge shell [NAME]` | 打开已激活工具链的干净交互 shell。 |
| `ge run [NAME] -- CMD` | 在环境中运行一条命令。 |
| `ge activate [NAME]` | 在当前 shell 中激活（需先执行 `ge shell-init --install`）。 |
| `ge deactivate` | 恢复激活前的 shell 环境。 |
| `ge shell-init` | 打印/安装/卸载 shell hook（`--install`、`--uninstall`）。 |
| `ge cc` / `ge c++` | 按运行时策略调用环境 C/C++ 编译器。 |
| `ge doctor [NAME]` | 检查编译器、glibc 目标、libstdc++、RPATH 和 uv 工具。 |
| `ge bundle BINARY` | 复制二进制和所需 GCC 运行库，生成可重定位目录。 |
| `ge toolchain [NAME]` | 生成 CMake toolchain 文件（`--print` 输出到 stdout）。 |
| `ge config` | 显示生效的默认值、channel 和路径。 |

## 工作原理

```text
~/.ge/
├── bin/                 # 私有 micromamba，有时也有 uv
├── cache/               # 下载缓存和 uv 缓存
├── mamba/               # micromamba root prefix / 包缓存
├── envs/
│   └── gcc13/
│       ├── conda/       # conda-forge GCC、binutils、sysroot、patchelf
│       ├── venv/        # uv 创建的 cmake/ninja 虚拟环境
│       ├── bin/         # c/cc/gcc/c++/g++ 裸命令 shim
│       └── env.json     # 解析后的精确版本和路径
├── state/               # 每个 shell 的激活快照，供 ge deactivate 使用
├── current              # 当前环境名
└── config.toml          # 可选默认配置
```

`ge create` 依次完成：

1. 下载 micromamba（仅一次），对 conda-forge 求解并安装工具链包，例如 Linux：

   ```text
   gcc_linux-64=13.3.0  gxx_linux-64=13.3.0
   sysroot_linux-64=2.17  patchelf
   ```

2. 在 conda 前缀中发现真正的编译器（包括 conda-forge 带 target 前缀的名字）。

3. 执行 `uv venv --seed --python 3.12`，再用 `uv pip install cmake ninja`
   安装构建工具。只需要编译器时可使用 `--no-python`。

激活环境时只重建最小必要变量：把环境的 shim 目录、venv 和工具链 `bin` 目录
前置，导出 `CC`/`CXX`/`AR`/`LD` 等，设置 `CMAKE_PREFIX_PATH`、
`CONDA_BUILD_SYSROOT` 和运行库路径。它**不会**导入 conda-build 默认的
`-march=nocona`/`-O2` 等编译选项，你的项目编译参数仍然由你控制。

shim 目录包含 `c`、`cc`、`gcc`、`c++`、`g++` 包装命令，它们委托给 `ge cc` /
`ge c++`，因此裸编译器名也遵循相同的运行时策略。`ge shell-init --install`
负责让 `ge activate`/`ge deactivate` 能修改父 shell；没有安装 hook 时请使用
`eval "$(ge activate ...)"`。

## `libstdc++.so.6` 问题

用 GCC 13/14/15 编译的程序可能要求 `GLIBCXX_3.4.32` 或更新的符号，而目标机器
的 `/usr/lib/.../libstdc++.so.6` 较旧，典型报错：

```text
./app: /lib/x86_64-linux-gnu/libstdc++.so.6: version `GLIBCXX_3.4.32' not found
./app: /lib/x86_64-linux-gnu/libstdc++.so.6: version `CXXABI_1.3.15' not found
```

全局设置 `LD_LIBRARY_PATH` 或随意复制新 libstdc++ 往往会破坏其他程序。`ge`
提供三种明确策略：

| 策略 | 行为 | 适用场景 |
| --- | --- | --- |
| `static`（默认） | 链接时附加 `-static-libstdc++ -static-libgcc`，产物不再依赖 libstdc++；Windows 还会加 `-static`。 | 独立可执行程序、最大兼容性。 |
| `shared` | 正常动态链接；conda GCC 自带指向环境 `lib` 的 RPATH，在本机可运行。 | 调试、插件、大量共享库项目。 |
| `bundle` | 动态链接并加入相对 RPATH（Linux `$ORIGIN/../lib`，macOS `@loader_path/../lib`），再用 `ge bundle` 复制精确运行库。 | 必须动态链接但需要分发的应用。 |

```console
ge create gcc14 --gcc 14
ge create gcc14-shared --gcc 14 --runtime shared

ge c++ --env gcc14 --runtime static -- main.cpp -o app
ge c++ --env gcc14 --runtime shared -- main.cpp -o app-shared
ge c++ --env gcc14 --runtime bundle -- main.cpp -o app-bundle
```

诊断示例：

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

检查已有二进制：

```console
ge doctor gcc13 --binary ./app
ge doctor gcc13 --binary ./app --target-glibc 2.17 --json
```

`ge doctor` 只在出现 error 时返回非零，可直接用于 CI。它会报告：

- 最大 `GLIBC_*` 需求与 sysroot 目标 glibc 的对比；
- 最大 `GLIBCXX_*` / `CXXABI_*` 需求与系统 libstdc++ 的对比（Linux）；
- 开发机绝对 RPATH；
- Windows MinGW 运行库 DLL 依赖；
- 缺失的 Python/uv 工具。

### 生成可分发目录

```console
ge c++ --env gcc13 --runtime bundle -- app.cpp -o app
ge bundle ./app --env gcc13 --dest ./dist

find ./dist
# dist/bin/app
# dist/lib/libstdc++.so.6
# dist/lib/libgcc_s.so.1
```

Linux 上 `ge bundle` 使用环境内置的 `patchelf`，把绝对开发 RPATH 改成
`$ORIGIN/../lib`；macOS 使用 `install_name_tool` 并在可用时进行 ad-hoc 签名；
Windows 会把所需 MinGW DLL 复制到 `.exe` 旁边。

## CMake

```console
ge toolchain gcc13
# ~/.ge/envs/gcc13/ge-toolchain.cmake

ge run gcc13 -- cmake -S . -B build \
  -DCMAKE_TOOLCHAIN_FILE=$HOME/.ge/envs/gcc13/ge-toolchain.cmake
ge run gcc13 -- cmake --build build
```

环境内的 venv 已提供 `cmake` 和 `ninja`，无需系统安装。

## 配置

可选 `~/.ge/config.toml`：

```toml
[defaults]
gcc = "14"
sysroot = "2.17"     # 仅 Linux；设为 "none" 使用默认 sysroot
python = "3.12"
runtime = "static"   # static | shared | bundle
tools = ["cmake", "ninja"]
channels = ["conda-forge"]
download_timeout = 60
```

常用环境变量：

| 变量 | 含义 |
| --- | --- |
| `GE_ROOT` | 数据目录，默认 `~/.ge`。 |
| `GE_GCC` / `GE_SYSROOT` / `GE_PYTHON` / `GE_RUNTIME` | 覆盖默认值。 |
| `GE_CONDA_CHANNELS` | 逗号分隔的 channel URL，便于使用镜像。 |
| `GE_MAMBA_ROOT` | micromamba root/cache 目录。 |
| `GE_UV` / `GE_MICROMAMBA` | 指定外部二进制。 |
| `GE_UV_URL` / `GE_MICROMAMBA_URL` | 覆盖 bootstrap 下载地址。 |
| `GE_DOWNLOAD_TIMEOUT` | 下载超时秒数。 |
| `NO_COLOR` / `GE_NO_COLOR` | 禁用 ANSI 颜色。 |

`ge` 显式传入 channel 并使用 `--no-rc`，用户 `.condarc` 不会悄悄改变求解。
使用镜像时：

```console
GE_CONDA_CHANNELS="https://mirrors.example.edu/conda-forge,conda-forge" ge create gcc13
```

## 平台说明

### Linux

- 默认 sysroot 为 **glibc 2.17**，兼容性最广；需要更新的 glibc API 时可用
  `--sysroot 2.28` 或 `2.39`。
- 支持 `x86_64` 和 `aarch64`。
- WSL 就是普通 Linux，命令完全一致。

### macOS

- conda-forge 的原生 GCC 从 **15.2** 开始，因此默认 GCC 15；更老的版本在
  macOS 上没有 conda-forge 包。
- 支持 `arm64` 和 `x86_64`。
- 默认静态运行时；动态链接应用建议用 `ge bundle`。`install_name_tool` 和
  `codesign` 需要 Xcode Command Line Tools。

### Windows

- 原生 **MinGW-w64（UCRT）** GCC，来自 `gcc_win-64`/`gxx_win-64`，不是 MSVC。
- 仅支持 `x86_64`；Windows on ARM 请使用 WSL。
- 默认静态运行时生成的 `.exe` 只依赖 Windows UCRT，便于分发。
- `ge shell` 会启动 PowerShell（不可用时用 `cmd`）并注入环境。

## CI/CD

仓库包含：

- `ci.yml`：Linux/macOS/Windows 单元测试、Python 3.11–3.13、lint、构建，以及
  Linux 真实工具链集成测试、macOS/Windows 平台 smoke test。
- `release.yml`：推送 `v*` tag 后构建 sdist/wheel、校验 wheel、创建带 SHA-256
  校验和的 **GitHub Release**；配置 `PYPI_PUBLISH` 仓库变量和
  `PYPI_API_TOKEN` secret 后可发布到 PyPI。

发布：

```console
git tag v0.1.0
git push origin v0.1.0
```

## 开发

```console
git clone https://github.com/yulianjie/gcc-env
cd gcc-env
uv sync --dev
uv run pytest -m "not integration"
uv run ruff check src tests
uv run pytest -m integration   # 需要网络，下载数百 MB
```

集成测试会在 `~/.ge` 下创建 `ge-it-*` 环境并在结束后删除；可用
`GE_INTEGRATION_ROOT` 指定其他根目录。

## 限制

- 每个环境拥有完整工具链前缀，这是刻意隔离，不是轻量级多路复用；包缓存共享。
- Python venv 可选（`--no-python`），但 `ge doctor` 的工具检查和 CMake/Ninja
  便利依赖它。
- 静态 libstdc++ 非常适合可执行程序，但共享库/插件边界可能更适合
  `shared`/`bundle`。
- Linux 上 glibc 仍是系统依赖。2.17 sysroot 提供较广兼容性，但无法让程序运行
  在比该 glibc 更老的系统上。
- macOS conda-forge GCC 版本较新；Apple 框架相关开发仍建议使用 Apple Clang。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
