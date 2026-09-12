"""Colour for diff output, following git's rules.

Colour is added at the moment text is printed, never earlier. Patch text that
is going to be parsed again or written to a file -- the buffer `git add -e`
hands to your editor, the files `git format-patch` writes -- is produced by
the same code paths and must stay clean, so nothing here is applied to it.

Detection follows git, with the addition of `NO_COLOR`:

    --color / --color=always        on
    --no-color / --color=never      off
    NO_COLOR set                    off
    color.diff, else color.ui       whatever it says
    otherwise                       on only when stdout is a terminal

The last line is the one that matters most. `git diff > patch` must produce a
file that `git apply` can read, and a terminal check is what guarantees it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

RESET = "\033[m"

#: git's own defaults for `color.diff.<slot>`.
META = "\033[1m"  # bold
FRAG = "\033[36m"  # cyan
OLD = "\033[31m"  # red
NEW = "\033[32m"  # green
COMMIT = "\033[33m"  # yellow

#: `color.status.<slot>`. git paints a staged path green and an unstaged or
#: untracked one red, which is the whole signal: green is safe to commit.
ADDED = "\033[32m"  # green
CHANGED = "\033[31m"  # red
UNTRACKED = "\033[31m"  # red
UNMERGED = "\033[31m"  # red
BRANCH_COLOUR = "\033[32m"  # green

AUTO = "auto"
ALWAYS = "always"
NEVER = "never"
_TRUE = {"always", "true", "yes", "on", "1"}
_FALSE = {"never", "false", "no", "off", "0"}


@dataclass(frozen=True)
class Palette:
    """Either a set of escape codes, or all empty strings when colour is off.

    Keeping a disabled palette rather than a None means call sites never need
    to branch: they always wrap, and wrapping with "" is a no-op.
    """

    enabled: bool = False
    meta: str = ""
    frag: str = ""
    old: str = ""
    new: str = ""
    commit: str = ""
    reset: str = ""
    added: str = ""
    changed: str = ""
    untracked: str = ""
    unmerged: str = ""
    branch: str = ""

    @classmethod
    def on(cls) -> "Palette":
        return cls(
            True,
            META,
            FRAG,
            OLD,
            NEW,
            COMMIT,
            RESET,
            ADDED,
            CHANGED,
            UNTRACKED,
            UNMERGED,
            BRANCH_COLOUR,
        )

    @classmethod
    def off(cls) -> "Palette":
        return cls()

    def paint(self, colour: str, text: str) -> str:
        return "%s%s%s" % (colour, text, self.reset) if colour else text


def _setting(value: Optional[str]) -> Optional[bool]:
    """Read a git-style colour setting. None when it says "auto"."""
    if value is None:
        return None
    normalised = str(value).strip().lower()
    if normalised in _TRUE:
        return True
    if normalised in _FALSE:
        return False
    return None  # "auto", or anything unrecognised


def want_colour(ctx, opts=None, keys=("color.diff",)) -> bool:
    """Whether this invocation should emit colour.

    `keys` is the command-specific config consulted before the general
    `color.ui`, so `color.status` can differ from `color.diff` as in git.
    """
    if opts is not None:
        if opts.has("no-color") or _setting(opts.get("color")) is False:
            return False
        if opts.has("color"):
            # Bare --color means always; --color=auto falls through to the
            # terminal check below.
            decided = _setting(opts.get("color"))
            if decided is True or opts.get("color") in (1, None):
                return True

    if os.environ.get("NO_COLOR") is not None:
        return False

    for key in keys + ("color.ui",):
        decided = _setting(ctx.state.get_config(key)) if _has_state(ctx) else None
        if decided is not None:
            return decided

    return is_terminal(ctx.stdout)


def _has_state(ctx) -> bool:
    """Config lives per working copy, and `git diff` can be run without one."""
    try:
        return ctx.in_working_copy()
    except Exception:
        return False


def is_terminal(stream) -> bool:
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def palette_for(ctx, opts=None, keys=("color.diff",)) -> Palette:
    return Palette.on() if want_colour(ctx, opts, keys) else Palette.off()


# ----------------------------------------------------------------------
# painting
# ----------------------------------------------------------------------
def paint_diff(text: str, palette: Palette) -> str:
    """Colour a unified diff the way git does."""
    if not palette.enabled or not text:
        return text
    painted = [paint_diff_line(line, palette) for line in text.split("\n")]
    return "\n".join(painted)


def paint_diff_line(line: str, palette: Palette) -> str:
    if not palette.enabled:
        return line

    # Order matters: `---` and `+++` are file headers, not removed or added
    # lines, so they have to be checked before the single-character cases.
    if line.startswith(("diff --git ", "diff -", "index ", "Index: ", "--- ", "+++ ")):
        return palette.paint(palette.meta, line)
    if line.startswith("===") and set(line.strip()) == {"="}:
        return palette.paint(palette.meta, line)
    if line.startswith("@@"):
        return _paint_hunk_header(line, palette)
    if line.startswith("+"):
        return palette.paint(palette.new, line)
    if line.startswith("-"):
        return palette.paint(palette.old, line)
    return line


def _paint_hunk_header(line: str, palette: Palette) -> str:
    """`@@ -1,5 +1,5 @@ func()` -- the ranges are cyan, any trailing function
    context is left plain, as git does."""
    end = line.find("@@", 2)
    if end == -1:
        return palette.paint(palette.frag, line)
    ranges, remainder = line[: end + 2], line[end + 2 :]
    return palette.paint(palette.frag, ranges) + remainder


def paint_log_line(line: str, palette: Palette) -> str:
    """Colour the `commit rNNN` heading, matching git's yellow."""
    if palette.enabled and line.startswith("commit "):
        return palette.paint(palette.commit, line)
    return paint_diff_line(line, palette)


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------
def status_colour(palette: Palette, code: str, staged: bool) -> str:
    """The slot for one status entry.

    The index column is the staged side, so green; the worktree column is not
    staged yet, so red. That is the whole signal git is sending -- green means
    this is what a commit would capture.
    """
    if not palette.enabled:
        return ""
    if "U" in code:
        return palette.unmerged
    if code == "??":
        return palette.untracked
    return palette.added if staged else palette.changed


def paint_porcelain(palette: Palette, index: str, worktree: str, path: str) -> str:
    """`XY path` with each column coloured for the side it describes."""
    if not palette.enabled:
        return "%s%s %s" % (index, worktree, path)
    code = index + worktree
    if code == "??":
        return palette.paint(palette.untracked, "?? " + path)
    left = palette.paint(palette.added, index) if index.strip() else index
    right = palette.paint(palette.changed, worktree) if worktree.strip() else worktree
    return "%s%s %s" % (left, right, path)
