"""git stash, built on `svn diff` + `svn patch`.

Subversion has no stash. A stash entry here is a unified diff of the working
copy plus, optionally, the content of untracked files -- all kept in the local
object store, so nothing touches the server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

import tempfile
from pathlib import Path
from typing import List

from .. import formatting, status as status_mod
from ..cliargs import Options, parse
from ..errors import SvnGitError, UsageError
from ..state import ADD, StashEntry, make_commit_id, now

if TYPE_CHECKING:  # pragma: no cover
    from ..context import Context


def cmd_stash(ctx: "Context", argv: List[str]) -> int:
    subcommand = "push"
    rest = list(argv)
    if rest and not rest[0].startswith("-"):
        subcommand = rest.pop(0)

    handlers = {
        "push": _push,
        "save": _push,
        "list": _list,
        "pop": lambda c, a: _apply(c, a, drop=True),
        "apply": lambda c, a: _apply(c, a, drop=False),
        "drop": _drop,
        "clear": _clear,
        "show": _show,
        "branch": _branch,
    }
    handler = handlers.get(subcommand)
    if handler is None:
        raise UsageError(
            "unknown stash subcommand '%s' (push, list, pop, apply, drop, clear, show)"
            % subcommand
        )
    return handler(ctx, rest)


def _push(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=[
            "include-untracked",
            "u",
            "keep-index",
            "k",
            "all",
            "a",
            "quiet",
            "q",
            "patch",
            "p",
        ],
        values=["message", "m"],
    )
    message = str(opts.first("message", "m", default="")) or " ".join(opts.positionals)

    if opts.has("patch", "p"):
        return _push_patch(ctx, opts, message)

    report = status_mod.compute(ctx)
    tracked = [e for e in report.entries if not e.untracked and not e.ignored]
    untracked = [e for e in report.entries if e.untracked]
    include_untracked = opts.has("include-untracked", "u", "all", "a")

    if not tracked and not (include_untracked and untracked):
        ctx.echo("No local changes to save")
        return 0

    diff = ctx.svn.run("diff", str(ctx.wc_root), check=False).stdout
    patch_blob = ctx.state.objects.write(diff.encode("utf-8")) if diff.strip() else None

    saved_untracked = []
    if include_untracked:
        for entry in untracked:
            absolute = ctx.abs_path(entry.path)
            if absolute.is_file():
                saved_untracked.append(
                    {"path": entry.path, "blob": ctx.state.objects.write_file(absolute)}
                )

    index_snapshot = {path: vars(entry) for path, entry in ctx.state.index.items()}
    timestamp = now()
    stash_entry = StashEntry(
        id=make_commit_id(message or "stash", [], timestamp),
        message=message
        or "WIP on %s: %s" % (ctx.branch, formatting.revision_id(ctx.info.revision)),
        timestamp=timestamp,
        base_revision=ctx.info.revision,
        patch_blob=patch_blob,
        untracked=saved_untracked,
        index=index_snapshot,
    )

    # Files scheduled for addition survive `svn revert` as untracked leftovers,
    # which would then collide when the patch re-adds them. Remove them the way
    # git does.
    added = [e.path for e in tracked if e.index == ADD]
    ctx.svn.run("revert", "-R", str(ctx.wc_root), mutating=True)
    for path in added:
        absolute = ctx.abs_path(path)
        if absolute.is_file():
            absolute.unlink()
    for saved in saved_untracked:
        absolute = ctx.abs_path(saved["path"])
        if absolute.is_file():
            absolute.unlink()

    ctx.state.push_stash(stash_entry)
    if opts.has("keep-index", "k"):
        # git leaves the staged content in the working copy; only the unstaged
        # changes go away. The index itself is untouched.
        for path, values in index_snapshot.items():
            blob = values.get("blob")
            if blob is None:
                continue
            absolute = ctx.abs_path(path)
            absolute.parent.mkdir(parents=True, exist_ok=True)
            absolute.write_bytes(ctx.state.objects.read(blob))
    else:
        ctx.state.clear_index()
    ctx.state.save()
    if not opts.has("quiet", "q"):
        ctx.echo("Saved working directory and index state %s" % stash_entry.message)
    return 0


def _push_patch(ctx: "Context", opts: Options, message: str) -> int:
    """`git stash -p`: choose hunks to take out of the working copy.

    The sense is the opposite of `git add -p`. A hunk you accept is removed
    from the working copy and kept in the stash; one you decline stays where
    it is. Both content versions are stored, so `pop` can put the file back
    exactly when nothing else has touched it since.
    """
    from .. import hunks as hunks_mod
    from .. import patch as patch_mod
    from .interactive import effective_base, select_hunks

    report = status_mod.compute(ctx)
    if opts.has("include-untracked", "u", "all", "a"):
        ctx.note("untracked files have no hunks to choose from; -p ignores them")

    saved: List[Dict[str, str]] = []
    touched: List[str] = []
    for entry in report.entries:
        if entry.untracked or entry.ignored or entry.unmerged:
            continue
        absolute = ctx.abs_path(entry.path)
        if not absolute.is_file():
            continue

        # Stash saves every local change, so compare against the last commit
        # rather than against the staging area.
        base_bytes = effective_base(ctx, entry, use_index=False)
        work_bytes = absolute.read_bytes()
        if base_bytes == work_bytes:
            continue
        if hunks_mod.is_binary(base_bytes) or hunks_mod.is_binary(work_bytes):
            ctx.echo("%s is binary; skipping." % ctx.display_path(entry.path))
            continue

        base_text = base_bytes.decode("utf-8", errors="replace")
        diff = hunks_mod.diff_file(
            base_text, work_bytes.decode("utf-8", errors="replace")
        )
        if diff.empty:
            continue

        ctx.echo("diff --git a/%s b/%s" % (entry.path, entry.path))
        selection = select_hunks(
            ctx, diff, base_text, entry.path, prompt="Stash this hunk"
        )
        if selection.accepted:
            kept = patch_mod.apply_file_patch(
                base_text, patch_mod.FilePatch(entry.path, selection.rejected)
            )
            saved.append(
                {
                    "path": entry.path,
                    "full": ctx.state.objects.write(work_bytes),
                    "kept": ctx.state.objects.write(kept.encode("utf-8")),
                }
            )
            touched.append(entry.path)
        if selection.quit:
            break

    if not saved:
        ctx.echo("No local changes to save")
        return 0

    index_snapshot = {
        path: vars(entry) for path, entry in ctx.state.index.items() if path in touched
    }
    timestamp = now()
    stash_entry = StashEntry(
        id=make_commit_id(message or "stash", [], timestamp),
        message=message
        or "WIP on %s: %s" % (ctx.branch, formatting.revision_id(ctx.info.revision)),
        timestamp=timestamp,
        base_revision=ctx.info.revision,
        partial=saved,
        index=index_snapshot,
    )

    # Write the kept content only after every file has been decided, so an
    # interruption partway through leaves the working copy untouched.
    for item in saved:
        ctx.abs_path(item["path"]).write_bytes(ctx.state.objects.read(item["kept"]))
        ctx.state.unstage(item["path"])

    ctx.state.push_stash(stash_entry)
    ctx.state.save()
    if not opts.has("quiet", "q"):
        ctx.echo("Saved working directory and index state %s" % stash_entry.message)
    return 0


def _restore_partial(ctx: "Context", entry: StashEntry) -> None:
    """Put back a `-p` stash.

    A three-way merge against the content the stash left behind. When nothing
    has touched the file since, "ours" has no edits and the result is the
    stashed content exactly; when it has, both sets of edits survive unless
    they overlap.
    """
    from .. import patch as patch_mod

    objects = ctx.state.objects
    for item in entry.partial:
        absolute = ctx.abs_path(item["path"])
        full = objects.read(item["full"])
        kept = objects.read(item["kept"])
        current = absolute.read_bytes() if absolute.is_file() else b""

        merged = patch_mod.merge3(
            kept.decode("utf-8", errors="replace"),
            current.decode("utf-8", errors="replace"),
            full.decode("utf-8", errors="replace"),
        )
        if merged is None:
            raise SvnGitError(
                "could not restore %s: it was changed in the same place since "
                "the stash was made.\n"
                "The stash is still there; reconcile the file and try again."
                % ctx.display_path(item["path"])
            )
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(merged.encode("utf-8"))


def _list(ctx: "Context", argv: List[str]) -> int:
    for position, entry in enumerate(ctx.state.stash):
        ctx.echo("stash@{%d}: %s" % (position, entry.message))
    return 0


def _resolve_index(ctx: "Context", argv: List[str]) -> int:
    entries = ctx.state.stash
    if not entries:
        raise SvnGitError("No stash entries found.")
    positionals = [a for a in argv if not a.startswith("-")]
    if not positionals:
        return 0
    raw = positionals[0]
    if raw.startswith("stash@{") and raw.endswith("}"):
        raw = raw[len("stash@{") : -1]
    try:
        position = int(raw)
    except ValueError:
        raise UsageError("%s is not a valid stash reference" % positionals[0])
    if position < 0 or position >= len(entries):
        raise SvnGitError("stash@{%d} is not a valid reference" % position)
    return position


def _apply(ctx: "Context", argv: List[str], drop: bool) -> int:
    position = _resolve_index(ctx, argv)
    entries = ctx.state.stash
    entry = entries[position]

    if entry.partial:
        _restore_partial(ctx, entry)
    elif entry.patch_blob:
        patch = ctx.state.objects.read(entry.patch_blob)
        with tempfile.NamedTemporaryFile("wb", suffix=".patch", delete=False) as handle:
            handle.write(patch)
            patch_path = handle.name
        try:
            result = ctx.svn.run(
                "patch", patch_path, str(ctx.wc_root), check=False, mutating=True
            )
        finally:
            Path(patch_path).unlink(missing_ok=True)
        if result.stdout.strip():
            ctx.echo(result.stdout.rstrip())
        if not result.ok:
            raise SvnGitError(
                "could not apply stash@{%d}:\n%s" % (position, result.stderr.strip())
            )

    for saved in entry.untracked:
        absolute = ctx.abs_path(saved["path"])
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(ctx.state.objects.read(saved["blob"]))

    for path, values in entry.index.items():
        ctx.state.stage(
            path,
            values.get("action", "M"),
            values.get("blob"),
            values.get("executable", False),
        )

    if drop:
        entries.pop(position)
        ctx.state.set_stash(entries)
    ctx.state.save()

    report = status_mod.compute(ctx)
    for line in formatting.format_porcelain(report):
        ctx.echo(line)
    if drop:
        ctx.echo("Dropped stash@{%d} (%s)" % (position, entry.id[:7]))
    return 0


def _drop(ctx: "Context", argv: List[str]) -> int:
    position = _resolve_index(ctx, argv)
    entries = ctx.state.stash
    entry = entries.pop(position)
    ctx.state.set_stash(entries)
    ctx.state.save()
    ctx.echo("Dropped stash@{%d} (%s)" % (position, entry.id[:7]))
    return 0


def _clear(ctx: "Context", argv: List[str]) -> int:
    ctx.state.set_stash([])
    ctx.state.save()
    return 0


def _show(ctx: "Context", argv: List[str]) -> int:
    from .. import colour as colour_mod
    from .. import patch as patch_mod

    position = _resolve_index(ctx, argv)
    entry = ctx.state.stash[position]
    palette = colour_mod.palette_for(ctx)
    for item in entry.partial:
        objects = ctx.state.objects
        for line in patch_mod.render_file_patch(
            item["path"],
            objects.read(item["kept"]).decode("utf-8", errors="replace"),
            objects.read(item["full"]).decode("utf-8", errors="replace"),
        ):
            ctx.echo(colour_mod.paint_diff_line(line, palette))
    if entry.patch_blob:
        ctx.echo(
            colour_mod.paint_diff(
                ctx.state.objects.read_text(entry.patch_blob).rstrip(), palette
            )
        )
    for saved in entry.untracked:
        ctx.echo("untracked: %s" % saved["path"])
    return 0


def _branch(ctx: "Context", argv: List[str]) -> int:
    """`git stash branch <name> [<stash>]`: start a branch and restore there.

    Useful when a stash no longer applies to the branch you are on; in
    Subversion terms, switch to a fresh branch directory and unstash into it.
    """
    positionals = [a for a in argv if not a.startswith("-")]
    if not positionals:
        raise UsageError("git stash branch <branchname> [<stash>]")
    name = positionals[0]

    from .branching import cmd_checkout

    if cmd_checkout(ctx, ["-b", name]) != 0:
        return 1
    return _apply(ctx, positionals[1:], drop=True)
