from __future__ import annotations

from pathlib import Path

from gcc_env.config import Config
from gcc_env.runtime import BinaryInfo, _parse_dynamic, library_versions

from .conftest import make_environment


def test_parse_dynamic() -> None:
    text = """
 0x0000000000000001 (NEEDED)             Shared library: [libstdc++.so.6]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x000000000000000f (RPATH)              Library rpath: [/opt/conda/lib]
 0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN/../lib:/tmp/x]
"""
    needed, rpath, runpath, dynamic = _parse_dynamic(text)
    assert needed == ["libstdc++.so.6", "libc.so.6"]
    assert rpath == ["/opt/conda/lib"]
    assert runpath == ["$ORIGIN/../lib", "/tmp/x"]
    assert dynamic


def test_binary_info_versions() -> None:
    info = BinaryInfo(
        path=Path("x"),
        format="elf",
        dynamic=True,
        needed=["libstdc++.so.6"],
        glibc=["GLIBC_2.17", "GLIBC_2.4"],
        glibcxx=["GLIBCXX_3.4.32", "GLIBCXX_3.4.9"],
        cxxabi=["CXXABI_1.3.15"],
    )
    assert info.max_glibc == "GLIBC_2.17"
    assert info.max_glibcxx == "GLIBCXX_3.4.32"
    assert info.max_cxxabi == "CXXABI_1.3.15"
    assert info.needs_libstdcxx


def test_library_versions(tmp_path: Path) -> None:
    lib = tmp_path / "libstdc++.so.6"
    lib.write_bytes(b"\x7fELF....GLIBCXX_3.4.1...GLIBCXX_3.4.32...CXXABI_1.3.15...hello")
    glibcxx, cxxabi = library_versions(lib)
    assert "GLIBCXX_3.4.32" in glibcxx
    assert "CXXABI_1.3.15" in cxxabi


def test_cmake_toolchain(config: Config) -> None:
    from gcc_env.cmake import render_toolchain

    env = make_environment(config)
    text = render_toolchain(env)
    assert f'set(CMAKE_CXX_COMPILER "{env.cxx}")'.replace("\\", "/") in text
    assert 'set(CMAKE_CXX_FLAGS_INIT "")' in text
    assert "static-libstdc++" in text
    assert f'list(PREPEND CMAKE_PREFIX_PATH "{env.conda_prefix}")'.replace("\\", "/") in text
