"""The object every command receives: working copy discovery, a configured
svn client, lazily-loaded state, and path helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional

from . import layout as layout_mod
from .errors import NotAWorkingCopy
from .state import State
from .svnclient import SvnClient, SvnInfo


def find_wc_root(start: Path) -> Optional[Path]:
    """Walk up looking for `.svn`.

    Subversion 1.7+ keeps a single `.svn` at the working copy root, so the
    first hit going upwards is the root.
    """
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".svn").is_dir():
            return candidate
    return None


class Context:
    def __init__(
        self,
        cwd: Optional[Path] = None,
        dry_run: bool = False,
        trace: bool = False,
        stdout=None,
        stderr=None,
        svn_binary: Optional[str] = None,
    ):
        self.cwd = Path(cwd or Path.cwd())
        self.dry_run = dry_run
        self.stdout = stdout if stdout is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr
        self.svn = SvnClient(
            cwd=str(self.cwd),
            binary=svn_binary,
            dry_run=dry_run,
            trace=trace,
            stderr=self.stderr,
        )
        self._wc_root: Optional[Path] = None
        self._wc_searched = False
        self._state: Optional[State] = None
        self._info: Optional[SvnInfo] = None
        self._layout: Optional[layout_mod.Layout] = None

    # ------------------------------------------------------------------
    # output
    # ------------------------------------------------------------------
    def echo(self, message: str = "") -> None:
        print(message, file=self.stdout)

    def warn(self, message: str) -> None:
        print(message, file=self.stderr)

    def note(self, message: str) -> None:
        """Explain a git->svn translation the user might not expect."""
        if self.state_quiet:
            return
        print("svngit: %s" % message, file=self.stderr)

    @property
    def state_quiet(self) -> bool:
        if os.environ.get("SVNGIT_QUIET") == "1":
            return True
        if not self.in_working_copy():
            return False
        return self.state.get_config("svngit.quiet") == "true"

    # ------------------------------------------------------------------
    # working copy
    # ------------------------------------------------------------------
    @property
    def wc_root(self) -> Path:
        root = self.find_wc()
        if root is None:
            raise NotAWorkingCopy(self.cwd)
        return root

    def find_wc(self) -> Optional[Path]:
        if not self._wc_searched:
            self._wc_root = find_wc_root(self.cwd)
            self._wc_searched = True
        return self._wc_root

    def in_working_copy(self) -> bool:
        return self.find_wc() is not None

    @property
    def state(self) -> State:
        if self._state is None:
            self._state = State(self.wc_root)
        return self._state

    @property
    def info(self) -> SvnInfo:
        if self._info is None:
            self._info = self.svn.info(str(self.wc_root))
        return self._info

    @property
    def layout(self) -> layout_mod.Layout:
        if self._layout is None:
            self._layout = layout_mod.detect(self.svn, self.info, self.state.config)
            # Cache the probe result so later commands skip the network round trip.
            if "svngit.layout" not in self.state.config:
                self.state.set_config(
                    "svngit.layout", "standard" if self._layout.standard else "flat"
                )
                self.state.save()
        return self._layout

    @property
    def branch(self) -> str:
        return layout_mod.current_branch(self.layout, self.info)

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------
    def to_wc_path(self, user_path: str) -> str:
        """Turn a user-supplied path into a working-copy-root-relative POSIX path.

        Accepts git's `:/foo` "relative to repo root" syntax.
        """
        if user_path.startswith(":/"):
            candidate = (self.wc_root / user_path[2:]).resolve()
        else:
            candidate = (self.cwd / user_path).resolve()
        root = self.wc_root.resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            raise SystemExit(
                "fatal: %s is outside the working copy at %s" % (user_path, root)
            )
        return relative.as_posix() if str(relative) != "." else ""

    def to_wc_paths(self, user_paths: Iterable[str]) -> List[str]:
        return [self.to_wc_path(p) for p in user_paths]

    def abs_path(self, wc_path: str) -> Path:
        return self.wc_root / wc_path if wc_path else self.wc_root

    def display_path(self, wc_path: str) -> str:
        """Render a working-copy path the way git would: relative to cwd."""
        absolute = self.abs_path(wc_path)
        try:
            return os.path.relpath(absolute, self.cwd)
        except ValueError:
            return wc_path

    def svn_target(self, wc_path: str) -> str:
        """A path to hand to the svn binary (which runs with cwd=self.cwd)."""
        return str(self.abs_path(wc_path))

    def snapshot(self, wc_path: str) -> Optional[str]:
        """Copy a path's current content into the object store, returning its
        hash. Directories and missing files snapshot to None.

        Staging writes content here rather than only hashing it, so that a
        file edited after `git add` still commits the version that was staged.
        """
        absolute = self.abs_path(wc_path)
        if not absolute.is_file():
            return None
        return self.state.objects.write_file(absolute)
