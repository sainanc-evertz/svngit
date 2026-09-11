"""clone, init, status, add, rm, mv, reset, restore, clean."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional

from .. import formatting, status as status_mod
from ..cliargs import no_effect, parse, refuse
from ..errors import UsageError, SvnGitError
from ..state import ADD, DELETE, MODIFY


# ----------------------------------------------------------------------
# clone
# ----------------------------------------------------------------------
def cmd_clone(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["bare", "quiet", "q", "no-checkout", "n", "full"],
        values=["branch", "b", "depth", "revision", "r"],
    )
    refuse("clone", opts, {
        "bare": "a Subversion checkout is always a working copy. To inspect a "
                "repository without one, use the URL directly: `svn log <url>`.",
        "no-checkout": "a Subversion checkout is the only thing `clone` produces; "
                       "there is no separate history to fetch first.",
        "n": "a Subversion checkout is the only thing `clone` produces; "
             "there is no separate history to fetch first.",
    })
    if not opts.positionals:
        raise UsageError("git clone <repository-url> [<directory>]")

    url = opts.positionals[0].rstrip("/")
    destination = opts.positionals[1] if len(opts.positionals) > 1 else None

    checkout_url = url
    branch = opts.first("branch", "b")

    # `git clone` gives you the default branch, not every branch. The
    # equivalent is trunk, so look for a layout and descend into it rather
    # than dragging down branches/ and tags/ as well.
    if not opts.has("full"):
        # No working copy exists yet, so there is no per-checkout config to
        # read; the layout name can only come from the environment.
        trunk = os.environ.get("SVNGIT_TRUNK", "trunk")
        if branch:
            candidate = "%s/branches/%s" % (url, branch)
            if ctx.svn.path_exists(candidate):
                checkout_url = candidate
            elif ctx.svn.path_exists("%s/%s" % (url, branch)):
                checkout_url = "%s/%s" % (url, branch)
            else:
                raise SvnGitError("branch '%s' not found under %s" % (branch, url))
        elif ctx.svn.path_exists("%s/%s" % (url, trunk)):
            checkout_url = "%s/%s" % (url, trunk)
            ctx.note(
                "checking out %s/%s (use --full to check out the whole repository)"
                % (url, trunk)
            )

    if destination is None:
        destination = _clone_destination(url, checkout_url)

    args = ["checkout", checkout_url, destination]
    revision = opts.first("revision", "r")
    if revision:
        args.extend(["-r", str(revision).lstrip("r")])
    if opts.has("depth"):
        # git --depth is a commit-history limit; svn --depth is a tree limit.
        ctx.note(
            "git's --depth limits history, which Subversion working copies do "
            "not store; ignoring it (use --revision to check out an older tree)"
        )

    if not opts.has("quiet", "q"):
        ctx.echo("Cloning into '%s'..." % destination)
    ctx.svn.run(*args, mutating=True, capture=False)
    if not opts.has("quiet", "q") and not ctx.dry_run:
        ctx.echo("done.")
    return 0


def _clone_destination(url: str, checkout_url: str) -> str:
    name = os.path.basename(checkout_url.rstrip("/"))
    if name in ("trunk", "tags", "branches"):
        name = os.path.basename(url.rstrip("/"))
    return name or "svn-checkout"


# ----------------------------------------------------------------------
# init
# ----------------------------------------------------------------------
def cmd_init(ctx, argv: List[str]) -> int:
    opts = parse(argv, flags=["standalone", "bare", "quiet", "q"], values=["initial-branch", "b"])
    refuse("init", opts, {
        "bare": "`svnadmin create` already makes a server-side repository with "
                "no working copy; --standalone then checks one out for you.",
    })
    no_effect(ctx, "init", opts, {
        "initial-branch": "the standard Subversion layout names it trunk.",
        "b": "the standard Subversion layout names it trunk.",
    })
    directory = Path(opts.positionals[0]).expanduser() if opts.positionals else ctx.cwd

    if not opts.has("standalone"):
        ctx.warn(
            "git init has no direct Subversion equivalent: a Subversion working\n"
            "copy is always a checkout of a server-side repository.\n"
            "\n"
            "To create a repository and check it out here, run:\n"
            "    git init --standalone %s\n"
            "\n"
            "To work with an existing repository:\n"
            "    git clone <url>" % (opts.positionals[0] if opts.positionals else ".")
        )
        return 1

    repo_path = (directory / ".svnrepo").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if repo_path.exists():
        raise SvnGitError("a repository already exists at %s" % repo_path)

    svnadmin = os.environ.get("SVNGIT_SVNADMIN", "svnadmin")
    if shutil.which(svnadmin) is None:
        raise SvnGitError("could not find '%s' on PATH; it is required by --standalone" % svnadmin)

    import subprocess

    subprocess.run([svnadmin, "create", str(repo_path)], check=True)
    repo_url = repo_path.as_uri()
    ctx.svn.run(
        "mkdir", "-m", "Create standard layout",
        "%s/trunk" % repo_url, "%s/branches" % repo_url, "%s/tags" % repo_url,
        mutating=True,
    )
    ctx.svn.run("checkout", "%s/trunk" % repo_url, str(directory), mutating=True)
    ctx.echo("Initialized Subversion repository in %s" % repo_path)
    ctx.echo("Checked out trunk into %s" % directory)
    return 0


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------
def cmd_status(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["short", "s", "porcelain", "branch", "b", "long", "ignored", "verbose", "v"],
        values=["untracked-files", "u"],
    )
    # --long is the default format; -v adds nothing svngit can show.
    paths = ctx.to_wc_paths(opts.paths) if opts.paths else None
    report = status_mod.compute(ctx, paths, include_ignored=opts.has("ignored"))

    mode = str(opts.first("untracked-files", "u", default="normal"))
    if mode == "no":
        report.entries = [e for e in report.entries if not e.untracked]
    elif mode not in ("normal", "all"):
        raise UsageError("invalid untracked files mode '%s' (no, normal, all)" % mode)

    if opts.has("porcelain") or opts.has("short", "s"):
        if opts.has("branch", "b"):
            ctx.echo("## %s...origin/%s" % (ctx.branch, ctx.branch))
        for line in formatting.format_porcelain(report):
            ctx.echo(line)
        return 0

    behind = _incoming_count(ctx)
    lines = formatting.format_long(
        report,
        branch=ctx.branch,
        revision=ctx.info.revision,
        behind=behind,
        pending=ctx.state.commits,
        display=ctx.display_path,
    )
    for line in lines:
        ctx.echo(line)
    return 0


def _incoming_count(ctx) -> int:
    """How many server revisions we do not have yet.

    Off by default: `git status` is a local, instant command in git, and
    Subversion can only answer this by contacting the server. Turn it on with
    `git config svngit.checkupstream true`.
    """
    if ctx.state.get_config("svngit.checkupstream") != "true":
        return 0
    try:
        head = ctx.svn.info(str(ctx.wc_root), revision="HEAD").revision
    except Exception:
        return 0  # offline is not a reason for `git status` to fail
    return max(0, head - ctx.info.revision)


# ----------------------------------------------------------------------
# add
# ----------------------------------------------------------------------
def cmd_add(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["all", "A", "update", "u", "force", "f", "verbose", "v", "dry-run", "n", "patch", "p", "edit", "e", "intent-to-add", "N"],
    )
    intent_only = opts.has("intent-to-add", "N")
    stage_all = opts.has("all", "A")
    tracked_only = opts.has("update", "u")
    targets = opts.paths

    if opts.has("patch", "p") and opts.has("edit", "e"):
        raise UsageError("-p and -e cannot be combined; -p picks hunks, -e edits the whole patch")

    if not targets and not (stage_all or tracked_only or opts.has("patch", "p") or opts.has("edit", "e")):
        raise UsageError("nothing specified, nothing added.\nhint: maybe you wanted 'git add .'?")

    if targets == ["."] or targets == [":/"]:
        stage_all = True
        targets = []

    scope = ctx.to_wc_paths(targets) if targets else None
    report = status_mod.compute(ctx, scope)

    if opts.has("patch", "p") or opts.has("edit", "e"):
        from .interactive import stage_edit, stage_patch

        # Untracked files have no diff to pick from, so only mention them when
        # the user named one explicitly.
        chosen = report.entries if targets else [e for e in report.entries if not e.untracked]
        if opts.has("edit", "e"):
            return stage_edit(ctx, chosen)
        return stage_patch(ctx, chosen)

    staged_count = 0

    for entry in report.entries:
        if entry.ignored:
            continue
        if entry.untracked and (tracked_only or (not stage_all and scope is None)):
            continue
        if _stage_one(
            ctx,
            entry,
            dry_run=opts.has("dry-run", "n"),
            verbose=opts.has("verbose", "v"),
            intent_only=intent_only,
        ):
            staged_count += 1

    if not opts.has("dry-run", "n"):
        ctx.state.save()
    if opts.has("verbose", "v") and staged_count == 0:
        ctx.echo("nothing to add")
    return 0


def _stage_one(ctx, entry, dry_run: bool = False, verbose: bool = False,
               intent_only: bool = False) -> bool:
    """Stage a single path, running whatever svn scheduling it needs."""
    abs_path = ctx.abs_path(entry.path)

    if entry.untracked:
        if verbose or dry_run:
            ctx.echo("add '%s'" % ctx.display_path(entry.path))
        if dry_run:
            return True
        ctx.svn.run("add", "--parents", ctx.svn_target(entry.path), mutating=True)
        if intent_only:
            # git -N records the path with empty content, so the file shows as
            # AM and `git add -p` can pick hunks out of a brand new file.
            ctx.state.stage(
                entry.path, ADD, ctx.state.objects.write(b""),
                _is_executable(abs_path), intent=True,
            )
        else:
            ctx.state.stage(entry.path, ADD, ctx.snapshot(entry.path), _is_executable(abs_path))
        return True

    if entry.worktree == DELETE:
        # The file is gone from disk; `git add` on it stages the removal.
        if dry_run:
            ctx.echo("remove '%s'" % ctx.display_path(entry.path))
            return True
        ctx.svn.run("delete", "--force", ctx.svn_target(entry.path), mutating=True)
        ctx.state.stage(entry.path, DELETE)
        return True

    if entry.unmerged:
        if dry_run:
            return True
        ctx.svn.run("resolve", "--accept", "working", ctx.svn_target(entry.path), mutating=True)
        ctx.state.stage(entry.path, MODIFY, ctx.snapshot(entry.path), _is_executable(abs_path))
        return True

    if entry.index == ADD or entry.index == "R":
        if dry_run:
            return True
        ctx.state.stage(entry.path, entry.index, ctx.snapshot(entry.path), _is_executable(abs_path))
        return True

    if entry.index == DELETE:
        if dry_run:
            return True
        ctx.state.stage(entry.path, DELETE)
        return True

    if entry.worktree == "M" or entry.index == MODIFY:
        if dry_run:
            ctx.echo("add '%s'" % ctx.display_path(entry.path))
            return True
        ctx.state.stage(entry.path, MODIFY, ctx.snapshot(entry.path), _is_executable(abs_path))
        return True

    return False


def _is_executable(path: Path) -> bool:
    try:
        return bool(path.stat().st_mode & 0o111)
    except OSError:
        return False


# ----------------------------------------------------------------------
# rm / mv
# ----------------------------------------------------------------------
def cmd_rm(ctx, argv: List[str]) -> int:
    opts = parse(argv, flags=["r", "cached", "force", "f", "dry-run", "n", "quiet", "q"])
    if not opts.paths:
        raise UsageError("git rm <file>...")

    for user_path in opts.paths:
        wc_path = ctx.to_wc_path(user_path)
        target = ctx.svn_target(wc_path)
        if opts.has("cached"):
            # Stop tracking, keep the file on disk.
            ctx.svn.run("delete", "--keep-local", target, mutating=True)
        else:
            args = ["delete", target]
            if opts.has("force", "f"):
                args.append("--force")
            ctx.svn.run(*args, mutating=True)
        ctx.state.stage(wc_path, DELETE)
        if not opts.has("quiet", "q"):
            ctx.echo("rm '%s'" % ctx.display_path(wc_path))
    ctx.state.save()
    return 0


def cmd_mv(ctx, argv: List[str]) -> int:
    opts = parse(argv, flags=["force", "f", "verbose", "v", "dry-run", "n", "k"])
    no_effect(ctx, "mv", opts, {
        "k": "svngit stops on the first error rather than skipping it.",
    })
    if len(opts.paths) < 2:
        raise UsageError("git mv <source>... <destination>")

    *sources, destination = opts.paths
    dest_wc = ctx.to_wc_path(destination)
    args = ["move"]
    if opts.has("force", "f"):
        args.append("--force")
    args.extend(ctx.svn_target(ctx.to_wc_path(s)) for s in sources)
    args.append(ctx.svn_target(dest_wc))
    ctx.svn.run(*args, mutating=True)

    for source in sources:
        source_wc = ctx.to_wc_path(source)
        ctx.state.stage(source_wc, DELETE)
        moved_to = dest_wc if len(sources) == 1 else "%s/%s" % (dest_wc, os.path.basename(source_wc))
        ctx.state.stage(moved_to, ADD, ctx.snapshot(moved_to))
        if opts.has("verbose", "v"):
            ctx.echo("Renaming %s to %s" % (source, moved_to))
    ctx.state.save()
    return 0


# ----------------------------------------------------------------------
# reset / restore
# ----------------------------------------------------------------------
def cmd_reset(ctx, argv: List[str]) -> int:
    opts = parse(argv, flags=["soft", "mixed", "hard", "keep", "merge", "quiet", "q"])
    refuse("reset", opts, {
        "keep": "resetting to another revision while keeping local changes needs "
                "history rewriting. Use `git stash`, `git reset --hard <rev>`, "
                "then `git stash pop`.",
        "merge": "resetting to another revision while keeping local changes needs "
                 "history rewriting. Use `git stash`, `git reset --hard <rev>`, "
                 "then `git stash pop`.",
    })
    # --mixed is the default and needs no handling.
    from ..cliargs import split_revisions_and_paths

    revisions, paths = split_revisions_and_paths(ctx, opts.positionals)
    if opts.after_dashdash:
        paths.extend(opts.after_dashdash)

    if opts.has("hard"):
        return _reset_hard(ctx, revisions[0] if revisions else None)

    if opts.has("soft"):
        if revisions:
            raise UsageError(
                "git reset --soft <commit> would rewrite history, which "
                "Subversion cannot do. Without a commit it un-commits the last "
                "local (unpushed) commit."
            )
        return _reset_soft(ctx)

    # --mixed (the default): unstage.
    return _unstage(ctx, paths)


def _reset_soft(ctx) -> int:
    commit = ctx.state.pop_commit()
    if commit is None:
        raise SvnGitError(
            "no local commits to undo. Revisions already pushed to Subversion "
            "cannot be un-committed; use `git revert <rev>` instead."
        )
    for change in commit.changes:
        ctx.state.stage(change.path, change.action, change.blob, change.executable)
    ctx.state.save()
    ctx.echo("Uncommitted %s %s" % (commit.short_id, commit.summary))
    return 0


def _reset_hard(ctx, revision: Optional[str]) -> int:
    from .. import revisions as rev_mod

    if revision:
        number = rev_mod.resolve(ctx, revision, str(ctx.wc_root))
        ctx.svn.run("revert", "-R", str(ctx.wc_root), mutating=True)
        ctx.svn.run("update", "-r", str(number), str(ctx.wc_root), mutating=True, capture=False)
        ctx.echo("HEAD is now at %s" % formatting.revision_id(number))
    else:
        ctx.svn.run("revert", "-R", str(ctx.wc_root), mutating=True)
        ctx.echo("HEAD is now at %s" % formatting.revision_id(ctx.info.revision))
    ctx.state.clear_index()
    ctx.state.save()
    return 0


def _unstage(ctx, paths: List[str]) -> int:
    index = ctx.state.index
    wanted = ctx.to_wc_paths(paths) if paths else list(index)
    changed = 0
    for wc_path in wanted:
        entry = index.get(wc_path)
        if entry is None:
            continue
        if entry.action in (ADD, DELETE):
            # Adds and deletes live in svn's scheduling, not just our index, so
            # unstaging has to unschedule them too. `svn revert` on a scheduled
            # add leaves the file in place as unversioned -- exactly what
            # `git reset` does.
            ctx.svn.run("revert", ctx.svn_target(wc_path), mutating=True)
        ctx.state.unstage(wc_path)
        changed += 1
    ctx.state.save()
    if changed:
        report = status_mod.compute(ctx)
        unstaged = [e for e in report.unstaged]
        if unstaged:
            ctx.echo("Unstaged changes after reset:")
            for entry in unstaged:
                ctx.echo("%s\t%s" % (entry.worktree, ctx.display_path(entry.path)))
    return 0


def cmd_restore(ctx, argv: List[str]) -> int:
    opts = parse(argv, flags=["staged", "S", "worktree", "W", "quiet", "q"], values=["source", "s"])
    if not opts.paths:
        raise UsageError("git restore <file>...")

    staged = opts.has("staged", "S")
    worktree = opts.has("worktree", "W") or not staged

    if staged:
        _unstage(ctx, opts.paths)
    if worktree:
        source = opts.first("source", "s")
        for user_path in opts.paths:
            wc_path = ctx.to_wc_path(user_path)
            if source:
                from .. import revisions as rev_mod

                number = rev_mod.resolve(ctx, source, ctx.svn_target(wc_path))
                ctx.svn.run("update", "-r", str(number), ctx.svn_target(wc_path), mutating=True)
            else:
                ctx.svn.run("revert", "-R", ctx.svn_target(wc_path), mutating=True)
            ctx.state.unstage(wc_path)
        ctx.state.save()
    return 0


# ----------------------------------------------------------------------
# clean
# ----------------------------------------------------------------------
def cmd_clean(ctx, argv: List[str]) -> int:
    opts = parse(argv, flags=["force", "f", "d", "n", "dry-run", "x", "quiet", "q", "i", "interactive"])
    if opts.has("i", "interactive"):
        raise UsageError("git clean -i is not supported; use -n to preview and -f to delete")
    dry_run = opts.has("n", "dry-run")
    if not opts.has("force", "f") and not dry_run:
        raise UsageError("clean requires -f to actually delete files (or -n to preview)")

    scope = ctx.to_wc_paths(opts.paths) if opts.paths else None
    report = status_mod.compute(ctx, scope, include_ignored=opts.has("x"))
    removed = 0
    for entry in report.entries:
        if not (entry.untracked or (opts.has("x") and entry.ignored)):
            continue
        target = ctx.abs_path(entry.path)
        if target.is_dir() and not opts.has("d"):
            continue
        label = "Would remove" if dry_run else "Removing"
        if not opts.has("quiet", "q"):
            ctx.echo("%s %s" % (label, ctx.display_path(entry.path) + ("/" if target.is_dir() else "")))
        if dry_run:
            continue
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            try:
                target.unlink()
            except OSError as exc:
                ctx.warn("failed to remove %s: %s" % (entry.path, exc))
                continue
        removed += 1
    return 0


# ----------------------------------------------------------------------
# sparse-checkout
# ----------------------------------------------------------------------
SPARSE_KEY = "svngit.sparse"


def cmd_sparse_checkout(ctx, argv: List[str]) -> int:
    """Subversion calls this sparse directories, set with `svn update --set-depth`.

    The mapping is direct: a path in the cone is checked out at full depth,
    everything beside it is excluded. The chosen set is remembered so that
    `list` can report it, since Subversion stores depth per directory rather
    than as a list.
    """
    subcommand = argv[0] if argv and not argv[0].startswith("-") else None
    rest = argv[1:] if subcommand else argv

    handlers = {
        "list": _sparse_list,
        "set": lambda c, a: _sparse_apply(c, a, replace=True),
        "add": lambda c, a: _sparse_apply(c, a, replace=False),
        "init": _sparse_init,
        "disable": _sparse_disable,
        "reapply": lambda c, a: _sparse_apply(c, [], replace=False),
    }
    handler = handlers.get(subcommand or "")
    if handler is None:
        raise UsageError(
            "git sparse-checkout (list | set <paths> | add <paths> | init | disable)"
        )
    return handler(ctx, rest)


def _sparse_paths(ctx) -> List[str]:
    stored = ctx.state.get_config(SPARSE_KEY, "")
    return [p for p in stored.split("\n") if p]


def _sparse_list(ctx, argv: List[str]) -> int:
    paths = _sparse_paths(ctx)
    if not paths:
        ctx.warn("this working copy is not sparse")
        return 1
    for path in paths:
        ctx.echo(path)
    return 0


def _sparse_init(ctx, argv: List[str]) -> int:
    opts = parse(argv, flags=["cone", "no-cone", "sparse-index"])
    refuse("sparse-checkout", opts, {
        "no-cone": "Subversion excludes whole directories, never individual "
                   "files by pattern, so only cone mode exists here.",
    })
    no_effect(ctx, "sparse-checkout", opts, {
        "sparse-index": "Subversion records depth per directory; there is no "
                        "index to shrink.",
    })
    # --cone is the only mode, so it needs no handling.
    ctx.state.set_config(SPARSE_KEY, "")
    ctx.state.save()
    ctx.echo(
        "Sparse checkout enabled. Choose directories with "
        "`git sparse-checkout set <path>...`."
    )
    return 0


def _sparse_apply(ctx, argv: List[str], replace: bool) -> int:
    opts = parse(argv, flags=["cone", "no-cone", "skip-checks", "stdin"])
    refuse("sparse-checkout", opts, {
        "no-cone": "Subversion excludes whole directories, never individual "
                   "files by pattern, so only cone mode exists here.",
    })
    no_effect(ctx, "sparse-checkout", opts, {
        "skip-checks": "the paths are handed to svn, which validates them itself.",
    })
    wanted = list(opts.paths)
    if opts.has("stdin"):
        import sys

        wanted.extend(line.strip() for line in sys.stdin if line.strip())

    keep = [] if replace else _sparse_paths(ctx)
    for entry in wanted:
        normalised = ctx.to_wc_path(entry).rstrip("/")
        if normalised and normalised not in keep:
            keep.append(normalised)
    if not keep:
        raise UsageError("git sparse-checkout set <path>...")

    # Everything at the top that was not asked for is excluded; the chosen
    # paths come back at full depth.
    top_level = {path.split("/")[0] for path in keep}
    for child in sorted(p.name for p in ctx.wc_root.iterdir() if p.name != ".svn"):
        if child in top_level:
            continue
        ctx.svn.run(
            "update", "--set-depth", "exclude", ctx.svn_target(child),
            check=False, mutating=True,
        )
    for path in keep:
        ctx.svn.run(
            "update", "--set-depth", "infinity", ctx.svn_target(path),
            check=False, mutating=True,
        )

    ctx.state.set_config(SPARSE_KEY, "\n".join(keep))
    ctx.state.save()
    return 0


def _sparse_disable(ctx, argv: List[str]) -> int:
    ctx.svn.run(
        "update", "--set-depth", "infinity", str(ctx.wc_root),
        mutating=True, capture=False,
    )
    ctx.state.unset_config(SPARSE_KEY)
    ctx.state.save()
    ctx.echo("Sparse checkout disabled; the full tree is back.")
    return 0
