"""Exception types. Every one of these is caught by the CLI and turned into a
one-line message plus an exit code, so tracebacks never reach the user."""

from __future__ import annotations

from typing import Sequence


class SvnGitError(Exception):
    """Base for everything svngit raises deliberately."""

    exit_code = 1


class UsageError(SvnGitError):
    """Bad arguments. Mirrors git's exit code for usage problems."""

    exit_code = 129


class NotAWorkingCopy(SvnGitError):
    exit_code = 128

    def __init__(self, path: str) -> None:
        super().__init__(
            "not a subversion working copy (or any parent up to mount point): %s" % path
        )


class Unsupported(SvnGitError):
    """A git concept with no reasonable Subversion equivalent.

    The message should always say what to do instead -- an unsupported command
    that just says "unsupported" is a dead end for the user.
    """

    exit_code = 128


class SvnCommandError(SvnGitError):
    exit_code = 128

    def __init__(self, argv: Sequence[str], returncode: int, stderr: str):
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr = (stderr or "").strip()
        detail = self.stderr or "no output on stderr"
        super().__init__(
            "svn command failed (exit %d): %s\n%s"
            % (returncode, " ".join(self.argv), detail)
        )
