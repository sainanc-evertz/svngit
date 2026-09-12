"""Translate git revision syntax into Subversion revision numbers.

The important and easily-missed detail: git's HEAD and Subversion's HEAD are
not the same thing.

    git HEAD          -> svn BASE   (what your working copy is based on)
    git @{upstream}   -> svn HEAD   (the newest revision on the server)

Getting this backwards silently gives you someone else's code, so the mapping
is centralised here rather than spelled out at each call site.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional, Tuple

from .errors import UsageError

if TYPE_CHECKING:  # pragma: no cover
    from .context import Context

_REV_NUMBER = re.compile(r"^r?(\d+)$")
_ANCESTOR = re.compile(r"^(?P<base>[A-Za-z0-9_@{}/.^-]*?)(?P<op>[~^])(?P<count>\d*)$")

UPSTREAM_ALIASES = {
    "@{u}",
    "@{upstream}",
    "origin/HEAD",
    "origin/trunk",
    "origin/master",
    "origin/main",
    "FETCH_HEAD",
}


def split_range(spec: str) -> Tuple[Optional[str], Optional[str]]:
    """Split `a..b` / `a...b` into its endpoints. Returns (None, spec) when the
    spec is a single revision."""
    if "..." in spec:
        left, _, right = spec.partition("...")
        return left or None, right or None
    if ".." in spec:
        left, _, right = spec.partition("..")
        return left or None, right or None
    return None, spec


def is_range(spec: str) -> bool:
    return ".." in spec


def resolve(ctx: "Context", spec: str, target: str = ".") -> int:
    """Resolve one git revision expression to an svn revision number."""
    spec = spec.strip()
    if not spec:
        raise UsageError("empty revision")

    if spec in UPSTREAM_ALIASES:
        return ctx.svn.info(target, revision="HEAD").revision

    if spec.upper() in ("HEAD", "BASE", "@"):
        return ctx.svn.info(target).revision

    if spec.upper() == "PREV":
        return _nth_ancestor(ctx, target, 1)

    if spec.upper() == "COMMITTED":
        info = ctx.svn.info(target)
        return info.last_changed_rev or info.revision

    match = _REV_NUMBER.match(spec)
    if match:
        return int(match.group(1))

    match = _ANCESTOR.match(spec)
    if match:
        base = match.group("base") or "HEAD"
        raw_count = match.group("count")
        # `HEAD^` means one back; `HEAD~` also means one back.
        count = int(raw_count) if raw_count else 1
        if match.group("op") == "^" and raw_count and int(raw_count) > 1:
            # git's `^N` selects the Nth parent of a merge. Subversion history
            # is linear, so there is no second parent to select.
            raise UsageError(
                "%s selects a merge parent, which Subversion history does not "
                "have; use ~%s to walk back %s revisions instead"
                % (spec, raw_count, raw_count)
            )
        start = resolve(ctx, base, target) if base.upper() != "HEAD" else None
        return _nth_ancestor(ctx, target, count, start)

    # A tag or branch name: resolve to the last revision that changed it.
    revision = _resolve_ref(ctx, spec)
    if revision is not None:
        return revision

    raise UsageError("unknown revision or path not in the working copy: %s" % spec)


def _nth_ancestor(
    ctx: "Context", target: str, count: int, start: Optional[int] = None
) -> int:
    """Walk back `count` revisions *that touched this path*.

    git counts commits; svn revision numbers are repository-global and mostly
    unrelated to a given path, so counting revisions in the path's own log is
    the faithful translation.
    """
    revision_spec = "%d:0" % start if start is not None else "BASE:0"
    entries = ctx.svn.log(target, revision=revision_spec, limit=count + 1)
    if len(entries) <= count:
        raise UsageError(
            "%s~%d is before the start of history for %s"
            % ("HEAD" if start is None else "r%d" % start, count, target)
        )
    return entries[count].revision


def _resolve_ref(ctx: "Context", name: str) -> Optional[int]:
    """Resolve a branch or tag name to the revision it was last changed at."""
    info = ctx.info
    lay = ctx.layout
    if not lay.standard:
        return None
    root = info.repos_root.rstrip("/")
    candidates = []
    short = name[len("origin/") :] if name.startswith("origin/") else name
    if short.startswith("tags/"):
        candidates.append("%s/%s" % (root, lay.tag_path(short[len("tags/") :])))
    else:
        candidates.append("%s/%s" % (root, lay.branch_path(short)))
        candidates.append("%s/%s" % (root, lay.tag_path(short)))
    for url in candidates:
        entries = ctx.svn.log(url, limit=1)
        if entries:
            return entries[0].revision
    return None


def to_svn_range(ctx: "Context", spec: str, target: str = ".") -> str:
    """Turn a git revision or range into the argument for `svn -r`."""
    if is_range(spec):
        left, right = split_range(spec)
        start = resolve(ctx, left, target) if left else 0
        end = resolve(ctx, right, target) if right else ctx.svn.info(target).revision
        return "%d:%d" % (start, end)
    return str(resolve(ctx, spec, target))
