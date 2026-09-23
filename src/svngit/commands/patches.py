"""apply, format-patch and archive -- moving changes and trees in and out.

`git apply` uses svngit's own patch applier rather than `svn patch`, because
the patches it is handed are git-format and because the applier already
tolerates the stale hunk headers that hand-edited patches carry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import os
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional

from .. import formatting, patch as patch_mod, revisions as rev_mod
from ..cliargs import Options, no_effect, parse, refuse
from ..errors import SvnGitError, UsageError
from ..state import ADD, DELETE
from ..svnclient import LogEntry

if TYPE_CHECKING:  # pragma: no cover
    from ..context import Context

#: End-of-patch marker in mbox format. The trailing space is significant: it
#: is what separates the signature from a deletion of a line reading "-".
SIGNATURE = "-- "


# ----------------------------------------------------------------------
# apply
# ----------------------------------------------------------------------
def cmd_apply(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=[
            "check",
            "stat",
            "summary",
            "reverse",
            "R",
            "index",
            "cached",
            "3way",
            "3",
            "verbose",
            "v",
            "quiet",
            "q",
            "numstat",
        ],
        values=["p", "directory", "exclude", "include"],
    )
    refuse(
        "apply",
        opts,
        {
            "3way": "a three-way apply needs the blobs the patch was made against, "
            "which a Subversion working copy does not store.",
            "3": "a three-way apply needs the blobs the patch was made against, "
            "which a Subversion working copy does not store.",
            "cached": "there is no index to apply to separately; drop --cached to "
            "apply to the working copy.",
        },
    )
    no_effect(
        ctx,
        "apply",
        opts,
        {
            "index": "the patch is applied to the working copy; stage it afterwards "
            "with `git add`.",
            "exclude": "path filtering is not applied.",
            "include": "path filtering is not applied.",
            "directory": "paths are taken from the patch as given.",
        },
    )

    text = _read_patch(opts.paths)
    strip = int(str(opts.first("p", default="1")))
    patches = patch_mod.parse_patch(text)
    if not patches:
        raise SvnGitError("unrecognized input: no patch found")

    for file_patch in patches:
        # -p<n> counts components of the path as the patch wrote it, and the
        # `a/`/`b/` prefix is one of them. The parser removed it, so put it
        # back before stripping -- otherwise `-p1` eats the prefix *and* the
        # first real directory.
        file_patch.path = _strip_path(file_patch.patch_path, strip)
        file_patch.prefix = ""
        # These paths came out of the patch file, not out of svngit. Check them
        # before anything is read, written or even counted, so that --stat
        # cannot be used to probe outside the working copy either.
        ctx.abs_path_inside(file_patch.path)
        if file_patch.source_path is not None:
            if file_patch.is_copy:
                raise SvnGitError(
                    "%s is a copy, which svngit does not apply. Make the copy "
                    "yourself with `svn copy <source> %s`, then apply the rest "
                    "of the patch." % (file_patch.path, file_patch.path)
                )
            file_patch.source_path = _strip_path(file_patch.source_patch_path, strip)
            file_patch.source_prefix = ""
            # A rename deletes one path and writes another, so the source has
            # to clear the same check as the destination.
            ctx.abs_path_inside(file_patch.source_path)
        if opts.has("reverse", "R"):
            _reverse(file_patch)

    if opts.has("stat") or opts.has("numstat") or opts.has("summary"):
        return _report(ctx, patches, opts)

    # Apply everything in memory first: git apply is all-or-nothing.
    results = []
    for file_patch in patches:
        absolute = ctx.abs_path_inside(file_patch.path)
        source = (
            ctx.abs_path_inside(file_patch.source_path)
            if file_patch.source_path is not None
            else None
        )
        if source is not None and not source.is_file():
            raise SvnGitError(
                "%s: No such file or directory\n"
                "error: patch failed; no files were changed."
                % ctx.display_path(file_patch.source_path or "")
            )
        # A rename patches the content it is moving, so the hunks belong to the
        # source. Read and write as bytes throughout: text mode translates
        # newlines on Windows, which would rewrite every line ending in the
        # file the patch touches.
        read_from = source if source is not None else absolute
        current = (
            read_from.read_bytes().decode("utf-8", errors="replace")
            if read_from.is_file()
            else ""
        )
        try:
            content = patch_mod.apply_file_patch(current, file_patch)
        except patch_mod.PatchError as exc:
            raise SvnGitError("%s\nerror: patch failed; no files were changed." % exc)
        results.append((file_patch, source, absolute, content))

    if opts.has("check"):
        if not opts.has("quiet", "q"):
            ctx.echo(
                "Patch applies cleanly to %d file%s."
                % (len(results), "" if len(results) == 1 else "s")
            )
        return 0

    staged_a_rename = False
    for file_patch, source, absolute, content in results:
        absolute.parent.mkdir(parents=True, exist_ok=True)
        moved_by_svn = source is not None and _rename(ctx, source, absolute)
        absolute.write_bytes(content.encode("utf-8"))
        if moved_by_svn:
            # svn move has already scheduled both halves, so the rename is
            # staged as far as Subversion is concerned. Record it in svngit's
            # state too: `git status` reads svn, but `git commit` reads this,
            # and without it the rename showed up in one and not the other.
            # Snapshot after the write, so the staged blob is the patched file.
            ctx.state.stage(str(file_patch.source_path), DELETE)
            ctx.state.stage(file_patch.path, ADD, ctx.snapshot(file_patch.path))
            staged_a_rename = True
        if opts.has("verbose", "v"):
            target = ctx.display_path(file_patch.path)
            if source is None:
                ctx.echo("Applied patch to '%s' cleanly." % target)
            else:
                ctx.echo(
                    "Renamed '%s' to '%s' and applied the patch cleanly."
                    % (ctx.display_path(str(file_patch.source_path)), target)
                )
    if staged_a_rename:
        ctx.state.save()
    return 0


def _rename(ctx: "Context", source: Path, target: Path) -> bool:
    """Move a file, keeping its history when Subversion knows the source.

    git's own `apply` touches only the worktree and leaves `git add` to infer
    the rename afterwards from content similarity. Subversion has no rename
    detection to infer it with: a plain filesystem move leaves the old path
    missing and the new one unversioned, and committing that loses the file's
    history for good. `svn move` is the only way to move a versioned file *as*
    a move, and it schedules the change, so a rename arrives staged where an
    ordinary hunk does not. Subversion forces that difference; it is not a
    choice svngit is free to make.
    """
    if ctx.svn.is_versioned(str(source)):
        ctx.svn.run("move", str(source), str(target), mutating=True)
        return True
    source.replace(target)
    return False


def _read_patch(paths: List[str]) -> str:
    if not paths or paths == ["-"]:
        return sys.stdin.read()
    return "\n".join(Path(p).expanduser().read_text(encoding="utf-8") for p in paths)


def _strip_path(path: str, strip: int) -> str:
    """Apply git's -p<n>: drop that many leading components."""
    parts = path.split("/")
    return "/".join(parts[strip:]) if len(parts) > strip else parts[-1]


def _reverse(file_patch: patch_mod.FilePatch) -> None:
    for hunk in file_patch.hunks:
        hunk.old_lines, hunk.new_lines = hunk.new_lines, hunk.old_lines
    if file_patch.source_path is not None:
        # Undoing a rename means moving it back, so the two ends swap.
        file_patch.path, file_patch.source_path = (
            file_patch.source_path,
            file_patch.path,
        )


def _report(ctx: "Context", patches: List[patch_mod.FilePatch], opts: Options) -> int:
    rows = []
    for file_patch in patches:
        insertions = deletions = 0
        for hunk in file_patch.hunks:
            added, removed = patch_mod.hunk_stats(hunk)
            insertions += added
            deletions += removed
        # git names both ends of a rename in a diffstat, because "3 +-" against
        # a path that does not exist yet reads as nonsense on its own.
        label = file_patch.path
        if file_patch.source_path is not None:
            label = "%s => %s" % (file_patch.source_path, file_patch.path)
        rows.append((label, insertions, deletions))

    if opts.has("numstat"):
        for path, insertions, deletions in rows:
            ctx.echo("%d\t%d\t%s" % (insertions, deletions, path))
        return 0
    if opts.has("summary"):
        for file_patch in patches:
            if file_patch.source_path is not None:
                ctx.echo(" rename %s => %s" % (file_patch.source_path, file_patch.path))
            else:
                ctx.echo(" %s" % file_patch.path)
        return 0
    for line in formatting.format_diffstat(rows):
        ctx.echo(line)
    return 0


# ----------------------------------------------------------------------
# format-patch
# ----------------------------------------------------------------------
def cmd_format_patch(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=[
            "stdout",
            "numbered",
            "n",
            "no-numbered",
            "N",
            "quiet",
            "q",
            "signoff",
            "s",
        ],
        values=["output-directory", "o", "start-number", "subject-prefix", "max-count"],
        allow_numeric=True,
        numeric_key="count",
    )
    no_effect(
        ctx,
        "format-patch",
        opts,
        {
            "no-numbered": "output files are always numbered, since a range has no "
            "other stable ordering.",
            "N": "output files are always numbered, since a range has no other "
            "stable ordering.",
        },
    )
    # --numbered is the only behaviour, so it needs no handling.
    spec = opts.positionals[0] if opts.positionals else None
    count = opts.first("count", "max-count")

    if spec is None and count is None:
        raise UsageError(
            "git format-patch <revision-range>\n"
            "for example: git format-patch -3, or git format-patch r10..r20"
        )

    if spec is None:
        entries = ctx.svn.log(str(ctx.wc_root), limit=int(str(count)))
    elif rev_mod.is_range(spec):
        entries = ctx.svn.log(
            str(ctx.wc_root), revision=rev_mod.to_svn_range(ctx, spec, str(ctx.wc_root))
        )
    else:
        number = rev_mod.resolve(ctx, spec, str(ctx.wc_root))
        entries = ctx.svn.log(str(ctx.wc_root), revision=str(number))

    entries = list(reversed(entries))  # oldest first, as git numbers them
    if not entries:
        ctx.echo("No revisions to format.")
        return 0

    directory = Path(str(opts.first("output-directory", "o", default="."))).expanduser()
    prefix = str(opts.first("subject-prefix", default="PATCH"))
    start = int(str(opts.first("start-number", default="1")))

    for offset, entry in enumerate(entries):
        body = _mbox(ctx, entry, prefix, offset + start, len(entries), opts)
        if opts.has("stdout"):
            ctx.echo(body.rstrip("\n"))
            continue
        directory.mkdir(parents=True, exist_ok=True)
        name = "%04d-%s.patch" % (offset + start, _slug(entry.message))
        target = directory / name
        # Written as bytes so the patch carries exactly the line endings
        # it was composed with, on every platform.
        target.write_bytes(body.encode("utf-8"))
        if not opts.has("quiet", "q"):
            ctx.echo(os.path.relpath(target, ctx.cwd))
    return 0


def _mbox(
    ctx: "Context",
    entry: LogEntry,
    prefix: str,
    number: int,
    total: int,
    opts: Options,
) -> str:
    """One revision as a git-am-compatible patch."""
    subject = (
        entry.message.strip().splitlines()[0]
        if entry.message.strip()
        else "(no message)"
    )
    rest = "\n".join(entry.message.strip().splitlines()[1:]).strip()
    author = formatting.author_line(entry.author, ctx.info.repos_uuid)
    tag = "%s %d/%d" % (prefix, number, total) if total > 1 else prefix

    lines = [
        "From %s Mon Sep 17 00:00:00 2001" % formatting.revision_id(entry.revision),
        "From: %s" % author,
        "Date: %s" % formatting.format_date(entry.date),
        "Subject: [%s] %s" % (tag, subject),
        "",
    ]
    if rest:
        lines.extend([rest, ""])
    if opts.has("signoff", "s"):
        lines.extend(["Signed-off-by: %s" % author, ""])
    lines.append("---")

    diff = ctx.svn.run("diff", "-c", str(entry.revision), str(ctx.wc_root), check=False)
    lines.append(
        patch_mod.to_git_headers(diff.stdout, ctx.wc_root).rstrip("\n")
        if diff.ok
        else ""
    )
    lines.extend([SIGNATURE, "svngit"])
    return "\n".join(lines) + "\n"


def _slug(message: str) -> str:
    subject = message.strip().splitlines()[0] if message.strip() else "patch"
    cleaned = "".join(c if c.isalnum() else "-" for c in subject).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned or "patch")[:52]


# ----------------------------------------------------------------------
# archive
# ----------------------------------------------------------------------
def cmd_archive(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["verbose", "v", "list", "l"],
        values=["format", "o", "output", "prefix", "remote"],
    )
    refuse(
        "archive",
        opts,
        {
            "remote": "there is no archive service to ask; svngit exports from the "
            "repository directly, which needs no --remote.",
        },
    )
    if opts.has("list", "l"):
        for name in ("tar", "tar.gz", "tgz", "zip"):
            ctx.echo(name)
        return 0

    output = opts.first("output", "o")
    spec = opts.positionals[0] if opts.positionals else "HEAD"
    fmt = str(opts.first("format", default="")) or _format_from_name(output)
    if fmt not in ("tar", "tar.gz", "tgz", "zip"):
        raise UsageError("unknown archive format '%s' (tar, tar.gz, zip)" % fmt)

    revision = rev_mod.resolve(ctx, spec, str(ctx.wc_root))
    prefix = str(opts.first("prefix", default="")).rstrip("/")

    with tempfile.TemporaryDirectory() as work:
        exported = Path(work) / (prefix or "archive")
        ctx.svn.run(
            "export",
            "-r",
            str(revision),
            "--force",
            "%s@%d" % (ctx.info.url, revision),
            str(exported),
            mutating=False,
        )
        if output is None:
            raise UsageError(
                "git archive needs -o <file> (writing to a terminal is refused)"
            )
        target = Path(str(output)).expanduser()
        _write_archive(exported, target, fmt, prefix)

    if opts.has("verbose", "v"):
        ctx.echo("Wrote %s from %s" % (output, formatting.revision_id(revision)))
    return 0


def _format_from_name(output: Optional[str]) -> str:
    name = str(output or "").lower()
    if name.endswith(".zip"):
        return "zip"
    if name.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    return "tar"


def _write_archive(source: Path, target: Path, fmt: str, prefix: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "zip":
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    inner = path.relative_to(source)
                    archive.write(
                        path, str(Path(prefix) / inner) if prefix else str(inner)
                    )
        return

    # tarfile.open's mode is a Literal, so the two cases stay separate rather
    # than being selected through a variable.
    opened = (
        tarfile.open(target, "w:gz")
        if fmt in ("tar.gz", "tgz")
        else tarfile.open(target, "w")
    )
    with opened as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                inner = path.relative_to(source)
                archive.add(
                    path, arcname=str(Path(prefix) / inner) if prefix else str(inner)
                )
