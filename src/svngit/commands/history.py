"""log, show, diff, blame."""

from __future__ import annotations

import fnmatch
import re
from typing import Dict, List, Optional, Tuple

from .. import colour as colour_mod, formatting, patch as patch_mod
from .. import revisions as rev_mod, status as status_mod
from ..cliargs import no_effect, parse, refuse, split_revisions_and_paths
from ..errors import SvnGitError, UsageError
from ..state import ADD, DELETE
from ..svnclient import parse_svn_date


# ----------------------------------------------------------------------
# log
# ----------------------------------------------------------------------
def cmd_log(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["oneline", "graph", "stat", "name-only", "name-status", "patch", "p", "reverse", "all", "decorate", "abbrev-commit", "no-merges", "follow", "no-color"],
        values=["max-count", "n", "author", "grep", "since", "after", "until",
                "before", "pretty", "format", "skip", "S", "G"],
        optional_values=["color"],
        allow_numeric=True,
    )
    revisions, paths = split_revisions_and_paths(ctx, opts.positionals)
    paths.extend(opts.after_dashdash)

    no_effect(ctx, "log", opts, {
        "graph": "Subversion history is linear, so there is nothing to draw.",
        "decorate": "branch and tag names are directories, not refs, so revisions "
                    "carry no decoration.",
        "no-merges": "plain `svn log` does not mark which revisions were merges, "
                     "so they cannot be filtered out.",
    })

    if paths:
        target = ctx.svn_target(ctx.to_wc_path(paths[0]))
    elif opts.has("all"):
        # Every branch at once is the repository root, since branches are
        # directories under it.
        target = ctx.info.repos_root
    else:
        target = str(ctx.wc_root)

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

    if opts.has("S") or opts.has("G"):
        entries = _pickaxe(ctx, entries, opts, target)

    if opts.has("skip"):
        entries = entries[int(str(opts.get("skip"))) :]
    if opts.has("reverse"):
        entries = list(reversed(entries))

    oneline = opts.has("oneline") or str(opts.first("pretty", "format", default="")) == "oneline"
    uuid = ctx.info.repos_uuid

    # Unpushed local commits sit on top of server history, exactly as they do
    # in git.
    palette = colour_mod.palette_for(ctx, opts)
    pending = ctx.state.commits
    if pending and not revisions and not opts.has("reverse"):
        for commit in reversed(pending):
            for line in formatting.format_local_commit(commit, uuid, oneline=oneline):
                ctx.echo(colour_mod.paint_log_line(line, palette))

    for entry in entries:
        for line in formatting.format_log_entry(
            entry, uuid, oneline=oneline, show_paths=opts.has("name-only", "name-status", "stat")
        ):
            ctx.echo(colour_mod.paint_log_line(line, palette))
    return 0


def _pickaxe(ctx, entries, opts, target: str):
    """Filter revisions by what their diff contains.

    `svn log --search` matches the commit message only, so -S and -G have to
    read each revision's diff. That costs one `svn diff` per revision, which
    is why the caller should narrow the range first.
    """
    needle = opts.first("S")
    expression = opts.first("G")
    matcher = None
    if expression is not None:
        try:
            matcher = re.compile(str(expression))
        except re.error as exc:
            raise UsageError("invalid regex for -G: %s" % exc)

    if len(entries) > 200:
        ctx.note(
            "-S/-G reads the diff of every revision in range; narrowing with "
            "-n or a revision range will be much faster"
        )

    kept = []
    for entry in entries:
        result = ctx.svn.run("diff", "-c", str(entry.revision), target, check=False)
        if not result.ok:
            continue
        changed = [
            line for line in result.stdout.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        ]
        if matcher is not None:
            if any(matcher.search(line[1:]) for line in changed):
                kept.append(entry)
            continue
        # -S is git's "how many times does this string appear" test: keep the
        # revision when an added or removed line mentions it.
        text = str(needle)
        if any(text in line[1:] for line in changed):
            kept.append(entry)
    return kept


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
        flags=["stat", "name-only", "name-status", "oneline", "quiet", "s", "no-color"],
        values=["pretty", "format"],
        optional_values=["color"],
    )
    spec = opts.positionals[0] if opts.positionals else "HEAD"
    revision = rev_mod.resolve(ctx, spec, str(ctx.wc_root))

    entries = ctx.svn.log(str(ctx.wc_root), revision=str(revision), verbose=True)
    if not entries:
        raise UsageError("no revision %s in this working copy's history" % spec)
    entry = entries[0]

    palette = colour_mod.palette_for(ctx, opts)
    for line in formatting.format_log_entry(
        entry, ctx.info.repos_uuid, oneline=opts.has("oneline"), show_paths=opts.has("name-only", "name-status", "stat")
    ):
        ctx.echo(colour_mod.paint_log_line(line, palette))

    if opts.has("quiet", "s") or opts.has("name-only", "name-status"):
        return 0

    diff = ctx.svn.run("diff", "-c", str(revision), str(ctx.wc_root), check=False)
    if diff.ok and diff.stdout.strip():
        palette = colour_mod.palette_for(ctx, opts)
        normalised = patch_mod.to_git_headers(diff.stdout, ctx.wc_root).rstrip()
        ctx.echo(colour_mod.paint_diff(normalised, palette))
    return 0


# ----------------------------------------------------------------------
# diff
# ----------------------------------------------------------------------
def cmd_diff(ctx, argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["cached", "staged", "stat", "name-only", "name-status", "numstat", "shortstat", "no-color", "text", "binary"],
        values=["unified", "U", "diff-filter"],
        optional_values=["color"],
    )
    no_effect(ctx, "diff", opts, {
        "binary": "Subversion diffs cannot carry binary content.",
        "text": "binary files are reported as differing, never inlined.",
        "diff-filter": "the diff is not filtered by change type.",
    })
    context = opts.first("unified", "U")

    revisions, paths = split_revisions_and_paths(ctx, opts.positionals)
    paths.extend(opts.after_dashdash)
    wc_paths = ctx.to_wc_paths(paths) if paths else None
    targets = [ctx.svn_target(p) for p in wc_paths] if wc_paths else [str(ctx.wc_root)]
    extra = ["-x", "-U%s" % context] if context else []

    if revisions:
        spec = revisions[0] if len(revisions) == 1 else "%s..%s" % (revisions[0], revisions[1])
        if spec.upper() in ("HEAD", "@") and not rev_mod.is_range(spec):
            diff_text = ctx.svn.run("diff", *extra, *targets, check=False).stdout
        else:
            revision_arg = rev_mod.to_svn_range(ctx, spec, targets[0])
            diff_text = ctx.svn.run(
                "diff", "-r", revision_arg, *extra, *targets, check=False
            ).stdout
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

    # svn labels files with `Index:` and absolute paths; git uses
    # `diff --git a/x b/x`. Normalising here keeps every code path in this
    # command producing one format, and lets the output round-trip through
    # `git apply`, which strips one leading component by default.
    palette = colour_mod.palette_for(ctx, opts)
    normalised = patch_mod.to_git_headers(diff_text, ctx.wc_root).rstrip("\n")
    ctx.echo(colour_mod.paint_diff(normalised, palette))
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
def _line_range(spec) -> Tuple[int, Optional[int]]:
    """Parse git's `-L <start>,<end>`. Either end may be omitted."""
    if not spec:
        return 1, None
    text = str(spec)
    start, _, end = text.partition(",")
    try:
        first = int(start) if start.strip() else 1
        last = int(end) if end.strip() else None
    except ValueError:
        raise UsageError("git blame -L takes <start>[,<end>] line numbers, got %r" % text)
    return max(first, 1), last


def cmd_blame(ctx, argv: List[str]) -> int:
    opts = parse(argv, flags=["line-porcelain", "porcelain", "s", "w"], values=["L", "revision", "r"])
    refuse("blame", opts, {
        "porcelain": "svngit has no git object ids to emit; use `svn blame --xml` "
                     "for a machine-readable form.",
        "line-porcelain": "svngit has no git object ids to emit; use "
                          "`svn blame --xml` for a machine-readable form.",
    })
    if not opts.paths:
        raise UsageError("git blame <file>")

    wc_path = ctx.to_wc_path(opts.paths[0])
    target = ctx.svn_target(wc_path)
    args = ["blame", target]
    if opts.has("w"):
        args.extend(["-x", "-w"])
    revision = opts.first("revision", "r")
    if revision:
        args.extend(["-r", str(rev_mod.resolve(ctx, str(revision), target))])
    root = ctx.svn.xml(*args)
    if root is None:
        return 1

    first_line, last_line = _line_range(opts.first("L"))

    text = ctx.svn.run("cat", target, check=False).stdout.splitlines()
    entries = list(root.iter("entry"))
    width = len(str(len(entries)))

    for entry in entries:
        number = int(entry.get("line-number") or 0)
        if number < first_line or (last_line is not None and number > last_line):
            continue
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


# ----------------------------------------------------------------------
# shortlog
# ----------------------------------------------------------------------
def cmd_shortlog(ctx, argv: List[str]) -> int:
    """Group the log by author, as `git shortlog` does."""
    opts = parse(
        argv,
        flags=["summary", "s", "numbered", "n", "email", "e", "committer", "c"],
        values=["max-count", "author"],
        allow_numeric=True,
    )
    no_effect(ctx, "shortlog", opts, {
        "committer": "Subversion records one author per revision; there is no "
                     "separate committer to group by.",
        "c": "Subversion records one author per revision; there is no "
             "separate committer to group by.",
    })
    revisions, paths = split_revisions_and_paths(ctx, opts.positionals)
    target = ctx.svn_target(ctx.to_wc_path(paths[0])) if paths else str(ctx.wc_root)

    limit = opts.first("max-count", "n")
    entries = ctx.svn.log(
        target,
        revision=rev_mod.to_svn_range(ctx, revisions[0], target) if revisions else None,
        limit=int(limit) if limit else None,
    )
    if opts.has("author"):
        wanted = str(opts.get("author")).lower()
        entries = [e for e in entries if wanted in e.author.lower()]

    grouped: Dict[str, List[str]] = {}
    for entry in entries:
        subject = entry.message.strip().splitlines()[0] if entry.message.strip() else "(no message)"
        grouped.setdefault(entry.author, []).append(subject)

    uuid = ctx.info.repos_uuid
    order = sorted(
        grouped,
        key=(lambda a: (-len(grouped[a]), a.lower())) if opts.has("numbered", "n")
        else (lambda a: a.lower()),
    )
    for author in order:
        subjects = grouped[author]
        name = formatting.author_line(author, uuid) if opts.has("email", "e") else author
        if opts.has("summary", "s"):
            ctx.echo("%6d\t%s" % (len(subjects), name))
            continue
        ctx.echo("%s (%d):" % (name, len(subjects)))
        for subject in subjects:
            ctx.echo("      %s" % subject)
        ctx.echo("")
    return 0


# ----------------------------------------------------------------------
# describe
# ----------------------------------------------------------------------
def cmd_describe(ctx, argv: List[str]) -> int:
    """Name a revision after the most recent tag that precedes it.

    A Subversion tag is a directory copied from some revision, so "the tag
    this revision descends from" is the newest tag whose copy source is at or
    before it.
    """
    from .. import layout as layout_mod

    opts = parse(
        argv,
        flags=["tags", "all", "always", "dirty", "long", "contains"],
        values=["match", "abbrev", "candidates"],
    )
    refuse("describe", opts, {
        "contains": "that asks which later tag contains a revision; use "
                    "`git tag --contains <rev>` instead.",
    })
    no_effect(ctx, "describe", opts, {
        "abbrev": "a revision number is already its shortest form.",
        "candidates": "tags are compared by the revision they were copied from, "
                      "so the best match is found without a search limit.",
    })

    spec = opts.positionals[0] if opts.positionals else "HEAD"
    target_rev = rev_mod.resolve(ctx, spec, str(ctx.wc_root))
    info, lay = ctx.info, ctx.layout

    best_name, best_rev = None, -1
    for name in layout_mod.list_tags(ctx.svn, info, lay):
        if opts.has("match") and not fnmatch.fnmatch(name, str(opts.get("match"))):
            continue
        created = _tag_source_revision(ctx, info, lay, name)
        if created is None or created > target_rev:
            continue
        if created > best_rev:
            best_name, best_rev = name, created

    if best_name is None:
        if opts.has("always"):
            ctx.echo(formatting.revision_id(target_rev))
            return 0
        raise SvnGitError(
            "no tags can describe %s.\nTry --always, or create a tag first."
            % formatting.revision_id(target_rev)
        )

    distance = _revisions_between(ctx, best_rev, target_rev)
    suffix = _dirty_suffix(ctx) if opts.has("dirty") else ""
    if distance == 0 and not opts.has("long"):
        ctx.echo(best_name + suffix)
    else:
        ctx.echo("%s-%d-%s%s" % (best_name, distance, formatting.revision_id(target_rev), suffix))
    return 0


def _tag_source_revision(ctx, info, lay, name: str) -> Optional[int]:
    from .. import layout as layout_mod

    url = layout_mod.tag_url(info, lay, name)
    entries = ctx.svn.log(url, limit=1, stop_on_copy=True, verbose=True)
    if not entries:
        return None
    for path in entries[0].paths:
        if path.copyfrom_rev is not None:
            return path.copyfrom_rev
    return entries[0].revision


def _revisions_between(ctx, start: int, end: int) -> int:
    """How many revisions touched this branch between two points."""
    if end <= start:
        return 0
    entries = ctx.svn.log(str(ctx.wc_root), revision="%d:%d" % (end, start + 1))
    return len(entries)


def _dirty_suffix(ctx) -> str:
    report = status_mod.compute(ctx)
    return "-dirty" if any(not e.untracked and not e.ignored for e in report.entries) else ""


# ----------------------------------------------------------------------
# whatchanged
# ----------------------------------------------------------------------
def cmd_whatchanged(ctx, argv: List[str]) -> int:
    """git's older spelling of `log --raw`; here, log with the paths shown."""
    return cmd_log(ctx, list(argv) + ["--name-status"])
