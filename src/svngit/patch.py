"""Rendering a unified patch, and applying one back onto known content.

This is what `git add -e` needs and `git add -p` does not. Hunk picking can
work on difflib opcodes, because the user only ever chooses between two known
versions. Editing a patch by hand is open-ended -- lines can be deleted,
turned into context, or rewritten outright -- so the edited text has to be
parsed and applied for real.

Two details make hand-edited patches survivable:

* Hunk headers are treated as a hint, never as truth. Git recomputes them
  after an edit, and a human will not have adjusted the counts, so each hunk
  is located by matching its old lines and the header only breaks ties.
* A body line that is completely empty is read as a context line. Editors
  routinely strip the trailing space from a context line representing a blank
  line, and rejecting that would make the feature infuriating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import hunks as hunks_mod
from .errors import SvnGitError

NO_NEWLINE = "\\ No newline at end of file"

#: `git format-patch` ends the diff with this before its version banner. The
#: trailing space is load-bearing -- without it the line is indistinguishable
#: from the deletion of a line containing a single dash.
MBOX_SIGNATURE = "-- "


class PatchError(SvnGitError):
    """The edited patch does not apply to the content it was generated from."""


@dataclass
class PatchHunk:
    #: 1-based start line from the @@ header. A hint for disambiguation only.
    old_start: int
    old_lines: List[str] = field(default_factory=list)
    new_lines: List[str] = field(default_factory=list)


@dataclass
class FilePatch:
    path: str
    hunks: List[PatchHunk] = field(default_factory=list)


# ----------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------
def render_file_patch(path: str, base: str, work: str, context: int = 3) -> List[str]:
    """A unified diff for one file, or [] when it has not changed."""
    diff = hunks_mod.diff_file(base, work, context)
    if diff.empty:
        return []

    lines = [
        "diff --git a/%s b/%s" % (path, path),
        "--- a/%s" % path,
        "+++ b/%s" % path,
    ]
    for hunk in diff.hunks:
        lines.append(hunk.header)
        for prefix, text in hunk_body(diff, hunk):
            lines.append(prefix + text.rstrip("\n"))
            if not text.endswith("\n"):
                lines.append(NO_NEWLINE)
    return lines


def hunk_body(diff: hunks_mod.FileDiff, hunk: hunks_mod.Hunk) -> List[Tuple[str, str]]:
    """The hunk's body as (prefix, raw line) pairs, newlines intact."""
    first = diff.ops[hunk.changed_ops[0]]
    last = diff.ops[hunk.changed_ops[-1]]
    out: List[Tuple[str, str]] = []
    out.extend((" ", text) for text in diff.base[hunk.base_start : first[1]])
    for index in range(hunk.changed_ops[0], hunk.changed_ops[-1] + 1):
        tag, i1, i2, j1, j2 = diff.ops[index]
        if tag == "equal":
            out.extend((" ", text) for text in diff.base[i1:i2])
        else:
            out.extend(("-", text) for text in diff.base[i1:i2])
            out.extend(("+", text) for text in diff.work[j1:j2])
    out.extend((" ", text) for text in diff.base[last[2] : hunk.base_end])
    return out


def from_diff_hunk(diff: hunks_mod.FileDiff, hunk: hunks_mod.Hunk) -> PatchHunk:
    """Express one of `hunks.diff_file`'s hunks as a patch hunk.

    Lets the `git add -p` loop hold accepted hunks in a single representation
    whether the user took them as-is or rewrote them in an editor.
    """
    old: List[str] = []
    new: List[str] = []
    for prefix, text in hunk_body(diff, hunk):
        if prefix in (" ", "-"):
            old.append(text)
        if prefix in (" ", "+"):
            new.append(text)
    return PatchHunk(old_start=hunk.base_start + 1, old_lines=old, new_lines=new)


def render_hunk(diff: hunks_mod.FileDiff, hunk: hunks_mod.Hunk) -> List[str]:
    """One hunk on its own, for editing in isolation."""
    lines = [hunk.header]
    for prefix, text in hunk_body(diff, hunk):
        lines.append(prefix + text.rstrip("\n"))
        if not text.endswith("\n"):
            lines.append(NO_NEWLINE)
    return lines


def hunk_stats(hunk: PatchHunk) -> Tuple[int, int]:
    """(insertions, deletions) for a patch hunk, ignoring its context lines."""
    from difflib import SequenceMatcher

    insertions = deletions = 0
    matcher = SequenceMatcher(None, hunk.old_lines, hunk.new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            deletions += i2 - i1
            insertions += j2 - j1
    return insertions, deletions


# ----------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------
def parse_patch(text: str, default_path: Optional[str] = None) -> List[FilePatch]:
    """Parse a (possibly hand-edited) unified patch.

    `default_path` attributes a bare `@@` fragment to a file, which is what
    `git add -p`'s per-hunk edit produces.
    """
    files: List[FilePatch] = []
    current: Optional[FilePatch] = None
    hunk: Optional[PatchHunk] = None
    #: Which side(s) the last emitted body line went to, for the no-newline marker.
    last_sides: Tuple[str, ...] = ()

    for raw in text.splitlines():
        if raw == MBOX_SIGNATURE:
            # Everything after the mbox signature is the tool's version
            # banner, not part of the diff.
            break
        if raw.startswith("#"):
            continue  # git strips comment lines from the edited patch

        if raw.startswith("diff --git "):
            current = FilePatch(_path_from_diff_header(raw))
            files.append(current)
            hunk = None
            continue

        if raw.startswith("Index: "):
            # svn's own diff header. Its `---`/`+++` lines carry a revision
            # annotation, so the Index line is the cleaner source of the path.
            current = FilePatch(_header_path(raw[len("Index: ") :]))
            files.append(current)
            hunk = None
            continue

        if raw.startswith("--- "):
            continue
        if raw.startswith("+++ "):
            if current is None:
                current = FilePatch(_header_path(raw[4:]))
                files.append(current)
            continue

        if raw.startswith("@@"):
            if current is None:
                if default_path is None:
                    raise PatchError("patch fragment has no file header")
                current = FilePatch(default_path)
                files.append(current)
            hunk = PatchHunk(old_start=_old_start(raw))
            current.hunks.append(hunk)
            last_sides = ()
            continue

        if hunk is None:
            continue  # preamble or stray text between files

        if raw == NO_NEWLINE or raw.startswith("\\ No newline"):
            for side in last_sides:
                target = hunk.old_lines if side == "old" else hunk.new_lines
                if target:
                    target[-1] = target[-1].rstrip("\n")
            continue

        marker, content = (raw[0], raw[1:]) if raw else (" ", "")
        line = content + "\n"
        if marker == " ":
            hunk.old_lines.append(line)
            hunk.new_lines.append(line)
            last_sides = ("old", "new")
        elif marker == "-":
            hunk.old_lines.append(line)
            last_sides = ("old",)
        elif marker == "+":
            hunk.new_lines.append(line)
            last_sides = ("new",)
        else:
            raise PatchError(
                "unrecognised line in patch (expected it to start with ' ', '-' "
                "or '+'):\n  %s" % raw
            )

    return [f for f in files if f.hunks]


def _path_from_diff_header(line: str) -> str:
    remainder = line[len("diff --git ") :]
    if " b/" in remainder:
        return remainder.split(" b/", 1)[1].strip()
    return _strip_prefix(remainder.split()[-1])


def _header_path(text: str) -> str:
    """The path out of a `---`/`+++`/`Index:` line.

    svn appends a tab and an annotation -- `(revision 3)`, `(working copy)` --
    which is not part of the name.
    """
    return _strip_prefix(text.split("\t")[0].strip())


def _strip_prefix(path: str) -> str:
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[2:]
    return path


def _old_start(header: str) -> int:
    try:
        old = header.split("-", 1)[1].split(" ", 1)[0]
        return int(old.split(",")[0])
    except (IndexError, ValueError):
        return 1


# ----------------------------------------------------------------------
# applying
# ----------------------------------------------------------------------
def apply_file_patch(base: str, file_patch: FilePatch) -> str:
    """Apply a parsed patch to `base`, returning the new content."""
    base_lines = base.splitlines(keepends=True)
    result: List[str] = []
    cursor = 0

    for number, hunk in enumerate(file_patch.hunks, start=1):
        position = _locate(base_lines, hunk.old_lines, hunk.old_start - 1, cursor)
        if position is None:
            raise PatchError(
                "hunk #%d of %s does not apply.\n"
                "The context lines around your edit no longer match the file. "
                "Check that you changed '-' lines to ' ' rather than deleting "
                "them, and that no context line was altered."
                % (number, file_patch.path)
            )
        result.extend(base_lines[cursor:position])
        result.extend(hunk.new_lines)
        cursor = position + len(hunk.old_lines)

    result.extend(base_lines[cursor:])
    return "".join(result)


def _locate(
    haystack: Sequence[str], needle: Sequence[str], hint: int, start: int
) -> Optional[int]:
    """Find `needle` in `haystack` at or after `start`, nearest to `hint`.

    A hand-edited patch has stale line numbers, so the header is only used to
    choose between otherwise equally valid matches.
    """
    if not needle:
        return max(start, min(hint, len(haystack)))
    span = len(needle)
    matches = [
        index
        for index in range(start, len(haystack) - span + 1)
        if list(haystack[index : index + span]) == list(needle)
    ]
    if not matches:
        return None
    return min(matches, key=lambda index: abs(index - hint))


# ----------------------------------------------------------------------
# three-way merge
# ----------------------------------------------------------------------
def to_git_headers(diff_text: str, wc_root: "Path") -> str:
    """Rewrite `svn diff` headers into git's form.

    svn labels files with `Index:` and annotates the ---/+++ paths with a
    revision, using absolute paths. git uses `diff --git a/x b/x` with paths
    relative to the tree. Normalising here means every svngit diff looks the
    same whichever code path produced it, and -- because `git apply` strips
    one leading component by default -- that `svngit diff | svngit apply`
    round-trips the way it does in git.
    """
    from pathlib import Path

    root = Path(wc_root).resolve()

    def relative(raw: str) -> str:
        candidate = Path(raw.split("\t")[0].strip())
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            return candidate.as_posix()

    out: List[str] = []
    for line in diff_text.splitlines():
        if line.startswith("Index: "):
            path = relative(line[len("Index: ") :])
            out.append("diff --git a/%s b/%s" % (path, path))
            continue
        if line.strip() and set(line.strip()) == {"="}:
            continue  # svn's rule under the Index line
        if line.startswith("--- ") or line.startswith("+++ "):
            prefix = "a/" if line.startswith("---") else "b/"
            body = line[4:]
            # Idempotent: some diffs reach here already in git form, and
            # prefixing twice would give `a/a/file`.
            if body.startswith(prefix) or body.strip() == "/dev/null":
                out.append(line)
            else:
                out.append("%s %s%s" % (line[:3], prefix, relative(body)))
            continue
        out.append(line)
    return "\n".join(out)


def merge3(ancestor: str, ours: str, theirs: str) -> Optional[str]:
    """Combine two independent sets of edits to `ancestor`. None on conflict.

    Restoring a `git stash -p` entry onto a file that has moved on since needs
    this rather than patch application: the stashed delta's context comes from
    the content the stash left behind, so an unrelated edit anywhere near it
    would stop a patch from matching. With all three versions in hand the
    edits can simply be combined.
    """
    base = ancestor.splitlines(keepends=True)
    regions = _edits(base, ours.splitlines(keepends=True)) + _edits(
        base, theirs.splitlines(keepends=True)
    )

    unique = []
    for region in sorted(regions, key=lambda r: (r[0], r[1])):
        if region not in unique:
            unique.append(region)

    out: List[str] = []
    cursor = 0
    for start, end, replacement in unique:
        if start < cursor:
            return None  # both sides rewrote the same lines
        out.extend(base[cursor:start])
        out.extend(replacement)
        cursor = end
    out.extend(base[cursor:])
    return "".join(out)


def _edits(base: List[str], other: List[str]) -> List[Tuple[int, int, Tuple[str, ...]]]:
    """Changed regions as (start, end, replacement) against `base`."""
    from difflib import SequenceMatcher

    matcher = SequenceMatcher(None, base, other, autojunk=False)
    return [
        (i1, i2, tuple(other[j1:j2]))
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]
