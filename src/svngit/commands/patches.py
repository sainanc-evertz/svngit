"""apply, format-patch and archive -- moving changes and trees in and out.

`git apply` uses svngit's own patch applier rather than `svn patch`, because
the patches it is handed are git-format and because the applier already
tolerates the stale hunk headers that hand-edited patches carry.
"""

from __future__ import annotations

import os
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional

from .. import formatting, patch as patch_mod, revisions as rev_mod
from ..cliargs import no_effect, parse, refuse
from ..errors import SvnGitError, UsageError


#: End-of-patch marker in mbox format. The trailing space is significant: it
#: is what separates the signature from a deletion of a line reading "-".
SIGNATURE = "-- "


# ----------------------------------------------------------------------
# apply
# ----------------------------------------------------------------------
def cmd_apply(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=[
            "check", "stat", "summary", "reverse", "R", "index", "cached",
            "3way", "3", "verbose", "v", "quiet", "q", "numstat",
        ],
        values=["p", "directory", "exclude", "include"],
    )
    refuse("apply", opts, {
        "3way": "a three-way apply needs the blobs the patch was made against, "
                "which a Subversion working copy does not store.",
        "3": "a three-way apply needs the blobs the patch was made against, "
             "which a Subversion working copy does not store.",
        "cached": "there is no index to apply to separately; drop --cached to "
                  "apply to the working copy.",
    })
    no_effect(ctx, "apply", opts, {
        "index": "the patch is applied to the working copy; stage it afterwards "
                 "with `git add`.",
        "exclude": "path filtering is not applied.",
        "include": "path filtering is not applied.",
        "directory": "paths are taken from the patch as given.",
    })

    text = _read_patch(opts.paths)
    strip = int(str(opts.first("p", default="1")))
    patches = patch_mod.parse_patch(text)
    if not patches:
        raise SvnGitError("unrecognized input: no patch found")

    for file_patch in patches:
        file_patch.path = _strip_path(file_patch.path, strip)
        if opts.has("reverse", "R"):
            _reverse(file_patch)

    if opts.has("stat") or opts.has("numstat") or opts.has("summary"):
        return _report(ctx, patches, opts)

    # Apply everything in memory first: git apply is all-or-nothing.
    results = []
    for file_patch in patches:
        absolute = ctx.abs_path(file_patch.path)
        current = absolute.read_text(encoding="utf-8", errors="replace") if absolute.is_file() else ""
        try:
            results.append((absolute, patch_mod.apply_file_patch(current, file_patch)))
        except patch_mod.PatchError as exc:
            raise SvnGitError("%s\nerror: patch failed; no files were changed." % exc)

    if opts.has("check"):
        if not opts.has("quiet", "q"):
            ctx.echo("Patch applies cleanly to %d file%s."
                     % (len(results), "" if len(results) == 1 else "s"))
        return 0

    for absolute, content in results:
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(content, encoding="utf-8")
        if opts.has("verbose", "v"):
            ctx.echo("Applied patch to '%s' cleanly."
                     % ctx.display_path(absolute.relative_to(ctx.wc_root).as_posix()))
    return 0


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


def _report(ctx, patches, opts) -> int:
    rows = []
    for file_patch in patches:
        insertions = deletions = 0
        for hunk in file_patch.hunks:
            added, removed = patch_mod.hunk_stats(hunk)
            insertions += added
            deletions += removed
        rows.append((file_patch.path, insertions, deletions))

    if opts.has("numstat"):
        for path, insertions, deletions in rows:
            ctx.echo("%d\t%d\t%s" % (insertions, deletions, path))
        return 0
    if opts.has("summary"):
        for path, _, _ in rows:
            ctx.echo(" %s" % path)
        return 0
    for line in formatting.format_diffstat(rows):
        ctx.echo(line)
    return 0


# ----------------------------------------------------------------------
# format-patch
# ----------------------------------------------------------------------
def cmd_format_patch(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["stdout", "numbered", "n", "no-numbered", "N", "quiet", "q", "signoff", "s"],
        values=["output-directory", "o", "start-number", "subject-prefix", "max-count"],
        allow_numeric=True,
        numeric_key="count",
    )
    no_effect(ctx, "format-patch", opts, {
        "no-numbered": "output files are always numbered, since a range has no "
                       "other stable ordering.",
        "N": "output files are always numbered, since a range has no other "
             "stable ordering.",
    })
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
        target.write_text(body, encoding="utf-8")
        if not opts.has("quiet", "q"):
            ctx.echo(os.path.relpath(target, ctx.cwd))
    return 0


def _mbox(ctx, entry, prefix: str, number: int, total: int, opts) -> str:
    """One revision as a git-am-compatible patch."""
    subject = entry.message.strip().splitlines()[0] if entry.message.strip() else "(no message)"
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
    lines.append(_git_headers(ctx, diff.stdout).rstrip("\n") if diff.ok else "")
    lines.extend([SIGNATURE, "svngit"])
    return "\n".join(lines) + "\n"


def _git_headers(ctx, diff_text: str) -> str:
    """Rewrite `svn diff` headers into the git form.

    format-patch output is meant to be portable -- `git apply` elsewhere, or
    `svngit apply` here -- and both expect `diff --git a/x b/x` with relative
    paths, not svn's `Index:` plus absolute paths and revision annotations.
    """
    out: List[str] = []
    for line in diff_text.splitlines():
        if line.startswith("Index: "):
            path = _relative(ctx, line[len("Index: ") :])
            out.append("diff --git a/%s b/%s" % (path, path))
            continue
        if set(line.strip()) == {"="} and line.strip():
            continue  # svn's rule under the Index line
        if line.startswith("--- ") or line.startswith("+++ "):
            marker, prefix = (line[:3], "a/") if line.startswith("---") else (line[:3], "b/")
            out.append("%s %s%s" % (marker, prefix, _relative(ctx, line[4:])))
            continue
        out.append(line)
    return "\n".join(out)


def _relative(ctx, raw: str) -> str:
    """Strip svn's tab annotation and make the path working-copy relative."""
    candidate = Path(raw.split("\t")[0].strip())
    try:
        return candidate.resolve().relative_to(ctx.wc_root.resolve()).as_posix()
    except (ValueError, OSError):
        return candidate.as_posix()


def _slug(message: str) -> str:
    subject = message.strip().splitlines()[0] if message.strip() else "patch"
    cleaned = "".join(c if c.isalnum() else "-" for c in subject).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned or "patch")[:52]


# ----------------------------------------------------------------------
# archive
# ----------------------------------------------------------------------
def cmd_archive(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["verbose", "v", "list", "l"],
        values=["format", "o", "output", "prefix", "remote"],
    )
    refuse("archive", opts, {
        "remote": "there is no archive service to ask; svngit exports from the "
                  "repository directly, which needs no --remote.",
    })
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
            "export", "-r", str(revision), "--force",
            "%s@%d" % (ctx.info.url, revision), str(exported),
            mutating=False,
        )
        if output is None:
            raise UsageError("git archive needs -o <file> (writing to a terminal is refused)")
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
                    archive.write(path, str(Path(prefix) / inner) if prefix else str(inner))
        return

    mode = "w:gz" if fmt in ("tar.gz", "tgz") else "w"
    with tarfile.open(target, mode) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                inner = path.relative_to(source)
                archive.add(path, arcname=str(Path(prefix) / inner) if prefix else str(inner))
