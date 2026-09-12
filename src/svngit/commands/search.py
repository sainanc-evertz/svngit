"""grep and check-ignore.

Subversion has no search command, so `git grep` is implemented here rather
than translated. The part that matters is *which* files it looks at: git
searches tracked files, so this excludes anything `svn status` calls
unversioned or ignored, and never descends into `.svn`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

import fnmatch
import os
import re
from pathlib import Path
from typing import Iterator, List, Optional, Set

from .. import status as status_mod
from ..cliargs import parse, refuse
from ..errors import UsageError
from ..hunks import is_binary

if TYPE_CHECKING:  # pragma: no cover
    from ..context import Context

#: Subversion's own default ignores, used when nothing is configured.
DEFAULT_GLOBAL_IGNORES = (
    "*.o *.lo *.la *.al .libs *.so *.so.[0-9]* *.a *.pyc *.pyo __pycache__ "
    "*.rej *~ #*# .#* .*.swp .DS_Store [Tt]humbs.db"
).split()


def tracked_files(
    ctx: "Context", paths: Optional[List[str]] = None, include_untracked: bool = False
) -> Iterator[Path]:
    """Every versioned file under `paths`, or under the working copy root.

    Derived by walking the tree and subtracting what `svn status` reports as
    unversioned or ignored, which is one local svn call rather than one per
    file.
    """
    excluded: Set[str] = set()
    for entry in status_mod.compute(ctx, paths, include_ignored=True).entries:
        if entry.ignored or (entry.untracked and not include_untracked):
            excluded.add(entry.path)

    roots = [ctx.abs_path(p) for p in paths] if paths else [ctx.wc_root]
    for root in roots:
        if root.is_file():
            relative = root.relative_to(ctx.wc_root).as_posix()
            if relative not in excluded:
                yield root
            continue
        for directory, subdirs, filenames in os.walk(root):
            subdirs[:] = [d for d in subdirs if d != ".svn"]
            here = Path(directory)
            # An excluded directory takes its whole subtree with it.
            subdirs[:] = [
                d
                for d in subdirs
                if (here / d).relative_to(ctx.wc_root).as_posix() not in excluded
            ]
            for name in sorted(filenames):
                candidate = here / name
                if candidate.relative_to(ctx.wc_root).as_posix() in excluded:
                    continue
                yield candidate


# ----------------------------------------------------------------------
# grep
# ----------------------------------------------------------------------
def cmd_grep(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=[
            "ignore-case",
            "i",
            "line-number",
            "n",
            "files-with-matches",
            "l",
            "files-without-match",
            "L",
            "count",
            "c",
            "word-regexp",
            "w",
            "extended-regexp",
            "E",
            "fixed-strings",
            "F",
            "basic-regexp",
            "G",
            "invert-match",
            "v",
            "no-filename",
            "h",
            "untracked",
            "I",
            "cached",
            "name-only",
            "quiet",
            "q",
        ],
        values=["max-count", "m"],
    )
    refuse(
        "grep",
        opts,
        {
            "cached": "there is no index of file content to search; `git grep` here "
            "searches the working copy.",
        },
    )
    # -E/-G select POSIX regex dialects; Python's `re` is a superset of both,
    # so the pattern behaves the same either way.
    if not opts.positionals and not opts.after_dashdash:
        raise UsageError("git grep <pattern> [<path>...]")

    pattern_text = opts.positionals[0]
    search_paths = opts.positionals[1:] + list(opts.after_dashdash)
    wc_paths = ctx.to_wc_paths(search_paths) if search_paths else None

    flags = re.IGNORECASE if opts.has("ignore-case", "i") else 0
    if opts.has("fixed-strings", "F"):
        pattern_text = re.escape(pattern_text)
    if opts.has("word-regexp", "w"):
        pattern_text = r"\b(?:%s)\b" % pattern_text
    try:
        pattern = re.compile(pattern_text, flags)
    except re.error as exc:
        raise UsageError("invalid pattern %r: %s" % (opts.positionals[0], exc))

    invert = opts.has("invert-match", "v")
    show_names_only = opts.has("files-with-matches", "l", "name-only")
    show_misses_only = opts.has("files-without-match", "L")
    counting = opts.has("count", "c")
    show_filename = not opts.has("no-filename", "h")
    number = opts.has("line-number", "n")
    limit = (
        int(str(opts.first("max-count", "m"))) if opts.has("max-count", "m") else None
    )

    found_any = False
    for absolute in tracked_files(ctx, wc_paths, opts.has("untracked")):
        try:
            data = absolute.read_bytes()
        except OSError:
            continue
        if is_binary(data) and not opts.has("I"):
            # git reports a binary match without printing the line.
            if not invert and pattern.search(data.decode("latin-1")):
                found_any = True
                if not opts.has("quiet", "q"):
                    ctx.echo(
                        "Binary file %s matches"
                        % ctx.display_path(absolute.relative_to(ctx.wc_root).as_posix())
                    )
            continue

        display = ctx.display_path(absolute.relative_to(ctx.wc_root).as_posix())
        matches = []
        for index, line in enumerate(
            data.decode("utf-8", errors="replace").splitlines(), 1
        ):
            if bool(pattern.search(line)) != invert:
                matches.append((index, line))
                if limit and len(matches) >= limit:
                    break

        if show_misses_only:
            if not matches:
                found_any = True
                ctx.echo(display)
            continue
        if not matches:
            continue

        found_any = True
        if opts.has("quiet", "q"):
            return 0
        if show_names_only:
            ctx.echo(display)
        elif counting:
            ctx.echo(
                "%s:%d" % (display, len(matches))
                if show_filename
                else str(len(matches))
            )
        else:
            for index, line in matches:
                parts = []
                if show_filename:
                    parts.append(display)
                if number:
                    parts.append(str(index))
                parts.append(line)
                ctx.echo(":".join(parts))

    return 0 if found_any else 1


# ----------------------------------------------------------------------
# check-ignore
# ----------------------------------------------------------------------
def cmd_check_ignore(ctx: "Context", argv: List[str]) -> int:
    opts = parse(argv, flags=["verbose", "v", "non-matching", "n", "quiet", "q"])
    if not opts.paths:
        raise UsageError("git check-ignore <path>...")

    matched = False
    for user_path in opts.paths:
        wc_path = ctx.to_wc_path(user_path)
        source, pattern = _ignore_match(ctx, wc_path)
        if pattern is None:
            if opts.has("non-matching", "n") and not opts.has("quiet", "q"):
                ctx.echo("::\t%s" % user_path)
            continue
        matched = True
        if opts.has("quiet", "q"):
            return 0
        if opts.has("verbose", "v"):
            ctx.echo("%s\t%s\t%s" % (source, pattern, user_path))
        else:
            ctx.echo(user_path)

    return 0 if matched else 1


def _ignore_match(ctx: "Context", wc_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Which pattern, if any, makes Subversion ignore this path.

    Patterns are read rather than inferred from `svn status`, so a path that
    does not exist yet can still be checked -- which is most of the point of
    the command.
    """
    name = wc_path.rsplit("/", 1)[-1]
    parent = wc_path.rsplit("/", 1)[0] if "/" in wc_path else ""
    parent_target = ctx.svn_target(parent)

    local = ctx.svn.propget("svn:ignore", parent_target)
    for entry in local.split():
        if fnmatch.fnmatch(name, entry):
            return "%s/svn:ignore" % (parent or "."), entry

    inherited = ctx.svn.propget("svn:global-ignores", str(ctx.wc_root))
    for entry in inherited.split():
        if fnmatch.fnmatch(name, entry):
            return "svn:global-ignores", entry

    for entry in DEFAULT_GLOBAL_IGNORES:
        if fnmatch.fnmatch(name, entry):
            return "svn default ignores", entry

    return None, None
