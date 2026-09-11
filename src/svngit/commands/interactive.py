"""The `git add -p` prompt loop.

The diff shown is index-to-worktree, not BASE-to-worktree, so hunks you have
already staged do not come round again -- and staging is incremental: each
answered hunk is folded into the staged content, which is stored as a blob like
any other staged file. Everything downstream (status's two columns, `git diff`,
`git diff --cached`, commit, push) already reads that blob, so partial staging
needs no special handling anywhere else.
"""

from __future__ import annotations

from typing import List, Optional

from .. import hunks as hunks_mod
from .. import patch as patch_mod
from ..state import ADD, MODIFY
from ..status import FileStatus

HELP = """\
y - stage this hunk
n - do not stage this hunk
a - stage this hunk and all later hunks in the file
d - do not stage this hunk or any later hunk in the file
s - split the current hunk into smaller hunks
e - manually edit the current hunk
q - quit; do not stage this hunk or any remaining ones
? - print this help"""

HUNK_EDIT_NOTES = """\
#
# ---
# To remove '-' lines, make them ' ' lines (context).
# To remove '+' lines, delete them.
# Lines starting with '#' will be removed.
#
# If the patch applies cleanly, the edited hunk is staged immediately.
# If it does not apply cleanly, you get to edit it again.
# Removing every line aborts the edit and leaves the hunk unstaged.
"""

#: Answers that end the whole session rather than just this hunk.
QUIT = "q"


def stage_patch(ctx, entries: List[FileStatus]) -> int:
    """Walk the modified files hunk by hunk. Returns a process exit code."""
    candidates = [e for e in entries if not e.ignored and not e.unmerged]
    staged_any = False

    for entry in candidates:
        if entry.untracked:
            ctx.echo(
                "%s is untracked; `git add -p` only splits changes to tracked "
                "files. Use `git add %s` to add the whole file."
                % (ctx.display_path(entry.path), ctx.display_path(entry.path))
            )
            continue

        outcome = _stage_one_file(ctx, entry)
        if outcome is None:
            return 0 if staged_any else 0  # user quit
        staged_any = staged_any or outcome

    ctx.state.save()
    return 0


def effective_base(ctx, entry: FileStatus) -> bytes:
    """What the worktree is being compared against.

    In order of precedence: the staged blob (git diffs index to worktree), then
    the newest queued local commit, then the pristine BASE copy. Skipping the
    middle case would re-offer hunks the user has already committed locally.
    """
    staged = ctx.state.index.get(entry.path)
    if staged is not None and staged.blob is not None:
        return ctx.state.objects.read(staged.blob)

    queued = ctx.state.queued_blobs()
    if entry.path in queued:
        blob = queued[entry.path]
        return ctx.state.objects.read(blob) if blob else b""

    if entry.index == ADD:
        return b""  # scheduled add with nothing committed yet: all new
    return ctx.svn.run(
        "cat", "-r", "BASE", ctx.svn_target(entry.path), check=False
    ).stdout.encode("utf-8")


def _stage_one_file(ctx, entry: FileStatus) -> Optional[bool]:
    """Returns True/False for staged-something, or None if the user quit."""
    absolute = ctx.abs_path(entry.path)
    if not absolute.is_file():
        return False

    work_bytes = absolute.read_bytes()
    base_bytes = effective_base(ctx, entry)
    staged = ctx.state.index.get(entry.path)

    if base_bytes == work_bytes:
        return False

    display = ctx.display_path(entry.path)
    if hunks_mod.is_binary(base_bytes) or hunks_mod.is_binary(work_bytes):
        return _whole_file(ctx, entry, display, work_bytes)

    diff = hunks_mod.diff_file(
        base_bytes.decode("utf-8", errors="replace"),
        work_bytes.decode("utf-8", errors="replace"),
    )
    if diff.empty:
        return False

    ctx.echo("diff --git a/%s b/%s" % (entry.path, entry.path))

    base_text = base_bytes.decode("utf-8", errors="replace")
    accepted: List[patch_mod.PatchHunk] = []
    queue = list(diff.hunks)
    position = 0
    quit_all = False

    while position < len(queue):
        hunk = queue[position]
        for line in diff.render(hunk):
            ctx.echo(line)

        answer = _ask(ctx, position + 1, len(queue), hunk.splittable)
        if answer is None or answer == QUIT:
            quit_all = answer == QUIT or answer is None
            break
        if answer == "y":
            accepted.append(patch_mod.from_diff_hunk(diff, hunk))
        elif answer == "a":
            accepted.extend(patch_mod.from_diff_hunk(diff, h) for h in queue[position:])
            break
        elif answer == "d":
            break
        elif answer == "s":
            pieces = diff.split(hunk)
            if len(pieces) == 1:
                ctx.echo("Sorry, cannot split this hunk")
                continue
            queue[position : position + 1] = pieces
            ctx.echo("Split into %d hunks." % len(pieces))
            continue
        elif answer == "e":
            edited = _edit_hunk(ctx, diff, hunk, entry.path, base_text, accepted)
            if edited is _EDIT_RETRY:
                continue  # does not apply; stay on this hunk
            if edited is not None:
                accepted.append(edited)
        position += 1

    if accepted:
        content = patch_mod.apply_file_patch(
            base_text, patch_mod.FilePatch(entry.path, accepted)
        )
        blob = ctx.state.objects.write(content.encode("utf-8"))
        action = entry.index if entry.index in (ADD, "R") else MODIFY
        ctx.state.stage(entry.path, action, blob, staged.executable if staged else False)
        ctx.state.save()
        insertions = deletions = 0
        for item in accepted:
            added, removed = patch_mod.hunk_stats(item)
            insertions += added
            deletions += removed
        ctx.echo(
            "Staged %d hunk%s from %s (+%d/-%d)."
            % (len(accepted), "" if len(accepted) == 1 else "s", display, insertions, deletions)
        )

    return None if quit_all else bool(accepted)


#: Returned by _edit_hunk when the edit did not apply and should be retried.
_EDIT_RETRY = object()


def _edit_hunk(ctx, diff, hunk, path, base_text, accepted):
    """`e`: edit one hunk in the editor and validate it before accepting.

    Validation is a trial application of everything accepted so far plus this
    hunk, so a hunk that cannot land is caught while the user is still here to
    fix it -- which is what git means by "you get to edit it again".
    """
    from .. import editor as editor_mod

    original = "\n".join(patch_mod.render_hunk(diff, hunk)) + "\n"
    edited = editor_mod.edit_text(ctx, original + HUNK_EDIT_NOTES, suffix=".diff", what="hunk")

    try:
        patches = patch_mod.parse_patch(edited, default_path=path)
    except patch_mod.PatchError as exc:
        ctx.warn("Your edited hunk could not be read: %s" % exc)
        return _EDIT_RETRY

    candidate = patches[0].hunks[0] if patches and patches[0].hunks else None
    if candidate is None or not (candidate.old_lines or candidate.new_lines):
        # git: removing every line aborts the edit and leaves the hunk alone.
        ctx.echo("Edit aborted; the hunk was left unstaged.")
        return None
    try:
        patch_mod.apply_file_patch(base_text, patch_mod.FilePatch(path, accepted + [candidate]))
    except patch_mod.PatchError:
        ctx.warn("Your edited hunk does not apply. Edit again.")
        return _EDIT_RETRY
    return candidate


def _whole_file(ctx, entry: FileStatus, display: str, work_bytes: bytes) -> Optional[bool]:
    """Binary files have no hunks to choose between: all or nothing."""
    ctx.echo("diff --git a/%s b/%s" % (entry.path, entry.path))
    ctx.echo("Binary file %s cannot be split into hunks." % display)
    answer = _ask(ctx, 1, 1, splittable=False, prompt="Stage this file", editable=False)
    if answer is None or answer == QUIT:
        return None
    if answer in ("y", "a"):
        blob = ctx.state.objects.write(work_bytes)
        ctx.state.stage(entry.path, entry.index if entry.index == ADD else MODIFY, blob)
        ctx.state.save()
        return True
    return False


def _ask(
    ctx,
    index: int,
    total: int,
    splittable: bool,
    prompt: str = "Stage this hunk",
    editable: bool = True,
) -> Optional[str]:
    """Read one answer. None means end of input, which git treats as quit."""
    choices = "y,n,q,a,d" + (",s" if splittable else "") + (",e" if editable else "") + ",?"
    while True:
        ctx.stdout.write("(%d/%d) %s [%s]? " % (index, total, prompt, choices))
        ctx.stdout.flush()
        line = ctx.stdin.readline()
        if not line:
            ctx.echo("")
            return None
        answer = line.strip().lower()
        if answer in ("y", "n", "q", "a", "d") or (answer == "s" and splittable) or (answer == "e" and editable):
            return answer
        if answer == "s":
            ctx.echo("Sorry, cannot split this hunk")
            continue
        if answer == "e":
            ctx.echo("Sorry, cannot edit this hunk")
            continue
        ctx.echo(HELP)


# ----------------------------------------------------------------------
# git add -e
# ----------------------------------------------------------------------
INSTRUCTIONS = """\
# Editing the patch to be staged.
#
#   To drop an added line,    delete the '+' line.
#   To keep a removed line,   change its '-' to a space (make it context).
#   To change what is staged, edit the '+' line's text.
#   Lines starting with '#' are ignored, as is anything you delete entirely.
#
# Hunk headers are recalculated, so the @@ line counts do not need fixing.
# An empty patch, or quitting without saving, stages nothing.
# If the patch does not apply, nothing at all is staged.
"""


def stage_edit(ctx, entries: List[FileStatus]) -> int:
    """`git add -e`: hand the whole diff to the editor, stage what comes back."""
    from .. import editor as editor_mod
    from .. import patch as patch_mod

    sources = {}
    lines: List[str] = []
    for entry in entries:
        if entry.ignored or entry.unmerged:
            continue
        if entry.untracked:
            ctx.echo(
                "%s is untracked and has no diff to edit; use `git add %s`."
                % (ctx.display_path(entry.path), ctx.display_path(entry.path))
            )
            continue
        absolute = ctx.abs_path(entry.path)
        if not absolute.is_file():
            continue

        base_bytes = effective_base(ctx, entry)
        work_bytes = absolute.read_bytes()
        if base_bytes == work_bytes:
            continue
        if hunks_mod.is_binary(base_bytes) or hunks_mod.is_binary(work_bytes):
            ctx.echo("%s is binary; skipping (no patch to edit)." % ctx.display_path(entry.path))
            continue

        base = base_bytes.decode("utf-8", errors="replace")
        rendered = patch_mod.render_file_patch(entry.path, base, work_bytes.decode("utf-8", errors="replace"))
        if rendered:
            sources[entry.path] = (entry, base)
            lines.extend(rendered)

    if not lines:
        ctx.echo("No changes.")
        return 0

    edited = editor_mod.edit_text(
        ctx, INSTRUCTIONS + "\n".join(lines) + "\n", suffix=".diff", what="patch"
    )

    try:
        patches = patch_mod.parse_patch(edited)
    except patch_mod.PatchError as exc:
        ctx.warn("fatal: %s" % exc)
        return 1

    if not patches:
        ctx.echo("No changes.")
        return 0

    # Apply everything before staging anything: a patch that fails partway
    # must not leave half the files staged.
    staged = []
    for file_patch in patches:
        if file_patch.path not in sources:
            ctx.warn("fatal: the edited patch refers to an unknown file: %s" % file_patch.path)
            return 1
        entry, base = sources[file_patch.path]
        try:
            content = patch_mod.apply_file_patch(base, file_patch)
        except patch_mod.PatchError as exc:
            ctx.warn("fatal: %s" % exc)
            ctx.warn("Nothing was staged.")
            return 1
        if content != base:
            staged.append((entry, content))

    if not staged:
        ctx.echo("No changes.")
        return 0

    for entry, content in staged:
        existing = ctx.state.index.get(entry.path)
        blob = ctx.state.objects.write(content.encode("utf-8"))
        action = entry.index if entry.index in (ADD, "R") else MODIFY
        ctx.state.stage(entry.path, action, blob, existing.executable if existing else False)
        ctx.echo("Staged the edited patch for %s." % ctx.display_path(entry.path))
    ctx.state.save()
    return 0
