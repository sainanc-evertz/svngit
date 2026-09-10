"""Render Subversion data in the shapes git users expect.

Output is what makes a translation layer feel real or feel like a wrapper, so
these formats follow git's closely -- including the blank-line and indentation
details that tools and eyeballs both key off.
"""

from __future__ import annotations

import difflib
from datetime import datetime
from typing import Iterable, List, Optional, Sequence

from .state import LocalCommit
from .status import StatusReport
from .svnclient import LogEntry

STATUS_WORDS = {
    "A": "new file",
    "M": "modified",
    "D": "deleted",
    "R": "replaced",
    "U": "both modified",
}


def revision_id(revision: int) -> str:
    """How svngit spells a Subversion revision where git would show a sha."""
    return "r%d" % revision


def format_date(value: Optional[datetime]) -> str:
    if value is None:
        return "(unknown date)"
    return value.strftime("%a %b %-d %H:%M:%S %Y %z") if _supports_dash_d() else value.strftime(
        "%a %b %d %H:%M:%S %Y %z"
    )


_DASH_D_SUPPORTED: Optional[bool] = None


def _supports_dash_d() -> bool:
    global _DASH_D_SUPPORTED
    if _DASH_D_SUPPORTED is None:
        try:
            datetime(2020, 1, 2).strftime("%-d")
            _DASH_D_SUPPORTED = True
        except ValueError:
            _DASH_D_SUPPORTED = False
    return _DASH_D_SUPPORTED


def author_line(author: str, uuid: str = "") -> str:
    """Subversion stores a username; git wants "Name <email>"."""
    if "@" in author:
        name = author.split("@", 1)[0]
        return "%s <%s>" % (name, author)
    domain = "localhost" if not uuid else uuid[:8] + ".svn"
    return "%s <%s@%s>" % (author, author, domain)


def format_log_entry(
    entry: LogEntry,
    uuid: str = "",
    oneline: bool = False,
    show_paths: bool = False,
    abbrev: bool = False,
) -> List[str]:
    if oneline:
        ident = revision_id(entry.revision)
        return ["%s %s" % (ident, entry.message.strip().splitlines()[0] if entry.message.strip() else "")]

    lines = ["commit %s" % revision_id(entry.revision)]
    lines.append("Author: %s" % author_line(entry.author, uuid))
    lines.append("Date:   %s" % format_date(entry.date))
    lines.append("")
    body = entry.message.strip("\n")
    if body:
        lines.extend("    " + line if line else "" for line in body.splitlines())
    else:
        lines.append("    (no commit message)")
    lines.append("")
    if show_paths and entry.paths:
        for path in entry.paths:
            lines.append(" %s  %s" % (path.action, path.path))
        lines.append("")
    return lines


def format_local_commit(commit: LocalCommit, uuid: str = "", oneline: bool = False) -> List[str]:
    if oneline:
        return ["%s %s (not pushed)" % (commit.short_id, commit.summary)]
    lines = ["commit %s (local, not pushed)" % commit.id]
    lines.append("Author: %s" % author_line(commit.author, uuid))
    lines.append("Date:   %s" % format_date(datetime.fromtimestamp(commit.timestamp).astimezone()))
    lines.append("")
    body = commit.message.strip("\n")
    lines.extend("    " + line if line else "" for line in body.splitlines())
    lines.append("")
    return lines


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------
def format_porcelain(report: StatusReport) -> List[str]:
    return ["%s%s %s" % (e.index, e.worktree, e.path) for e in report.entries]


def format_short(report: StatusReport) -> List[str]:
    lines = []
    for entry in report.entries:
        index = entry.index if entry.index != "?" else "?"
        worktree = entry.worktree if entry.worktree != "?" else "?"
        lines.append("%s%s %s" % (index, worktree, entry.path))
    return lines


def format_long(
    report: StatusReport,
    branch: str,
    revision: int,
    ahead: int = 0,
    behind: int = 0,
    pending: Sequence[LocalCommit] = (),
    display=lambda p: p,
) -> List[str]:
    lines = ["On branch %s" % branch]
    tracking = "Your working copy is at r%d." % revision
    if behind:
        tracking += " %d revision%s available on the server (use \"git pull\")." % (
            behind,
            "" if behind == 1 else "s",
        )
    lines.append(tracking)
    if pending:
        lines.append("")
        lines.append(
            "You have %d local commit%s not yet pushed (use \"git push\" to send %s to Subversion):"
            % (len(pending), "" if len(pending) == 1 else "s", "it" if len(pending) == 1 else "them")
        )
        for commit in pending:
            lines.append("  %s %s" % (commit.short_id, commit.summary))
    lines.append("")

    if report.unmerged:
        lines.append("Unmerged paths:")
        lines.append('  (use "git add <file>..." to mark resolution)')
        for entry in report.unmerged:
            lines.append("\tboth modified:   %s" % display(entry.path))
        lines.append("")

    staged = [e for e in report.staged if not e.unmerged]
    if staged:
        lines.append("Changes to be committed:")
        lines.append('  (use "git restore --staged <file>..." to unstage)')
        for entry in staged:
            lines.append("\t%-16s %s" % (STATUS_WORDS.get(entry.index, "modified") + ":", display(entry.path)))
        lines.append("")

    unstaged = [e for e in report.unstaged if not e.unmerged]
    if unstaged:
        lines.append("Changes not staged for commit:")
        lines.append('  (use "git add/rm <file>..." to update what will be committed)')
        lines.append('  (use "git restore <file>..." to discard changes in working directory)')
        for entry in unstaged:
            lines.append("\t%-16s %s" % (STATUS_WORDS.get(entry.worktree, "modified") + ":", display(entry.path)))
        lines.append("")

    if report.untracked:
        lines.append("Untracked files:")
        lines.append('  (use "git add <file>..." to include in what will be committed)')
        for entry in report.untracked:
            lines.append("\t%s" % display(entry.path))
        lines.append("")

    if report.clean and not pending:
        lines.append("nothing to commit, working tree clean")
    elif not staged and not report.unmerged:
        if unstaged or report.untracked:
            lines.append('no changes added to commit (use "git add" and/or "git commit -a")')

    # Collapse the trailing blank line git would not print.
    while lines and lines[-1] == "":
        lines.pop()
    return lines


# ----------------------------------------------------------------------
# diffs
# ----------------------------------------------------------------------
def unified_diff(
    old: bytes,
    new: bytes,
    path: str,
    old_label: Optional[str] = None,
    new_label: Optional[str] = None,
) -> List[str]:
    """Produce a git-style unified diff between two blobs."""
    if _is_binary(old) or _is_binary(new):
        if old == new:
            return []
        return [
            "diff --git a/%s b/%s" % (path, path),
            "Binary files a/%s and b/%s differ" % (path, path),
        ]
    old_lines = old.decode("utf-8", errors="replace").splitlines(keepends=True)
    new_lines = new.decode("utf-8", errors="replace").splitlines(keepends=True)
    if old_lines == new_lines:
        return []
    body = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=old_label or ("a/%s" % path),
            tofile=new_label or ("b/%s" % path),
        )
    )
    lines = ["diff --git a/%s b/%s" % (path, path)]
    for line in body:
        lines.append(line.rstrip("\n"))
    return lines


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8000]


def format_diffstat(files: Iterable[tuple]) -> List[str]:
    """files: iterable of (path, insertions, deletions)."""
    rows = list(files)
    if not rows:
        return []
    width = max(len(path) for path, _, _ in rows)
    lines = []
    total_ins = total_del = 0
    for path, ins, dele in rows:
        total_ins += ins
        total_del += dele
        bar = "+" * min(ins, 40) + "-" * min(dele, 40)
        lines.append(" %-*s | %4d %s" % (width, path, ins + dele, bar))
    summary = " %d file%s changed" % (len(rows), "" if len(rows) == 1 else "s")
    if total_ins:
        summary += ", %d insertion%s(+)" % (total_ins, "" if total_ins == 1 else "s")
    if total_del:
        summary += ", %d deletion%s(-)" % (total_del, "" if total_del == 1 else "s")
    lines.append(summary)
    return lines


def count_diff_lines(diff_text: str) -> tuple:
    insertions = deletions = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return insertions, deletions
