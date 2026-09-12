"""Compute a git-shaped view of the working copy.

Subversion reports a single state per path. Git reports two: one for the index
and one for the worktree. We synthesise the missing dimension by combining
`svn status` with our own staging area, and -- for staged files -- by
comparing the file's current content against the hash recorded when it was
staged. That is what makes `AM` and `MM` show up correctly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from .state import ADD, DELETE, MODIFY, IndexEntry
from .svnclient import StatusEntry

if TYPE_CHECKING:  # pragma: no cover
    from .context import Context

UNTRACKED = "?"
IGNORED = "!"
UNMERGED = "U"
CLEAN = " "


@dataclass
class FileStatus:
    path: str  # working-copy-relative, POSIX separators
    index: str  # git's first status column
    worktree: str  # git's second status column

    @property
    def code(self) -> str:
        return self.index + self.worktree

    @property
    def untracked(self) -> bool:
        return self.code == "??"

    @property
    def ignored(self) -> bool:
        return self.code == "!!"

    @property
    def unmerged(self) -> bool:
        return "U" in self.code

    @property
    def staged(self) -> bool:
        return self.index not in (CLEAN, UNTRACKED, IGNORED)

    @property
    def unstaged(self) -> bool:
        return self.worktree not in (CLEAN, UNTRACKED, IGNORED)


@dataclass
class StatusReport:
    entries: List[FileStatus] = field(default_factory=list)
    branch: str = ""
    revision: int = 0

    @property
    def staged(self) -> List[FileStatus]:
        return [e for e in self.entries if e.staged]

    @property
    def unstaged(self) -> List[FileStatus]:
        return [e for e in self.entries if e.unstaged and not e.untracked]

    @property
    def untracked(self) -> List[FileStatus]:
        return [e for e in self.entries if e.untracked]

    @property
    def unmerged(self) -> List[FileStatus]:
        return [e for e in self.entries if e.unmerged]

    @property
    def clean(self) -> bool:
        return not self.entries


def hash_file(path: Path) -> Optional[str]:
    """sha256 of a file's bytes, or None if it is not a readable regular file."""
    try:
        if not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _worktree_matches_index(abs_path: Path, staged_blob: Optional[str]) -> bool:
    if staged_blob is None:
        return True
    return hash_file(abs_path) == staged_blob


def compute(
    ctx: "Context", paths: Optional[List[str]] = None, include_ignored: bool = False
) -> StatusReport:
    """Build the two-column status for the working copy."""
    targets = [ctx.svn_target(p) for p in paths] if paths else [str(ctx.wc_root)]
    svn_entries = ctx.svn.status(targets, no_ignore=include_ignored)
    index = ctx.state.index
    queued = ctx.state.queued_blobs()
    root = ctx.wc_root.resolve()

    by_path: Dict[str, FileStatus] = {}

    def rel(entry_path: str) -> Optional[str]:
        # svn echoes paths in the form they were given. We always pass
        # absolute targets, but resolve against the client's cwd rather than
        # the process cwd so a relative path can never bind to the wrong file.
        candidate = Path(entry_path)
        if not candidate.is_absolute():
            candidate = Path(ctx.svn.cwd) / candidate
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except ValueError:
            return None

    for entry in svn_entries:
        wc_path = rel(entry.path)
        if wc_path is None or wc_path == ".":
            continue
        status = _translate_entry(ctx, entry, index.get(wc_path), wc_path, queued)
        if status is not None:
            by_path[wc_path] = status

    # A path can be staged as a modification while svn calls it unmodified --
    # for instance when the user staged it and then reverted the edit by hand.
    # Keep it visible so `git reset` has something to act on.
    for wc_path, staged in index.items():
        if wc_path in by_path:
            continue
        if paths and not any(
            wc_path == p or wc_path.startswith(p.rstrip("/") + "/") for p in paths
        ):
            continue
        abs_path = ctx.abs_path(wc_path)
        worktree = CLEAN if _worktree_matches_index(abs_path, staged.blob) else "M"
        by_path[wc_path] = FileStatus(wc_path, staged.action, worktree)

    report = StatusReport(entries=[by_path[k] for k in sorted(by_path)])
    return report


class _NotLocal:
    """Sentinel: this path is not explained by a queued local commit.

    A class rather than `object()` so the function returning it can say so in
    its signature instead of widening the return type to `object`.
    """


_NOT_LOCAL = _NotLocal()


def _against_local_commit(
    wc_path: str, abs_path: Path, committed: Optional[str], item: str
) -> Union[FileStatus, None, _NotLocal]:
    """Compare a path against the local commit that last touched it.

    Subversion still calls the path modified, because nothing has been pushed.
    Git would call it committed. Anything *beyond* the local commit is a fresh
    unstaged modification.
    """
    if committed is None:
        # The local commit deleted it; gone from disk means nothing further.
        return None if not abs_path.exists() else FileStatus(wc_path, CLEAN, "M")
    if hash_file(abs_path) == committed:
        return None
    if item in ("added", "modified", "replaced", "normal", "incomplete"):
        return FileStatus(wc_path, CLEAN, "M")
    return _NOT_LOCAL


def _translate_entry(
    ctx: "Context",
    entry: StatusEntry,
    staged: Optional[IndexEntry],
    wc_path: str,
    queued: Optional[Dict[str, Optional[str]]] = None,
) -> Optional[FileStatus]:
    item = entry.item
    props_modified = entry.props in ("modified", "conflicted")
    abs_path = ctx.abs_path(wc_path)

    if staged is None and queued and wc_path in queued:
        resolved = _against_local_commit(wc_path, abs_path, queued[wc_path], item)
        if not isinstance(resolved, _NotLocal):
            return resolved

    if item == "unversioned":
        return FileStatus(wc_path, UNTRACKED, UNTRACKED)
    if item == "ignored":
        return FileStatus(wc_path, IGNORED, IGNORED)
    if item in ("external", "none") and not props_modified:
        return None
    if item in ("conflicted", "obstructed"):
        # git spells an unresolved conflict "UU"; svn has no index side, so we
        # report the same letter twice.
        return FileStatus(wc_path, UNMERGED, UNMERGED)

    if item == "added":
        # `svn add` schedules the file, which *is* the staged state; there is
        # no way to have an added-but-unstaged file in Subversion.
        worktree = CLEAN
        if staged is not None and not _worktree_matches_index(abs_path, staged.blob):
            worktree = "M"
        return FileStatus(wc_path, ADD, worktree)

    if item == "replaced":
        return FileStatus(wc_path, "R", CLEAN)

    if item == "deleted":
        return FileStatus(wc_path, DELETE, CLEAN)

    if item == "missing":
        # Removed from disk but still versioned: git calls that an unstaged
        # deletion.
        return FileStatus(wc_path, CLEAN, DELETE)

    if item == "modified" or props_modified or item == "incomplete":
        if staged is not None:
            worktree = CLEAN if _worktree_matches_index(abs_path, staged.blob) else "M"
            return FileStatus(wc_path, MODIFY, worktree)
        return FileStatus(wc_path, CLEAN, MODIFY)

    if item == "normal":
        if staged is not None:
            worktree = CLEAN if _worktree_matches_index(abs_path, staged.blob) else "M"
            return FileStatus(wc_path, staged.action, worktree)
        return None

    return None
