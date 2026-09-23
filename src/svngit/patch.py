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
    #: The `a/` or `b/` the header carried, if it had one.
    #:
    #: `path` has it removed, because everything except `apply` wants a
    #: working-copy path to match against. `apply` wants the opposite: `-p<n>`
    #: counts components of the path *as the patch wrote it*, and the prefix is
    #: one of them. Keeping it here lets both have what they need.
    prefix: str = ""
    #: For a rename or a copy, where the content comes from, with its own
    #: prefix. None for an ordinary patch.
    source_path: Optional[str] = None
    source_prefix: str = ""
    #: A copy keeps its source. Parsed so `apply` can refuse it by name
    #: instead of silently treating it as a rename and deleting the original.
    is_copy: bool = False

    @property
    def patch_path(self) -> str:
        """The path as the patch wrote it, which is what `-p<n>` strips."""
        return self.prefix + self.path

    @property
    def source_patch_path(self) -> str:
        """The source path as the patch wrote it. `-p<n>` strips this too."""
        return self.source_prefix + (self.source_path or "")


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
    #: The `a/` side of the last `diff --git`. A `rename from` line names the
    #: source without its prefix, and `-p<n>` has to count that prefix, so the
    #: header is the usable source of the path and this carries it forward.
    old_side: Tuple[str, str] = ("", "")

    for raw in text.splitlines():
        if raw == MBOX_SIGNATURE:
            # Everything after the mbox signature is the tool's version
            # banner, not part of the diff.
            break
        if raw.startswith("#"):
            continue  # git strips comment lines from the edited patch

        if raw.startswith("diff --git "):
            old_side, (prefix, path) = _paths_from_diff_header(raw)
            current = FilePatch(path, prefix=prefix)
            files.append(current)
            hunk = None
            continue

        if raw.startswith("rename from ") or raw.startswith("copy from "):
            # The header's own `a/` side is used rather than the path on this
            # line, so that `-p<n>` counts the same components on both sides.
            if current is not None:
                current.source_prefix, current.source_path = old_side
                current.is_copy = raw.startswith("copy from ")
            continue

        if raw.startswith("Index: "):
            # svn's own diff header. Its `---`/`+++` lines carry a revision
            # annotation, so the Index line is the cleaner source of the path.
            prefix, path = _header_path(raw[len("Index: ") :])
            current = FilePatch(path, prefix=prefix)
            files.append(current)
            hunk = None
            continue

        if raw.startswith("--- "):
            continue
        if raw.startswith("+++ "):
            if current is None:
                prefix, path = _header_path(raw[4:])
                current = FilePatch(path, prefix=prefix)
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

    # A pure rename carries no hunks at all -- the whole patch is the header --
    # so hunks alone cannot decide whether there is anything to do. Everything
    # else with no hunks is dropped as before: `add -e` uses that to mean the
    # user deleted a file's changes from the buffer.
    return [f for f in files if f.hunks or f.source_path is not None]


def _paths_from_diff_header(line: str) -> Tuple[Tuple[str, str], Tuple[str, str]]:
    """Both sides of a `diff --git a/old b/new` line, each split from its prefix.

    The two sides differ only for a rename or a copy; everything else names the
    same file twice. Split on " b/" rather than on whitespace, because a path
    may legitimately contain spaces and only the prefix is a reliable landmark.
    """
    remainder = line[len("diff --git ") :]
    if " b/" in remainder:
        old, new = remainder.split(" b/", 1)
        return _split_prefix(old.strip()), ("b/", new.strip())
    parts = remainder.split()
    return _split_prefix(parts[0]), _split_prefix(parts[-1])


def _header_path(text: str) -> Tuple[str, str]:
    """The path out of a `---`/`+++`/`Index:` line.

    svn appends a tab and an annotation -- `(revision 3)`, `(working copy)` --
    which is not part of the name.
    """
    return _split_prefix(text.split("\t")[0].strip())


def _split_prefix(path: str) -> Tuple[str, str]:
    """Separate a leading `a/` or `b/` from the rest of the path.

    Returned rather than discarded: `apply` has to put it back to count
    components for `-p<n>`. svn's own `Index:` paths carry no prefix, so the
    first element is often empty.
    """
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return prefix, path[2:]
    return "", path


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
                "hunk #%d of %s does not apply.\n%s"
                % (number, file_patch.path, _why_it_failed(base_lines, hunk))
            )
        result.extend(base_lines[cursor:position])
        result.extend(hunk.new_lines)
        cursor = position + len(hunk.old_lines)

    result.extend(base_lines[cursor:])
    return "".join(result)


def _why_it_failed(base_lines: List[str], hunk: PatchHunk) -> str:
    """Explain a failed hunk, naming line endings when that is the cause.

    A patch is parsed into LF lines, so it can never match a CRLF file. That
    failure is not the user's edit, and telling them to check their '-' lines
    would send them looking in the wrong place.
    """
    base_crlf = any(line.endswith("\r\n") for line in base_lines)
    patch_crlf = any(line.endswith("\r\n") for line in hunk.old_lines)
    if base_crlf and not patch_crlf:
        return (
            "The file uses CRLF line endings and the patch uses LF, so no "
            "context line can match. svngit reads patches as LF; convert the "
            "file with `dos2unix`, or use `git add -p`, which works on the "
            "file directly and keeps CRLF intact."
        )
    return (
        "The context lines around your edit no longer match the file. Check "
        "that you changed '-' lines to ' ' rather than deleting them, and "
        "that no context line was altered."
    )


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
