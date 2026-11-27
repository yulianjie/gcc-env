"""Exception types used by gcc-env."""

from __future__ import annotations


class GeError(Exception):
    """Base class for expected, user-facing errors."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(GeError):
    """Invalid command-line usage or configuration."""

    exit_code = 2


class PlatformError(GeError):
    """The current platform or architecture is unsupported."""


class EnvError(GeError):
    """An environment is missing, broken, or already exists."""


class DownloadError(GeError):
    """A bootstrap download failed."""


class CommandError(GeError):
    """An external command failed."""

    def __init__(
        self,
        command: list[str] | str,
        returncode: int,
        output: str | None = None,
        *,
        hint: str | None = None,
    ) -> None:
        printable = " ".join(command) if isinstance(command, list) else command
        message = f"command failed (exit code {returncode}): {printable}"
        if output:
            tail = output.strip().splitlines()[-20:]
            if tail:
                message += "\n" + "\n".join(tail)
        super().__init__(message, hint=hint)
        self.command = command
        self.returncode = returncode
        self.output = output
