"""commit, push, pull, fetch -- the commands where git and Subversion differ
most, because git commits locally and pushes later while Subversion commits
straight to the server.

svngit reconciles that with a local commit queue. `git commit` records the
staged content into a content-addressed store and queues it; `git push`
replays the queue as a series of `svn commit` calls. Users who prefer the
Subversion semantics can set `git config svngit.commitmode immediate`, which
makes `git commit` talk to the server directly.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from .. import formatting, status as status_mod
from ..cliargs import parse
from ..errors import SvnGitError, UsageError
from ..state import ADD, DELETE, Change, LocalCommit, make_commit_id, now
from ..status import hash_file

DEFERRED = "deferred"
IMMEDIATE = "immediate"


def commit_mode(ctx) -> str:
    mode = ctx.state.get_config("svngit.commitmode", DEFERRED)
    if mode not in (DEFERRED, IMMEDIATE):
        raise SvnGitError(
            "svngit.commitmode must be 'deferred' or 'immediate', got %r" % mode
        )
    return mode


# ----------------------------------------------------------------------
# commit
# ----------------------------------------------------------------------
def cmd_commit(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["all", "a", "amend", "quiet", "q", "verbose", "v", "allow-empty", "no-verify", "n", "s", "signoff"],
        values=["message", "m", "file", "F", "author", "reuse-message", "C"],
    )

    if opts.has("all", "a"):
        _stage_all_tracked(ctx)

    if opts.has("amend"):
        return _amend(ctx, opts)

    index = ctx.state.index
    if not index and not opts.has("allow-empty"):
        report = status_mod.compute(ctx)
        _report_nothing_to_commit(ctx, report)
        return 1

    message = _resolve_message(ctx, opts, index)
    if not message.strip() and not opts.has("allow-empty"):
        raise SvnGitError("aborting commit due to empty commit message")

    if commit_mode(ctx) == IMMEDIATE:
        return _commit_immediate(ctx, message, list(index), opts)
    return _commit_deferred(ctx, message, index, opts)


def _stage_all_tracked(ctx) -> None:
    """`git commit -a`: stage every tracked modification and deletion."""
    from .workspace import _stage_one

    report = status_mod.compute(ctx)
    for entry in report.entries:
        if entry.untracked or entry.ignored:
            continue
        _stage_one(ctx, entry)
    ctx.state.save()


def _report_nothing_to_commit(ctx, report) -> None:
    ctx.echo("On branch %s" % ctx.branch)
    if report.unstaged or report.untracked:
        ctx.echo('no changes added to commit (use "git add" and/or "git commit -a")')
    else:
        ctx.echo("nothing to commit, working tree clean")


def _resolve_message(ctx, opts, index) -> str:
    message = opts.first("message", "m")
    if message is not None:
        return str(message)
    file_arg = opts.first("file", "F")
    if file_arg:
        if file_arg == "-":
            import sys

            return sys.stdin.read()
        return Path(file_arg).expanduser().read_text(encoding="utf-8")
    return _edit_message(ctx, index)


def _edit_message(ctx, index) -> str:
    editor = (
        os.environ.get("GIT_EDITOR")
        or os.environ.get("SVN_EDITOR")
        or os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
    )
    if not editor:
        raise UsageError(
            "no commit message supplied and no editor configured.\n"
            "Use -m \"message\", or set $EDITOR."
        )
    template = [
        "",
        "# Please enter the commit message for your changes. Lines starting",
        "# with '#' will be ignored, and an empty message aborts the commit.",
        "#",
        "# On branch %s" % ctx.branch,
        "# Changes to be committed:",
    ]
    for path, entry in sorted(index.items()):
        template.append("#\t%s: %s" % (formatting.STATUS_WORDS.get(entry.action, "modified"), path))
    with tempfile.NamedTemporaryFile("w+", suffix=".COMMIT_EDITMSG", delete=False, encoding="utf-8") as handle:
        handle.write("\n".join(template) + "\n")
        temp_path = handle.name
    try:
        subprocess.run("%s %s" % (editor, temp_path), shell=True, check=True)
        raw = Path(temp_path).read_text(encoding="utf-8")
    finally:
        os.unlink(temp_path)
    return "\n".join(
        line for line in raw.splitlines() if not line.startswith("#")
    ).strip()


def _commit_deferred(ctx, message: str, index, opts) -> int:
    # `git add` already copied each staged file into the object store, so the
    # commit records what was staged rather than whatever is on disk now.
    changes = [
        Change(path=path, action=entry.action, blob=entry.blob, executable=entry.executable)
        for path, entry in sorted(index.items())
    ]

    timestamp = now()
    commit = LocalCommit(
        id=make_commit_id(message, changes, timestamp),
        message=message,
        author=_author(ctx, opts),
        timestamp=timestamp,
        base_revision=ctx.info.revision,
        changes=changes,
    )
    ctx.state.add_commit(commit)
    ctx.state.clear_index()
    ctx.state.save()

    if not opts.has("quiet", "q"):
        ctx.echo("[%s %s] %s" % (ctx.branch, commit.short_id, commit.summary))
        for line in _summarise(ctx, changes):
            ctx.echo(" %s" % line)
        ctx.echo('This commit is local. Run "git push" to send it to Subversion.')
    return 0


def _commit_immediate(ctx, message: str, paths: Sequence[str], opts) -> int:
    targets = _commit_targets(ctx, paths)
    result = ctx.svn.run("commit", "-m", message, *targets, mutating=True)
    ctx.state.clear_index()
    ctx.state.save()
    revision = _parse_committed_revision(result.stdout)
    _refresh_base_revision(ctx)
    if not opts.has("quiet", "q"):
        if revision:
            ctx.echo("[%s %s] %s" % (ctx.branch, formatting.revision_id(revision), message.splitlines()[0]))
        else:
            ctx.echo(result.stdout.strip() or "committed")
    return 0


def _author(ctx, opts) -> str:
    explicit = opts.first("author")
    if explicit:
        return str(explicit)
    configured = ctx.state.get_config("user.name")
    if configured:
        return configured
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _summarise(ctx, changes: Sequence[Change]) -> List[str]:
    """git's post-commit summary: file count, line counts, mode changes.

    Line counts come from `svn diff` against BASE, which reads the pristine
    copy in .svn and so costs nothing over the network.
    """
    insertions = deletions = 0
    targets = [ctx.svn_target(c.path) for c in changes]
    if targets:
        result = ctx.svn.run("diff", *targets, check=False)
        if result.ok:
            insertions, deletions = formatting.count_diff_lines(result.stdout)

    summary = "%d file%s changed" % (len(changes), "" if len(changes) == 1 else "s")
    if insertions:
        summary += ", %d insertion%s(+)" % (insertions, "" if insertions == 1 else "s")
    if deletions:
        summary += ", %d deletion%s(-)" % (deletions, "" if deletions == 1 else "s")

    lines = [summary]
    for change in changes:
        mode = "100755" if change.executable else "100644"
        if change.action == ADD:
            lines.append("create mode %s %s" % (mode, change.path))
        elif change.action == DELETE:
            lines.append("delete mode %s %s" % (mode, change.path))
    return lines


def _parse_committed_revision(output: str) -> Optional[int]:
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Committed revision"):
            digits = "".join(ch for ch in line if ch.isdigit())
            if digits:
                return int(digits)
    return None


def _amend(ctx, opts) -> int:
    commits = ctx.state.commits
    if commits:
        commit = commits[-1]
        index = ctx.state.index
        for path, entry in index.items():
            blob = entry.blob
            if entry.action != DELETE and ctx.abs_path(path).is_file():
                blob = blob or ctx.state.objects.write_file(ctx.abs_path(path))
            existing = next((c for c in commit.changes if c.path == path), None)
            if existing:
                existing.action = entry.action
                existing.blob = blob
            else:
                commit.changes.append(Change(path, entry.action, blob, entry.executable))
        message = opts.first("message", "m")
        if message is not None:
            commit.message = str(message)
        ctx.state.replace_commits(commits)
        ctx.state.clear_index()
        ctx.state.save()
        ctx.echo("[%s %s] %s (amended)" % (ctx.branch, commit.short_id, commit.summary))
        return 0

    # Nothing queued: the only thing left to amend is a message already on the
    # server, which svn stores as a revision property.
    message = opts.first("message", "m")
    if message is None:
        raise UsageError("git commit --amend needs -m when the commit is already pushed")
    revision = ctx.info.last_changed_rev or ctx.info.revision
    result = ctx.svn.run(
        "propset", "--revprop", "-r", str(revision), "svn:log", str(message),
        str(ctx.wc_root), check=False, mutating=True,
    )
    if not result.ok:
        raise SvnGitError(
            "could not amend r%d's message.\n"
            "Subversion only allows this when the server has the "
            "pre-revprop-change hook enabled, and the content of a pushed "
            "revision can never be amended -- use `git revert %s` and commit "
            "a correction instead.\n%s"
            % (revision, formatting.revision_id(revision), result.stderr.strip())
        )
    ctx.echo("Amended the log message of %s" % formatting.revision_id(revision))
    return 0


def _commit_targets(ctx, paths: Sequence[str]) -> List[str]:
    """Expand staged paths into svn commit targets.

    Subversion refuses to commit a file whose parent directory is itself
    newly scheduled for addition, so any such parents have to be committed
    alongside it.
    """
    wanted: Set[str] = set(paths)
    entries = {e.path: e for e in status_mod.compute(ctx).entries}
    for path in list(paths):
        parts = path.split("/")[:-1]
        for depth in range(len(parts), 0, -1):
            parent = "/".join(parts[:depth])
            entry = entries.get(parent)
            if entry is not None and entry.index == ADD:
                wanted.add(parent)
    ordered = sorted(wanted, key=lambda p: (p.count("/"), p))
    return [ctx.svn_target(p) for p in ordered]


# ----------------------------------------------------------------------
# push
# ----------------------------------------------------------------------
def cmd_push(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["force", "f", "quiet", "q", "verbose", "v", "dry-run", "n", "tags", "all", "set-upstream", "u", "delete", "d"],
        values=["repo"],
    )
    if opts.has("delete", "d"):
        from .branching import delete_remote_ref

        return delete_remote_ref(ctx, opts.positionals)

    commits = ctx.state.commits
    if not commits:
        if commit_mode(ctx) == IMMEDIATE:
            ctx.echo("Everything up-to-date (svngit.commitmode=immediate commits directly to Subversion)")
        else:
            ctx.echo("Everything up-to-date")
        return 0

    if opts.has("dry-run", "n"):
        ctx.echo("Would push %d commit%s to %s:" % (len(commits), "" if len(commits) == 1 else "s", ctx.info.url))
        for commit in commits:
            ctx.echo("  %s %s" % (commit.short_id, commit.summary))
        return 0

    _check_up_to_date(ctx, force=opts.has("force", "f"))
    return _replay(ctx, commits, quiet=opts.has("quiet", "q"))


def _check_up_to_date(ctx, force: bool) -> None:
    try:
        head = ctx.svn.info(str(ctx.wc_root), revision="HEAD").revision
    except Exception:
        return
    if head > ctx.info.revision and not force:
        raise SvnGitError(
            "Updates were rejected because the remote contains work you do not\n"
            "have locally (server is at r%d, you are at r%d).\n"
            "Run \"git pull\" to integrate the remote changes, then push again."
            % (head, ctx.info.revision)
        )


def _replay(ctx, commits: List[LocalCommit], quiet: bool = False) -> int:
    """Push each queued commit as its own Subversion revision.

    Before touching anything we snapshot the current content of every path the
    queue mentions. Replaying rewinds those paths through each commit's
    content in turn, so without the snapshot any edit made after the last
    `git commit` would be silently overwritten.
    """
    objects = ctx.state.objects
    base_revision = ctx.info.revision
    touched = sorted({change.path for commit in commits for change in commit.changes})
    snapshot: Dict[str, Optional[str]] = {}
    for path in touched:
        absolute = ctx.abs_path(path)
        snapshot[path] = objects.write_file(absolute) if absolute.is_file() else None

    pushed = 0
    remaining = list(commits)
    last_revision: Optional[int] = None

    try:
        for commit in commits:
            _materialise(ctx, commit)
            targets = _commit_targets(ctx, [c.path for c in commit.changes])
            if not targets:
                remaining.pop(0)
                continue
            result = ctx.svn.run("commit", "-m", commit.message, *targets, mutating=True)
            revision = _parse_committed_revision(result.stdout)
            if revision:
                last_revision = revision
            pushed += 1
            # Persist after every revision: if the next one fails, the ones
            # already on the server must not be pushed twice.
            remaining.pop(0)
            ctx.state.replace_commits(remaining)
            ctx.state.save()
            if not quiet:
                ctx.echo(
                    "  %s -> %s  %s"
                    % (commit.short_id, formatting.revision_id(revision) if revision else "(committed)", commit.summary)
                )
    finally:
        _restore(ctx, snapshot)
        ctx.state.replace_commits(remaining)
        ctx.state.save()
        _refresh_base_revision(ctx)

    if not quiet:
        ctx.echo("To %s" % ctx.info.url)
        if last_revision:
            ctx.echo(
                "   %s..%s  %s -> %s"
                % (
                    formatting.revision_id(base_revision),
                    formatting.revision_id(last_revision),
                    ctx.branch,
                    ctx.branch,
                )
            )
        ctx.echo("Pushed %d commit%s." % (pushed, "" if pushed == 1 else "s"))
    return 0


def _refresh_base_revision(ctx) -> None:
    """Bring the working copy's BASE up to the revision we just created.

    `svn commit` bumps only the committed paths; the working copy root keeps
    its old revision. Left alone, that stale BASE makes `git log` miss the
    revision that was just pushed and makes the *next* push look like the
    server has moved ahead. `svn update` is the standard remedy and is
    content-neutral here, since a push only runs when we are already current.
    """
    result = ctx.svn.run("update", str(ctx.wc_root), "--accept", "postpone",
                         check=False, mutating=True)
    if result.ok:
        ctx._info = None  # force the next read to see the new revision


def _materialise(ctx, commit: LocalCommit) -> None:
    """Put the working copy into the state this commit recorded."""
    objects = ctx.state.objects
    known = {e.path: e for e in status_mod.compute(ctx).entries}
    for change in commit.changes:
        absolute = ctx.abs_path(change.path)
        if change.action == DELETE:
            entry = known.get(change.path)
            if entry is not None and entry.index == DELETE:
                continue  # already scheduled by `git rm`
            if absolute.exists() or entry is not None:
                ctx.svn.run("delete", "--force", ctx.svn_target(change.path), mutating=True, check=False)
            continue

        if change.blob is not None:
            absolute.parent.mkdir(parents=True, exist_ok=True)
            absolute.write_bytes(objects.read(change.blob))
            if change.executable:
                absolute.chmod(absolute.stat().st_mode | 0o111)

        entry = known.get(change.path)
        needs_add = entry is None or entry.untracked
        if change.action == ADD and needs_add:
            ctx.svn.run("add", "--parents", "--force", ctx.svn_target(change.path), mutating=True, check=False)


def _restore(ctx, snapshot: Dict[str, Optional[str]]) -> None:
    """Put back the working copy content captured before the replay."""
    objects = ctx.state.objects
    for path, blob in snapshot.items():
        absolute = ctx.abs_path(path)
        if blob is None:
            continue
        try:
            data = objects.read(blob)
        except KeyError:
            continue
        if absolute.is_file() and hash_file(absolute) == blob:
            continue
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(data)


# ----------------------------------------------------------------------
# pull / fetch
# ----------------------------------------------------------------------
def cmd_pull(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["rebase", "r", "no-rebase", "ff-only", "quiet", "q", "verbose", "v", "all", "prune", "p"],
        values=["strategy", "s", "depth"],
    )
    if opts.has("no-rebase"):
        ctx.note(
            "Subversion updates always replay your local changes on top of the "
            "server's, so --no-rebase has no effect"
        )

    before = ctx.info.revision
    args = ["update", str(ctx.wc_root), "--accept", "postpone"]
    if opts.positionals:
        ctx.note("Subversion has a single remote; ignoring '%s'" % " ".join(opts.positionals))

    result = ctx.svn.run(*args, mutating=True)
    if not opts.has("quiet", "q"):
        ctx.echo(result.stdout.rstrip() if result.stdout.strip() else "Already up to date.")

    after = ctx.svn.info(str(ctx.wc_root)).revision
    ctx._info = None
    if after > before:
        ctx.echo("Updating %s..%s" % (formatting.revision_id(before), formatting.revision_id(after)))
        conflicts = [e for e in status_mod.compute(ctx).entries if e.unmerged]
        if conflicts:
            ctx.echo("")
            ctx.echo("Automatic merge failed; fix conflicts and then commit the result.")
            for entry in conflicts:
                ctx.echo("\tboth modified: %s" % ctx.display_path(entry.path))
            return 1
    elif not opts.has("quiet", "q") and after == before:
        ctx.echo("Already up to date.")
    return 0


def cmd_fetch(ctx, argv: List[str]) -> int:
    opts = parse(argv, flags=["all", "prune", "p", "quiet", "q", "verbose", "v", "tags", "dry-run", "n"])
    root = str(ctx.wc_root)
    head = ctx.svn.info(root, revision="HEAD").revision
    base = ctx.info.revision
    if head <= base:
        if not opts.has("quiet", "q"):
            ctx.echo("Already up to date.")
        return 0

    entries = ctx.svn.log(root, revision="%d:%d" % (head, base + 1))
    ctx.echo("From %s" % ctx.info.url)
    ctx.echo(
        "   %s..%s  %s -> origin/%s"
        % (formatting.revision_id(base), formatting.revision_id(head), ctx.branch, ctx.branch)
    )
    if not opts.has("quiet", "q"):
        for entry in entries:
            ctx.echo("     %s %s" % (formatting.revision_id(entry.revision), entry.message.strip().splitlines()[0] if entry.message.strip() else ""))
        ctx.echo("")
        ctx.echo(
            'Subversion has no local mirror of unfetched revisions, so these are listed '
            'rather than downloaded. Run "git pull" to apply them.'
        )
    return 0
