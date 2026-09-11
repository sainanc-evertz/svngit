"""log, show, diff, blame."""

from __future__ import annotations

from typing import List, Optional, Tuple

from .. import formatting, revisions as rev_mod, status as status_mod
from ..cliargs import parse, split_revisions_and_paths
from ..errors import UsageError
from ..state import ADD, DELETE
from ..svnclient import parse_svn_date


# ----------------------------------------------------------------------
# log
# ----------------------------------------------------------------------
def cmd_log(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["oneline", "graph", "stat", "name-only", "name-status", "patch", "p", "reverse", "all", "decorate", "abbrev-commit", "no-merges", "follow"],
        values=["max-count", "n", "author", "grep", "since", "after", "until", "before", "pretty", "format", "skip"],
        allow_numeric=True,
    )
    revisions, paths = split_revisions_and_paths(ctx, opts.positionals)
    paths.extend(opts.after_dashdash)

    if opts.has("graph"):
        ctx.note("Subversion history is linear, so --graph has nothing to draw")

    target = ctx.svn_target(ctx.to_wc_path(paths[0])) if paths else str(ctx.wc_root)

    revision_arg = _log_range(ctx, revisions, target, opts)
    limit = opts.first("max-count", "n")
    entries = ctx.svn.log(
        target,
        revision=revision_arg,
        limit=int(limit) if limit else None,
        verbose=opts.has("stat", "name-only", "name-status"),
        search=str(opts.get("grep")) if opts.has("grep") else None,
        extra=["--diff"] if opts.has("patch", "p") else None,
    )

    if opts.has("author"):
        wanted = str(opts.get("author"))
        entries = [e for e in entries if wanted.lower() in e.author.lower()]

    if opts.has("skip"):
        entries = entries[int(str(opts.get("skip"))) :]
    if opts.has("reverse"):
        entries = list(reversed(entries))

    oneline = opts.has("oneline") or str(opts.first("pretty", "format", default="")) == "oneline"
    uuid = ctx.info.repos_uuid

    # Unpushed local commits sit on top of server history, exactly as they do
    # in git.
    pending = ctx.state.commits
    if pending and not revisions and not opts.has("reverse"):
        for commit in reversed(pending):
            for line in formatting.format_local_commit(commit, uuid, oneline=oneline):
                ctx.echo(line)

    for entry in entries:
        for line in formatting.format_log_entry(
            entry, uuid, oneline=oneline, show_paths=opts.has("name-only", "name-status", "stat")
        ):
            ctx.echo(line)
    return 0


def _log_range(ctx, revisions: List[str], target: str, opts) -> Optional[str]:
    if revisions:
        return rev_mod.to_svn_range(ctx, revisions[0], target)
    since = opts.first("since", "after")
    until = opts.first("until", "before")
    if since or until:
        start = "{%s}" % until if until else "BASE"
        end = "{%s}" % since if since else "1"
        return "%s:%s" % (start, end)
    return None


# ----------------------------------------------------------------------
# show
# ----------------------------------------------------------------------
def cmd_show(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["stat", "name-only", "name-status", "oneline", "quiet", "s"],
        values=["pretty", "format"],
    )
    spec = opts.positionals[0] if opts.positionals else "HEAD"
    revision = rev_mod.resolve(ctx, spec, str(ctx.wc_root))

    entries = ctx.svn.log(str(ctx.wc_root), revision=str(revision), verbose=True)
    if not entries:
        raise UsageError("no revision %s in this working copy's history" % spec)
    entry = entries[0]

    for line in formatting.format_log_entry(
        entry, ctx.info.repos_uuid, oneline=opts.has("oneline"), show_paths=opts.has("name-only", "name-status", "stat")
    ):
        ctx.echo(line)

    if opts.has("quiet", "s") or opts.has("name-only", "name-status"):
        return 0

    diff = ctx.svn.run("diff", "-c", str(revision), str(ctx.wc_root), check=False)
    if diff.ok and diff.stdout.strip():
        ctx.echo(diff.stdout.rstrip())
    return 0


# ----------------------------------------------------------------------
# diff
# ----------------------------------------------------------------------
def cmd_diff(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["cached", "staged", "stat", "name-only", "name-status", "numstat", "shortstat", "no-color", "color", "text", "binary"],
        values=["unified", "U", "diff-filter"],
    )
    revisions, paths = split_revisions_and_paths(ctx, opts.positionals)
    paths.extend(opts.after_dashdash)
    wc_paths = ctx.to_wc_paths(paths) if paths else None
    targets = [ctx.svn_target(p) for p in wc_paths] if wc_paths else [str(ctx.wc_root)]

    if revisions:
        spec = revisions[0] if len(revisions) == 1 else "%s..%s" % (revisions[0], revisions[1])
        if spec.upper() in ("HEAD", "@") and not rev_mod.is_range(spec):
            diff_text = ctx.svn.run("diff", *targets, check=False).stdout
        else:
            revision_arg = rev_mod.to_svn_range(ctx, spec, targets[0])
            diff_text = ctx.svn.run("diff", "-r", revision_arg, *targets, check=False).stdout
        return _emit_diff(ctx, diff_text, opts)

    if opts.has("cached", "staged"):
        return _emit_diff(ctx, _staged_diff(ctx, wc_paths), opts)

    return _emit_diff(ctx, _worktree_diff(ctx, wc_paths), opts)


def _staged_diff(ctx, wc_paths: Optional[List[str]]) -> str:
    """index vs BASE: compare each staged blob against its pristine copy."""
    chunks: List[str] = []
    for path, entry in sorted(ctx.state.index.items()):
        if wc_paths and path not in wc_paths:
            continue
        target = ctx.svn_target(path)
        if entry.action == DELETE:
            old = ctx.svn.run("cat", "-r", "BASE", target, check=False).stdout.encode("utf-8")
            chunks.extend(formatting.unified_diff(old, b"", path, new_label="/dev/null"))
            continue
        new = ctx.state.objects.read(entry.blob) if entry.blob else b""
        if entry.action == ADD:
            chunks.extend(formatting.unified_diff(b"", new, path, old_label="/dev/null"))
            continue
        result = ctx.svn.run("cat", "-r", "BASE", target, check=False)
        old = result.stdout.encode("utf-8") if result.ok else b""
        chunks.extend(formatting.unified_diff(old, new, path))
    return "\n".join(chunks) + ("\n" if chunks else "")


def _worktree_diff(ctx, wc_paths: Optional[List[str]]) -> str:
    """worktree vs index: staged paths compare against their staged blob,
    everything else against BASE (which is what svn diff already gives)."""
    index = ctx.state.index
    queued = ctx.state.queued_blobs()
    chunks: List[str] = []

    report = status_mod.compute(ctx, wc_paths)
    unstaged_targets = []
    for entry in report.entries:
        if entry.untracked or entry.ignored:
            continue
        if entry.path in index:
            staged = index[entry.path]
            if staged.blob is None:
                continue
            absolute = ctx.abs_path(entry.path)
            current = absolute.read_bytes() if absolute.is_file() else b""
            chunks.extend(
                formatting.unified_diff(ctx.state.objects.read(staged.blob), current, entry.path)
            )
        elif entry.unstaged:
            if entry.path in queued:
                # Already committed locally: diff against that commit, since
                # `svn diff` would compare against the unpushed BASE instead.
                blob = queued[entry.path]
                old = ctx.state.objects.read(blob) if blob else b""
                absolute = ctx.abs_path(entry.path)
                current = absolute.read_bytes() if absolute.is_file() else b""
                chunks.extend(formatting.unified_diff(old, current, entry.path))
            else:
                unstaged_targets.append(ctx.svn_target(entry.path))

    text = "\n".join(chunks) + ("\n" if chunks else "")
    if unstaged_targets:
        text += ctx.svn.run("diff", *unstaged_targets, check=False).stdout
    return text


def _emit_diff(ctx, diff_text: str, opts) -> int:
    if not diff_text.strip():
        return 0

    if opts.has("name-only") or opts.has("name-status") or opts.has("stat") or opts.has("numstat") or opts.has("shortstat"):
        per_file = _split_by_file(diff_text)
        if opts.has("name-only"):
            for path, _ in per_file:
                ctx.echo(path)
            return 0
        if opts.has("name-status"):
            for path, body in per_file:
                ctx.echo("%s\t%s" % (_status_letter(body), path))
            return 0
        rows = [(path, *formatting.count_diff_lines(body)) for path, body in per_file]
        if opts.has("numstat"):
            for path, ins, dele in rows:
                ctx.echo("%d\t%d\t%s" % (ins, dele, path))
            return 0
        lines = formatting.format_diffstat(rows)
        if opts.has("shortstat"):
            ctx.echo(lines[-1])
            return 0
        for line in lines:
            ctx.echo(line)
        return 0

    ctx.echo(diff_text.rstrip("\n"))
    return 0


def _split_by_file(diff_text: str) -> List[Tuple[str, str]]:
    """Group a unified diff into (path, body) pairs.

    Handles both our own `diff --git` headers and svn's `Index:` headers.
    """
    files: List[Tuple[str, List[str]]] = []
    current: Optional[Tuple[str, List[str]]] = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1] if " b/" in line else line.split()[-1]
            current = (path, [])
            files.append(current)
            continue
        if line.startswith("Index: "):
            current = (line[len("Index: ") :].strip(), [])
            files.append(current)
            continue
        if current is not None:
            current[1].append(line)
    return [(path, "\n".join(body)) for path, body in files]


def _status_letter(body: str) -> str:
    if "--- /dev/null" in body:
        return "A"
    if "+++ /dev/null" in body:
        return "D"
    return "M"


# ----------------------------------------------------------------------
# blame
# ----------------------------------------------------------------------
def cmd_blame(ctx, argv: List[str]) -> int:
    opts = parse(argv, flags=["line-porcelain", "porcelain", "s", "w"], values=["L", "revision", "r"])
    if not opts.paths:
        raise UsageError("git blame <file>")

    wc_path = ctx.to_wc_path(opts.paths[0])
    target = ctx.svn_target(wc_path)
    args = ["blame", target]
    revision = opts.first("revision", "r")
    if revision:
        args.extend(["-r", str(rev_mod.resolve(ctx, str(revision), target))])
    root = ctx.svn.xml(*args)
    if root is None:
        return 1

    text = ctx.svn.run("cat", target, check=False).stdout.splitlines()
    entries = list(root.iter("entry"))
    width = len(str(len(entries)))

    for entry in entries:
        number = int(entry.get("line-number") or 0)
        commit = entry.find("commit")
        if commit is None:
            revision_id, author, date = "00000000", "Not Committed Yet", ""
        else:
            revision_id = formatting.revision_id(int(commit.get("revision") or 0))
            author_node = commit.find("author")
            author = author_node.text if author_node is not None and author_node.text else "(unknown)"
            date_node = commit.find("date")
            parsed = parse_svn_date(date_node.text if date_node is not None and date_node.text else "")
            date = parsed.strftime("%Y-%m-%d %H:%M:%S %z") if parsed else ""
        line = text[number - 1] if 0 < number <= len(text) else ""
        if opts.has("s"):
            ctx.echo("%s %*d) %s" % (revision_id, width, number, line))
        else:
            ctx.echo("%s (%-16s %s %*d) %s" % (revision_id, author, date, width, number, line))
    return 0
