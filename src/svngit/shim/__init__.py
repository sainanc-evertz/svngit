"""The `git` shim, shipped inside the package.

Keeping it here rather than in a top-level `bin/` means one line works for
every install route -- pip, pipx, Homebrew, a zipapp or a git checkout:

    export PATH="$(svngit --shim-path):$PATH"
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

#: What the shim is called on each platform. Both are installed either way,
#: so a shared checkout works from Windows and from a Unix shell.
POSIX_SHIM = "git"
WINDOWS_SHIM = "git.cmd"


def shim_directory() -> Path:
    """A real directory on disk holding the shim, ready to put on PATH.

    Two cases need handling beyond "return where the file is":

    * Running from a zipapp, the shim is inside the archive and PATH cannot
      reach it, so it is unpacked into the user's cache directory.
    * Wheels do not carry the executable bit, which would leave the shim on
      PATH but unrunnable, with a confusing error. It is restored here.
    """
    directory = Path(__file__).resolve().parent
    if not (directory / POSIX_SHIM).is_file():
        directory = _unpack()
    _make_executable(directory / POSIX_SHIM)
    return directory


def _make_executable(target: Path) -> None:
    if os.name == "nt" or not target.is_file():
        return
    mode = target.stat().st_mode
    wanted = mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    if mode != wanted:
        try:
            target.chmod(wanted)
        except OSError:
            pass  # read-only install; the caller will see the real error


def _cache_directory() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "svngit" / "shim"


def _unpack() -> Path:
    """Write the shim out of the archive so PATH can reach it."""
    from importlib import resources

    target = _cache_directory()
    target.mkdir(parents=True, exist_ok=True)
    for name in (POSIX_SHIM, WINDOWS_SHIM):
        try:
            data = resources.files(__package__).joinpath(name).read_bytes()
        except (FileNotFoundError, OSError, AttributeError):
            continue
        destination = target / name
        # Rewrite only when it differs, so an upgrade refreshes the shim but
        # a normal run does not touch the file system.
        if not destination.exists() or destination.read_bytes() != data:
            destination.write_bytes(data)
    return target
