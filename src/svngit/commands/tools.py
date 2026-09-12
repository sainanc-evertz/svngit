"""difftool and mergetool -- handing files to an external program.

Subversion keeps conflict state as files on disk (`name.mine`, `name.rOLD`,
`name.rNEW`) rather than as index stages, so `mergetool` feeds those three to
the tool and then tells Subversion the conflict is resolved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence, Tuple

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from .. import status as status_mod
from ..cliargs import no_effect, parse
from ..errors import SvnGitError

if TYPE_CHECKING:  # pragma: no cover
    from ..context import Context

#: Tried in order when no tool is configured.
DIFF_TOOLS = ("difft", "delta", "colordiff", "diff")
MERGE_TOOLS = ("nvim", "vimdiff", "kdiff3", "meld", "opendiff", "diff3")


def _pick(configured: Optional[str], candidates: Sequence[str]) -> Optional[str]:
    if configured:
        return str(configured)
    for name in candidates:
        if shutil.which(name):
            return name
    return None


# ----------------------------------------------------------------------
# difftool
# ----------------------------------------------------------------------
def cmd_difftool(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=[
            "no-prompt",
            "y",
            "prompt",
            "dir-diff",
            "d",
            "cached",
            "staged",
            "gui",
            "g",
        ],
        values=["tool", "t", "extcmd"],
    )
    no_effect(
        ctx,
        "difftool",
        opts,
        {
            "dir-diff": "svn diff hands the tool one file at a time.",
            "d": "svn diff hands the tool one file at a time.",
            "gui": "svngit does not keep a separate GUI tool setting; use --tool.",
            "g": "svngit does not keep a separate GUI tool setting; use --tool.",
        },
    )
    # --prompt is the default; --no-prompt/-y is what changes it.
    tool = _pick(
        opts.first("tool", "t", "extcmd") or ctx.state.get_config("diff.tool"),
        DIFF_TOOLS,
    )
    if tool is None:
        raise SvnGitError(
            "no diff tool found. Set one with `git config diff.tool <program>`."
        )

    paths = ctx.to_wc_paths(opts.paths) if opts.paths else None
    targets = [ctx.svn_target(p) for p in paths] if paths else [str(ctx.wc_root)]

    args = ["diff", "--diff-cmd", tool]
    if opts.has("cached", "staged"):
        ctx.note(
            "--cached compares the staging area, which Subversion has no view "
            "of; showing the working copy against BASE instead"
        )
    result = ctx.svn.run(*args, *targets, check=False, capture=False)
    return 0 if result.ok else 1


# ----------------------------------------------------------------------
# mergetool
# ----------------------------------------------------------------------
def cmd_mergetool(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["no-prompt", "y", "prompt", "gui", "g"],
        values=["tool", "t"],
    )
    no_effect(
        ctx,
        "mergetool",
        opts,
        {
            "gui": "svngit does not keep a separate GUI tool setting; use --tool.",
            "g": "svngit does not keep a separate GUI tool setting; use --tool.",
        },
    )
    # --prompt is the default; --no-prompt/-y is what changes it.
    conflicts = [e for e in status_mod.compute(ctx).entries if e.unmerged]
    if opts.paths:
        wanted = set(ctx.to_wc_paths(opts.paths))
        conflicts = [e for e in conflicts if e.path in wanted]

    if not conflicts:
        ctx.echo("No files need merging")
        return 0

    tool = _pick(
        opts.first("tool", "t") or ctx.state.get_config("merge.tool"), MERGE_TOOLS
    )
    if tool is None:
        raise SvnGitError(
            "no merge tool found. Set one with `git config merge.tool <program>`, "
            "or resolve by hand and run `git add <file>`."
        )

    resolved = 0
    for entry in conflicts:
        absolute = ctx.abs_path(entry.path)
        mine, older, newer = _conflict_files(absolute)
        if mine is None:
            ctx.warn(
                "%s is conflicted but Subversion left no conflict files; "
                "resolve it by hand and run `git add %s`."
                % (ctx.display_path(entry.path), ctx.display_path(entry.path))
            )
            continue

        ctx.echo("Merging %s" % ctx.display_path(entry.path))
        if not opts.has("no-prompt", "y") and not _confirm(ctx, tool, entry.path):
            continue

        command = [tool, str(older), str(mine), str(newer), str(absolute)]
        try:
            subprocess.run(command, check=False)
        except OSError as exc:
            raise SvnGitError("could not run %s: %s" % (tool, exc))

        # Whatever the tool left in the working file is the resolution, which
        # is what `svn resolve --accept working` records.
        ctx.svn.run(
            "resolve", "--accept", "working", ctx.svn_target(entry.path), mutating=True
        )
        resolved += 1

    if resolved:
        ctx.echo(
            "%d conflict%s resolved. Stage them with `git add`, then commit."
            % (resolved, "" if resolved == 1 else "s")
        )
    return 0


def _conflict_files(
    absolute: Path,
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """Subversion's three conflict artefacts beside the file, if present."""
    parent, stem = absolute.parent, absolute.name
    mine = parent / ("%s.mine" % stem)
    revisions = sorted(parent.glob("%s.r*" % stem))
    if not mine.is_file() or len(revisions) < 2:
        return None, None, None
    return mine, revisions[0], revisions[-1]


def _confirm(ctx: "Context", tool: str, path: str) -> bool:
    ctx.stdout.write("Launch '%s' [Y/n]? " % tool)
    ctx.stdout.flush()
    line = ctx.stdin.readline()
    if not getattr(ctx.stdin, "isatty", lambda: False)():
        ctx.stdout.write(line if line.endswith("\n") else line + "\n")
    return not line.strip().lower().startswith("n")
