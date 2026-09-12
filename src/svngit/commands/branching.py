"""branch, checkout, switch, merge, cherry-pick, revert, tag.

Subversion branches are directories, so every operation here is a server-side
copy, move or delete rather than a ref update. Two consequences leak through
and are surfaced rather than hidden:

* Creating or deleting a branch is immediately visible to everyone, because it
  is a commit. There is no local-only branch.
* Merges record `svn:mergeinfo` on the working copy root. That property has to
  be committed with the merge or Subversion loses track of what was merged, so
  merge-like commands commit directly instead of queueing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import fnmatch
from typing import List, Optional

from .. import formatting, layout as layout_mod, status as status_mod
from ..svnclient import SvnInfo
from ..cliargs import no_effect, parse, refuse
from ..errors import SvnGitError, UsageError
from ..layout import TRUNK_BRANCH_NAME
from .. import revisions as rev_mod

if TYPE_CHECKING:  # pragma: no cover
    from ..context import Context


def _after_server_commit(ctx: "Context") -> None:
    """Catch the working copy up after a server-side copy, move or delete.

    Creating a branch or a tag is a commit, so the repository moves on while
    the working copy does not. Left alone, the stale BASE makes the very next
    `git push` look like the server has work you do not have.
    """
    from .sync import _refresh_base_revision

    _refresh_base_revision(ctx)


def _require_empty_queue(ctx: "Context", verb: str) -> None:
    pending = ctx.state.commits
    if pending:
        raise SvnGitError(
            "you have %d local commit%s that %s not been pushed.\n"
            "Subversion records %s directly on the server, and doing that now "
            "would sweep your unpushed work into it.\n"
            'Run "git push" first.'
            % (
                len(pending),
                "" if len(pending) == 1 else "s",
                "has" if len(pending) == 1 else "have",
                verb,
            )
        )


def _require_clean_tree(ctx: "Context", verb: str) -> None:
    report = status_mod.compute(ctx)
    dirty = [e for e in report.entries if not e.untracked and not e.ignored]
    if dirty:
        raise SvnGitError(
            "your local changes would be overwritten by %s:\n%s\n"
            'Commit or stash them first ("git stash").'
            % (verb, "\n".join("\t" + ctx.display_path(e.path) for e in dirty))
        )


# ----------------------------------------------------------------------
# branch
# ----------------------------------------------------------------------
def cmd_branch(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=[
            "all",
            "a",
            "remotes",
            "r",
            "list",
            "l",
            "verbose",
            "v",
            "force",
            "f",
            "quiet",
            "q",
        ],
        values=["delete", "d", "D", "move", "m", "copy", "c", "merged", "no-merged"],
    )
    info, lay = ctx.info, ctx.layout
    quiet = opts.has("quiet", "q")
    # -a/--all is the default here: every Subversion branch lives on the
    # server, so there is no local-only set to add.

    copy = opts.first("copy", "c")
    if copy:
        return _copy_branch(ctx, str(copy), opts.positionals, quiet)

    delete = opts.first("delete", "d", "D")
    if delete:
        return _delete_branch(ctx, str(delete), force=opts.has("D", "force", "f"))

    rename = opts.first("move", "m")
    if rename:
        return _rename_branch(ctx, str(rename), opts.positionals)

    if opts.positionals:
        name = opts.positionals[0]
        start_point = opts.positionals[1] if len(opts.positionals) > 1 else None
        _create_branch(ctx, name, start_point, quiet=quiet)
        return 0

    current = ctx.branch
    names = layout_mod.list_branches(ctx.svn, info, lay)

    if opts.has("merged") or opts.has("no-merged"):
        into = str(opts.first("merged", "no-merged") or "")
        want_merged = opts.has("merged")
        names = [
            name
            for name in names
            if name != current and _is_merged(ctx, info, lay, name, into) is want_merged
        ]

    for name in names:
        marker = "*" if name == current else " "
        label = name if not opts.has("remotes", "r") else "origin/" + name
        if opts.has("verbose", "v"):
            url = layout_mod.branch_url(info, lay, name)
            entries = ctx.svn.log(url, limit=1)
            summary = (
                entries[0].message.strip().splitlines()[0]
                if entries and entries[0].message.strip()
                else ""
            )
            revision = formatting.revision_id(entries[0].revision) if entries else "-"
            ctx.echo("%s %-24s %-8s %s" % (marker, label, revision, summary))
        else:
            ctx.echo("%s %s" % (marker, label))
    return 0


def _is_merged(
    ctx: "Context", info: SvnInfo, lay: layout_mod.Layout, name: str, into: str = ""
) -> bool:
    """Whether every revision on `name` is already merged into `into`.

    Subversion answers this directly: `svn mergeinfo --show-revs eligible`
    lists what has not been merged yet, so an empty answer means merged.
    """
    source = layout_mod.branch_url(info, lay, name)
    target = layout_mod.branch_url(info, lay, into) if into else str(ctx.wc_root)
    result = ctx.svn.run(
        "mergeinfo", "--show-revs", "eligible", source, target, check=False
    )
    if not result.ok:
        return False
    return not result.stdout.strip()


def _copy_branch(
    ctx: "Context", new_name: str, positionals: List[str], quiet: bool = False
) -> int:
    """`git branch -c`: another server-side copy, like creating one."""
    info, lay = ctx.info, ctx.layout
    source_name = positionals[0] if positionals else ctx.branch
    source = layout_mod.branch_url(info, lay, source_name)
    target = layout_mod.branch_url(info, lay, new_name)
    if ctx.svn.path_exists(target):
        raise SvnGitError("a branch named '%s' already exists" % new_name)
    ctx.svn.run(
        "copy",
        source,
        target,
        "-m",
        "Copy branch %s to %s" % (source_name, new_name),
        mutating=True,
    )
    _after_server_commit(ctx)
    if not quiet:
        ctx.echo("Copied branch %s to %s" % (source_name, new_name))
    return 0


def _create_branch(
    ctx: "Context", name: str, start_point: Optional[str] = None, quiet: bool = False
) -> str:
    info, lay = ctx.info, ctx.layout
    target = layout_mod.branch_url(info, lay, name)
    if ctx.svn.path_exists(target):
        raise SvnGitError("a branch named '%s' already exists" % name)

    if start_point:
        source = layout_mod.branch_url(info, lay, start_point)
        if not ctx.svn.path_exists(source):
            revision = rev_mod.resolve(ctx, start_point, str(ctx.wc_root))
            source = "%s@%d" % (info.url, revision)
    else:
        source = info.url

    if not quiet:
        ctx.note(
            "creating a branch in Subversion is a server-side commit, so '%s' is "
            "visible to everyone immediately" % name
        )
    ctx.svn.run("copy", source, target, "-m", "Create branch %s" % name, mutating=True)
    _after_server_commit(ctx)
    return target


def _delete_branch(ctx: "Context", name: str, force: bool = False) -> int:
    info, lay = ctx.info, ctx.layout
    if name == ctx.branch:
        raise SvnGitError("cannot delete branch '%s': you are currently on it" % name)
    url = layout_mod.branch_url(info, lay, name)
    if not ctx.svn.path_exists(url):
        raise SvnGitError("branch '%s' not found" % name)
    ctx.svn.run("delete", url, "-m", "Delete branch %s" % name, mutating=True)
    _after_server_commit(ctx)
    ctx.echo("Deleted branch %s (was %s)." % (name, url))
    return 0


def _rename_branch(ctx: "Context", new_name: str, positionals: List[str]) -> int:
    info, lay = ctx.info, ctx.layout
    old_name = positionals[0] if positionals else ctx.branch
    if old_name == TRUNK_BRANCH_NAME:
        raise SvnGitError("refusing to rename trunk")
    source = layout_mod.branch_url(info, lay, old_name)
    target = layout_mod.branch_url(info, lay, new_name)
    ctx.svn.run(
        "move",
        source,
        target,
        "-m",
        "Rename branch %s to %s" % (old_name, new_name),
        mutating=True,
    )
    _after_server_commit(ctx)
    if old_name == ctx.branch:
        ctx.svn.run("switch", target, str(ctx.wc_root), mutating=True)
    ctx.echo("Renamed branch %s to %s" % (old_name, new_name))
    return 0


def delete_remote_ref(ctx: "Context", positionals: List[str]) -> int:
    """`git push --delete <branch>`; Subversion has only remote branches."""
    names = [p for p in positionals if p not in ("origin",)]
    if not names:
        raise UsageError("git push --delete <branch>")
    for name in names:
        _delete_branch(ctx, name.replace("origin/", ""), force=True)
    return 0


# ----------------------------------------------------------------------
# checkout / switch
# ----------------------------------------------------------------------
def cmd_checkout(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=[
            "b",
            "B",
            "force",
            "f",
            "quiet",
            "q",
            "detach",
            "track",
            "t",
            "orphan",
            "merge",
            "m",
        ],
    )
    refuse(
        "checkout",
        opts,
        {
            "orphan": "a branch with no history would be an empty directory; make "
            "one with `svn mkdir ^/branches/<name> -m ...` if you really "
            "want it.",
        },
    )
    no_effect(
        ctx,
        "checkout",
        opts,
        {
            "track": "Subversion has a single remote, so there is no upstream to set.",
            "t": "Subversion has a single remote, so there is no upstream to set.",
            "merge": "`svn switch` always merges your local changes; that is the only behaviour.",
            "m": "`svn switch` always merges your local changes; that is the only behaviour.",
        },
    )
    positionals = list(opts.positionals)
    quiet = opts.has("quiet", "q")

    if opts.has("detach"):
        return _detach(ctx, positionals, quiet)

    # `git checkout -- <paths>` and `git checkout <paths>` restore files.
    if opts.after_dashdash:
        from .workspace import cmd_restore

        return cmd_restore(ctx, opts.after_dashdash)

    if opts.has("b", "B"):
        # -b takes the new branch name as its value in git, but our parser
        # sees it as a flag followed by a positional.
        if not positionals:
            raise UsageError("git checkout -b <new-branch> [<start-point>]")
        name = positionals[0]
        start_point = positionals[1] if len(positionals) > 1 else None
        url = _create_branch(ctx, name, start_point, quiet=quiet)
        ctx.svn.run("switch", url, str(ctx.wc_root), mutating=True, capture=False)
        if not quiet:
            ctx.echo("Switched to a new branch '%s'" % name)
        return 0

    if not positionals:
        raise UsageError("git checkout <branch> | git checkout -- <file>...")

    name = positionals[0]
    if _looks_like_path(ctx, name):
        from .workspace import cmd_restore

        return cmd_restore(ctx, positionals)

    return _switch_to(ctx, name, force=opts.has("force", "f"), quiet=quiet)


def _detach(ctx: "Context", positionals: List[str], quiet: bool = False) -> int:
    """`git checkout --detach <rev>`: sit at a revision rather than a branch.

    The Subversion equivalent is an out-of-date working copy, which is exactly
    what a detached HEAD is: no branch moves when you commit from here.
    """
    spec = positionals[0] if positionals else "HEAD"
    revision = rev_mod.resolve(ctx, spec, str(ctx.wc_root))
    ctx.svn.run(
        "update", "-r", str(revision), str(ctx.wc_root), mutating=True, capture=False
    )
    if not quiet:
        ctx.echo("HEAD is now at %s" % formatting.revision_id(revision))
        ctx.note(
            "the working copy is now at an older revision; `git pull` returns "
            "it to the tip of %s" % ctx.branch
        )
    return 0


def cmd_switch(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["create", "c", "force", "f", "quiet", "q", "detach"],
        values=["start-point"],
    )
    quiet = opts.has("quiet", "q")
    if opts.has("detach"):
        return _detach(ctx, opts.positionals, quiet)
    if not opts.positionals:
        raise UsageError("git switch <branch>")
    name = opts.positionals[0]
    if opts.has("create", "c"):
        url = _create_branch(ctx, name, opts.first("start-point"), quiet=quiet)
        ctx.svn.run("switch", url, str(ctx.wc_root), mutating=True, capture=False)
        if not quiet:
            ctx.echo("Switched to a new branch '%s'" % name)
        return 0
    return _switch_to(ctx, name, force=opts.has("force", "f"), quiet=quiet)


def _switch_to(
    ctx: "Context", name: str, force: bool = False, quiet: bool = False
) -> int:
    info, lay = ctx.info, ctx.layout
    _require_empty_queue(ctx, "switching branches")

    short = name[len("origin/") :] if name.startswith("origin/") else name
    if short.startswith("tags/"):
        url = layout_mod.tag_url(info, lay, short[len("tags/") :])
    else:
        url = layout_mod.branch_url(info, lay, short)

    if not ctx.svn.path_exists(url):
        raise SvnGitError(
            "pathspec '%s' did not match any branch known to Subversion.\n"
            'Use "git branch" to list branches.' % name
        )
    if force:
        ctx.svn.run("revert", "-R", str(ctx.wc_root), mutating=True)

    ctx.svn.run(
        "switch",
        url,
        str(ctx.wc_root),
        "--accept",
        "postpone",
        mutating=True,
        capture=False,
    )
    ctx.state.clear_index()
    ctx.state.save()
    if not quiet:
        ctx.echo("Switched to branch '%s'" % short)
    return 0


def _looks_like_path(ctx: "Context", name: str) -> bool:
    return (ctx.cwd / name).exists()


# ----------------------------------------------------------------------
# merge
# ----------------------------------------------------------------------
def cmd_merge(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["no-commit", "n", "abort", "squash", "ff", "no-ff", "quiet", "q"],
        values=["message", "m", "strategy", "s"],
    )
    refuse(
        "merge",
        opts,
        {
            "strategy": "Subversion has one merge algorithm; there is no strategy to pick.",
            "s": "Subversion has one merge algorithm; there is no strategy to pick.",
        },
    )
    no_effect(
        ctx,
        "merge",
        opts,
        {
            "ff": "every Subversion merge is a real merge; there is no fast-forward.",
            "no-ff": "every Subversion merge is a real merge; there is no fast-forward.",
        },
    )
    if opts.has("abort"):
        ctx.svn.run("revert", "-R", str(ctx.wc_root), mutating=True)
        ctx.echo("Merge aborted; working copy reverted.")
        return 0

    if not opts.positionals:
        raise UsageError("git merge <branch>")
    name = opts.positionals[0]

    _require_empty_queue(ctx, "a merge")
    info, lay = ctx.info, ctx.layout
    short = name[len("origin/") :] if name.startswith("origin/") else name
    url = layout_mod.branch_url(info, lay, short)
    if not ctx.svn.path_exists(url):
        raise SvnGitError("merge: %s - not something we can merge" % name)

    result = ctx.svn.run(
        "merge", url, str(ctx.wc_root), "--accept", "postpone", mutating=True
    )
    if result.stdout.strip():
        ctx.echo(result.stdout.rstrip())

    conflicts = [e for e in status_mod.compute(ctx).entries if e.unmerged]
    if conflicts:
        ctx.echo("Automatic merge failed; fix conflicts and then commit the result.")
        for entry in conflicts:
            ctx.echo("\tboth modified: %s" % ctx.display_path(entry.path))
        return 1

    message = str(opts.first("message", "m", default="Merge branch '%s'" % short))

    if opts.has("squash"):
        # git --squash takes the changes without recording a merge. svn records
        # the merge in svn:mergeinfo on the root, so reverting just that
        # property leaves the file changes behind as ordinary local edits.
        ctx.svn.run("revert", "--depth", "empty", str(ctx.wc_root), mutating=True)
        ctx.echo("Squash commit -- not updating HEAD")
        ctx.echo("Automatic merge went well; stopped before committing as requested")
        return 0

    if opts.has("no-commit", "n"):
        ctx.echo("Automatic merge went well; stopped before committing as requested")
        return 0

    return _commit_merge(ctx, message, quiet=opts.has("quiet", "q"))


def _commit_merge(ctx: "Context", message: str, quiet: bool = False) -> int:
    """Commit a merge straight to Subversion, root included.

    The root has to be in the commit: `svn merge` records svn:mergeinfo there,
    and without it Subversion will happily re-merge the same revisions later.
    """
    result = ctx.svn.run("commit", "-m", message, str(ctx.wc_root), mutating=True)
    from .sync import _parse_committed_revision, _refresh_base_revision

    revision = _parse_committed_revision(result.stdout)
    _refresh_base_revision(ctx)
    ctx.state.clear_index()
    ctx.state.save()
    if quiet:
        return 0
    if revision:
        ctx.echo("Committed as %s" % formatting.revision_id(revision))
    elif result.stdout.strip():
        ctx.echo(result.stdout.strip())
    return 0


# ----------------------------------------------------------------------
# cherry-pick / revert
# ----------------------------------------------------------------------
def cmd_cherry_pick(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["no-commit", "n", "abort", "continue", "x", "quiet", "q"],
        values=["mainline", "m"],
    )
    refuse(
        "cherry-pick",
        opts,
        {
            "continue": "there is no cherry-pick in progress to resume. Resolve the "
            "conflicts, `git add` them, then `git commit`.",
            "mainline": "Subversion revisions have no second parent to pick a side from.",
            "m": "Subversion revisions have no second parent to pick a side from.",
        },
    )
    if opts.has("abort"):
        ctx.svn.run("revert", "-R", str(ctx.wc_root), mutating=True)
        ctx.echo("cherry-pick aborted.")
        return 0
    if not opts.positionals:
        raise UsageError("git cherry-pick <revision>")

    _require_empty_queue(ctx, "a cherry-pick")
    revision = rev_mod.resolve(ctx, opts.positionals[0], str(ctx.wc_root))
    source = _source_url_for_revision(ctx, revision)

    ctx.svn.run(
        "merge",
        "-c",
        str(revision),
        source,
        str(ctx.wc_root),
        "--accept",
        "postpone",
        mutating=True,
    )
    conflicts = [e for e in status_mod.compute(ctx).entries if e.unmerged]
    if conflicts:
        ctx.echo("error: could not apply %s" % formatting.revision_id(revision))
        for entry in conflicts:
            ctx.echo("\tboth modified: %s" % ctx.display_path(entry.path))
        return 1

    if opts.has("no-commit", "n"):
        if not opts.has("quiet", "q"):
            ctx.echo(
                "Applied %s to the working copy." % formatting.revision_id(revision)
            )
        return 0

    entries = ctx.svn.log(source, revision=str(revision))
    original = entries[0].message.strip() if entries else ""
    message = original or "Cherry-pick %s" % formatting.revision_id(revision)
    if opts.has("x"):
        message += "\n\n(cherry picked from %s)" % formatting.revision_id(revision)
    return _commit_merge(ctx, message, quiet=opts.has("quiet", "q"))


def cmd_revert(ctx: "Context", argv: List[str]) -> int:
    """git revert -- undo a revision with a new one.

    Note this is *not* `svn revert`, which throws away local edits; that is
    `git restore` / `git checkout --`.
    """
    # --no-edit is the default: svngit never opens an editor for a revert.
    opts = parse(
        argv,
        flags=["no-commit", "n", "no-edit", "abort", "quiet", "q"],
        values=["message", "m"],
    )
    if opts.has("abort"):
        ctx.svn.run("revert", "-R", str(ctx.wc_root), mutating=True)
        ctx.echo("revert aborted.")
        return 0
    if not opts.positionals:
        raise UsageError("git revert <revision>")

    _require_empty_queue(ctx, "a revert")
    revision = rev_mod.resolve(ctx, opts.positionals[0], str(ctx.wc_root))
    source = _source_url_for_revision(ctx, revision)

    ctx.svn.run(
        "merge",
        "-c",
        "-%d" % revision,
        source,
        str(ctx.wc_root),
        "--accept",
        "postpone",
        mutating=True,
    )
    conflicts = [e for e in status_mod.compute(ctx).entries if e.unmerged]
    if conflicts:
        ctx.echo("error: could not revert %s" % formatting.revision_id(revision))
        return 1

    if opts.has("no-commit", "n"):
        return 0
    entries = ctx.svn.log(source, revision=str(revision))
    subject = (
        entries[0].message.strip().splitlines()[0]
        if entries and entries[0].message.strip()
        else ""
    )
    message = str(
        opts.first(
            "message",
            "m",
            default='Revert "%s"\n\nThis reverts %s.'
            % (subject, formatting.revision_id(revision)),
        )
    )
    return _commit_merge(ctx, message, quiet=opts.has("quiet", "q"))


def _source_url_for_revision(ctx: "Context", revision: int) -> str:
    """Which branch URL a revision's changes live on.

    Cherry-pick needs a merge source, and svn only takes a URL. Reading the
    revision's own paths is more reliable than assuming the current branch.
    """
    info, lay = ctx.info, ctx.layout
    entries = ctx.svn.log(info.repos_root, revision=str(revision), verbose=True)
    if entries and entries[0].paths:
        guess = layout_mod.guess_branch_for_paths(
            lay, [p.path for p in entries[0].paths]
        )
        if guess:
            return "%s/%s" % (info.repos_root.rstrip("/"), guess)
    return info.url


# ----------------------------------------------------------------------
# tag
# ----------------------------------------------------------------------
def _tag_contains(
    ctx: "Context", info: SvnInfo, lay: layout_mod.Layout, name: str, revision: int
) -> bool:
    """Whether a tag was cut from a revision at or after `revision`.

    A Subversion tag is a directory copy, so the revision it was copied from
    is what its history includes.
    """
    url = layout_mod.tag_url(info, lay, name)
    entries = ctx.svn.log(url, limit=1, stop_on_copy=True, verbose=True)
    if not entries:
        return False
    for path in entries[0].paths:
        if path.copyfrom_rev is not None:
            return path.copyfrom_rev >= revision
    return entries[0].revision >= revision


def cmd_tag(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["annotate", "a", "list", "l", "force", "f", "sign", "s", "n"],
        values=["message", "m", "delete", "d", "contains"],
    )
    refuse(
        "tag",
        opts,
        {
            "sign": "Subversion has no signed tags; a tag is a directory copy.",
            "s": "Subversion has no signed tags; a tag is a directory copy.",
        },
    )
    # -a/--annotate is the default: every Subversion tag is a copy made with a
    # log message, so there is no lightweight form to contrast it with.
    info, lay = ctx.info, ctx.layout

    delete = opts.first("delete", "d")
    if delete:
        url = layout_mod.tag_url(info, lay, str(delete))
        if not ctx.svn.path_exists(url):
            raise SvnGitError("tag '%s' not found" % delete)
        ctx.svn.run("delete", url, "-m", "Delete tag %s" % delete, mutating=True)
        _after_server_commit(ctx)
        ctx.echo("Deleted tag '%s'" % delete)
        return 0

    if not opts.positionals or opts.has("list", "l") or opts.has("contains"):
        pattern = opts.positionals[0] if opts.positionals else None
        floor = None
        if opts.has("contains"):
            floor = rev_mod.resolve(ctx, str(opts.get("contains")), str(ctx.wc_root))
        for name in layout_mod.list_tags(ctx.svn, info, lay):
            if pattern is not None and not fnmatch.fnmatch(name, pattern):
                continue
            if floor is not None and not _tag_contains(ctx, info, lay, name, floor):
                continue
            ctx.echo(name)
        return 0

    name = opts.positionals[0]
    url = layout_mod.tag_url(info, lay, name)
    if ctx.svn.path_exists(url) and not opts.has("force", "f"):
        raise SvnGitError("tag '%s' already exists" % name)

    source = info.url
    if len(opts.positionals) > 1:
        revision = rev_mod.resolve(ctx, opts.positionals[1], str(ctx.wc_root))
        source = "%s@%d" % (info.url, revision)

    message = str(opts.first("message", "m", default="Create tag %s" % name))
    args = ["copy", source, url, "-m", message]
    ctx.svn.run(*args, mutating=True)
    _after_server_commit(ctx)
    ctx.note("tags in Subversion are directories, so '%s' is now on the server" % name)
    return 0
