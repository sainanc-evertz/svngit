"""The `git add -p` and `git add -e` prompt loops.

The diff shown is index-to-worktree, not BASE-to-worktree, so hunks you have
already staged do not come round again -- and staging is incremental: each
answered hunk is folded into the staged content, which is stored as a blob like
any other staged file. Everything downstream (status's two columns, `git diff`,
`git diff --cached`, commit, push) already reads that blob, so partial staging
needs no special handling anywhere else.

Navigation means a hunk is no longer answered once on the way past: it carries
a decision -- undecided, staged, or skipped -- that can be revisited. Accepted
hunks are therefore collected in *file* order at the end rather than in the
order they were answered, because the patch applier walks forward through the
file with a single cursor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .. import colour as colour_mod
from .. import hunks as hunks_mod
from .. import patch as patch_mod
from ..state import ADD, MODIFY
from ..status import FileStatus

HELP = """\
y - stage this hunk
n - do not stage this hunk
q - quit; do not stage this hunk or any of the remaining ones
a - stage this hunk and all later hunks in the file
d - do not stage this hunk or any of the later hunks in the file
g - select a hunk to go to
/ - search for a hunk matching the given regex
j - leave this hunk undecided, see next undecided hunk
J - leave this hunk undecided, see next hunk
k - leave this hunk undecided, see previous undecided hunk
K - leave this hunk undecided, see previous hunk
s - split the current hunk into smaller hunks
e - manually edit the current hunk
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

QUIT = "q"


def _read_line(ctx) -> Optional[str]:
    """Read one answer, keeping piped output as readable as interactive output.

    A terminal echoes the user's keystroke and newline; a pipe does not, so
    without this the next line of output runs into the prompt.
    """
    line = ctx.stdin.readline()
    if not line:
        return None
    if not getattr(ctx.stdin, "isatty", lambda: False)():
        ctx.stdout.write(line if line.endswith("\n") else line + "\n")
    return line


#: Returned by _edit_hunk when the edit did not apply and should be retried.
_EDIT_RETRY = object()


@dataclass
class Selection:
    """The outcome of walking one file's hunks."""

    accepted: List[patch_mod.PatchHunk]
    rejected: List[patch_mod.PatchHunk]
    quit: bool = False


@dataclass
class _Item:
    """One hunk on screen, and what the user has decided about it."""

    hunk: object
    #: None while undecided; True staged; False skipped.
    decision: Optional[bool] = None
    #: Set when the hunk was accepted through `e`, replacing the original.
    edited: Optional[patch_mod.PatchHunk] = None


# ----------------------------------------------------------------------
# shared
# ----------------------------------------------------------------------
def effective_base(ctx, entry: FileStatus, use_index: bool = True) -> bytes:
    """What the worktree is being compared against.

    In order of precedence: the staged blob (git diffs index to worktree), then
    the newest queued local commit, then the pristine BASE copy. Skipping the
    middle case would re-offer hunks the user has already committed locally.

    `use_index=False` ignores the staging area, which is what stash wants: it
    saves every local change, staged or not.
    """
    staged = ctx.state.index.get(entry.path) if use_index else None
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


def _stage_blob(ctx, entry: FileStatus, content: str) -> None:
    existing = ctx.state.index.get(entry.path)
    blob = ctx.state.objects.write(content.encode("utf-8"))
    action = entry.index if entry.index in (ADD, "R") else MODIFY
    ctx.state.stage(
        entry.path, action, blob, existing.executable if existing else False
    )
    ctx.state.save()


# ----------------------------------------------------------------------
# git add -p
# ----------------------------------------------------------------------
def stage_patch(ctx, entries: List[FileStatus]) -> int:
    """Walk the modified files hunk by hunk. Returns a process exit code."""
    for entry in entries:
        if entry.ignored or entry.unmerged:
            continue
        if entry.untracked:
            ctx.echo(
                "%s is untracked; `git add -p` only splits changes to tracked "
                "files. Use `git add %s` to add the whole file."
                % (ctx.display_path(entry.path), ctx.display_path(entry.path))
            )
            continue
        if _stage_one_file(ctx, entry) is None:
            break  # the user quit
    ctx.state.save()
    return 0


def _stage_one_file(ctx, entry: FileStatus) -> Optional[bool]:
    """Returns True/False for staged-something, or None if the user quit."""
    absolute = ctx.abs_path(entry.path)
    if not absolute.is_file():
        return False

    work_bytes = absolute.read_bytes()
    base_bytes = effective_base(ctx, entry)
    if base_bytes == work_bytes:
        return False

    display = ctx.display_path(entry.path)
    if hunks_mod.is_binary(base_bytes) or hunks_mod.is_binary(work_bytes):
        return _whole_file(ctx, entry, display, work_bytes)

    base_text = base_bytes.decode("utf-8", errors="replace")
    diff = hunks_mod.diff_file(base_text, work_bytes.decode("utf-8", errors="replace"))
    if diff.empty:
        return False

    palette = colour_mod.palette_for(ctx)
    ctx.echo(
        colour_mod.paint_diff_line(
            "diff --git a/%s b/%s" % (entry.path, entry.path), palette
        )
    )
    selection = select_hunks(ctx, diff, base_text, entry.path)

    if selection.accepted:
        content = patch_mod.apply_file_patch(
            base_text, patch_mod.FilePatch(entry.path, selection.accepted)
        )
        _stage_blob(ctx, entry, content)
        insertions = deletions = 0
        for hunk in selection.accepted:
            added, removed = patch_mod.hunk_stats(hunk)
            insertions += added
            deletions += removed
        ctx.echo(
            "Staged %d hunk%s from %s (+%d/-%d)."
            % (
                len(selection.accepted),
                "" if len(selection.accepted) == 1 else "s",
                display,
                insertions,
                deletions,
            )
        )

    return None if selection.quit else bool(selection.accepted)


def select_hunks(
    ctx, diff, base_text: str, path: str, prompt: str = "Stage this hunk"
) -> Selection:
    """Walk one file's hunks, returning what the user chose.

    Shared by `git add -p`, which stages what is accepted, and
    `git stash -p`, which takes it out of the working copy instead.
    """
    palette = colour_mod.palette_for(ctx)
    queue = [_Item(hunk) for hunk in diff.hunks]
    position: Optional[int] = 0
    quit_all = False

    while position is not None and 0 <= position < len(queue):
        item = queue[position]
        for line in diff.render(item.hunk):
            ctx.echo(colour_mod.paint_diff_line(line, palette))

        answer = _ask(
            ctx, position, queue, splittable=item.hunk.splittable, prompt=prompt
        )
        if answer is None or answer == QUIT:
            quit_all = True
            break

        if answer == "y":
            item.decision = True
            position = _next_undecided(queue, position)
        elif answer == "n":
            item.decision = False
            position = _next_undecided(queue, position)
        elif answer == "a":
            for later in queue[position:]:
                later.decision = True
            position = _next_undecided(queue, position)
        elif answer == "d":
            for later in queue[position:]:
                later.decision = False
            position = _next_undecided(queue, position)
        elif answer == "s":
            pieces = diff.split(item.hunk)
            if len(pieces) == 1:
                ctx.echo("Sorry, cannot split this hunk")
                continue
            queue[position : position + 1] = [_Item(piece) for piece in pieces]
            ctx.echo("Split into %d hunks." % len(pieces))
        elif answer == "e":
            edited = _edit_hunk(ctx, diff, item.hunk, path, base_text, queue, position)
            if edited is _EDIT_RETRY or edited is None:
                continue  # stay on this hunk, still undecided
            item.edited = edited
            item.decision = True
            position = _next_undecided(queue, position)
        elif answer in ("j", "J", "k", "K"):
            position = _navigate(ctx, queue, position, answer)
        elif answer == "g":
            position = _goto(ctx, diff, queue, position)
        elif answer == "/":
            position = _search(ctx, diff, queue, position)

    return Selection(
        accepted=_hunks_where(diff, queue, True),
        rejected=_hunks_where(diff, queue, False),
        quit=quit_all,
    )


def _hunks_where(diff, queue: List[_Item], chosen: bool) -> List[patch_mod.PatchHunk]:
    """Hunks in file order, either the chosen ones or everything else.

    Order matters and answer order will not do: navigation lets a later hunk be
    decided first, while the applier walks the file forward exactly once. An
    undecided hunk counts as not chosen.
    """
    out = []
    for item in queue:
        if bool(item.decision) is not chosen:
            continue
        if chosen and item.edited is not None:
            out.append(item.edited)
        else:
            out.append(patch_mod.from_diff_hunk(diff, item.hunk))
    return out


# ----------------------------------------------------------------------
# movement
# ----------------------------------------------------------------------
def _next_undecided(queue: List[_Item], position: int) -> Optional[int]:
    """The next hunk still awaiting a decision, wrapping once; None when done.

    Wrapping is what lets `j` mean "come back to this later" rather than
    "silently drop it".
    """
    for index in range(position + 1, len(queue)):
        if queue[index].decision is None:
            return index
    for index in range(0, position + 1):
        if queue[index].decision is None:
            return index
    return None


def _navigate(ctx, queue: List[_Item], position: int, answer: str) -> int:
    if answer == "J":
        if position + 1 < len(queue):
            return position + 1
        ctx.echo("No next hunk")
    elif answer == "K":
        if position > 0:
            return position - 1
        ctx.echo("No previous hunk")
    elif answer == "j":
        for index in range(position + 1, len(queue)):
            if queue[index].decision is None:
                return index
        ctx.echo("No next undecided hunk")
    elif answer == "k":
        for index in range(position - 1, -1, -1):
            if queue[index].decision is None:
                return index
        ctx.echo("No previous undecided hunk")
    return position


def _goto(ctx, diff, queue: List[_Item], position: int) -> int:
    if len(queue) < 2:
        ctx.echo("Only one hunk to go to")
        return position

    palette = colour_mod.palette_for(ctx)
    for index, item in enumerate(queue, start=1):
        ctx.echo(
            colour_mod.paint_diff_line(
                "%s%3d: %s" % (_mark(item), index, _summary(diff, item)), palette
            )
        )

    ctx.stdout.write("go to which hunk? ")
    ctx.stdout.flush()
    line = _read_line(ctx)
    if not line or not line.strip():
        return position
    raw = line.strip()
    if not raw.isdigit() or not 1 <= int(raw) <= len(queue):
        ctx.echo("Invalid number: '%s'" % raw)
        return position
    return int(raw) - 1


def _mark(item: _Item) -> str:
    """The decision column in the `g` listing: staged, skipped, or pending."""
    if item.decision is True:
        return "+"
    if item.decision is False:
        return "-"
    return " "


def _summary(diff, item: _Item) -> str:
    """The hunk's header plus its first changed line, for the `g` listing."""
    rendered = diff.render(item.hunk)
    preview = next(
        (line for line in rendered if line.startswith("+")),
        next((line for line in rendered if line.startswith("-")), ""),
    )
    return "%-18s %s" % (item.hunk.header.strip("@ "), preview)


def _search(ctx, diff, queue: List[_Item], position: int) -> int:
    ctx.stdout.write("search for regex? ")
    ctx.stdout.flush()
    line = _read_line(ctx)
    if not line or not line.strip():
        return position
    try:
        pattern = re.compile(line.strip(), re.MULTILINE)
    except re.error as exc:
        ctx.echo("Malformed search regexp %s: %s" % (line.strip(), exc))
        return position

    # Forward from here, then wrap, so repeated searches walk the file.
    order = list(range(position + 1, len(queue))) + list(range(0, position + 1))
    for index in order:
        if pattern.search("\n".join(diff.render(queue[index].hunk))):
            return index
    ctx.echo("No hunk matches the given pattern")
    return position


# ----------------------------------------------------------------------
# per-hunk edit
# ----------------------------------------------------------------------
def _edit_hunk(ctx, diff, hunk, path, base_text, queue, position):
    """`e`: edit one hunk in the editor and validate it before accepting.

    Validation trial-applies every staged hunk *in file order* with this
    candidate in its own slot, so a hunk that cannot land is caught while the
    user is still here to fix it.
    """
    from .. import editor as editor_mod

    original = "\n".join(patch_mod.render_hunk(diff, hunk)) + "\n"
    edited = editor_mod.edit_text(
        ctx, original + HUNK_EDIT_NOTES, suffix=".diff", what="hunk"
    )

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

    trial: List[patch_mod.PatchHunk] = []
    for index, item in enumerate(queue):
        if index == position:
            trial.append(candidate)
        elif item.decision:
            trial.append(
                item.edited
                if item.edited is not None
                else patch_mod.from_diff_hunk(diff, item.hunk)
            )
    try:
        patch_mod.apply_file_patch(base_text, patch_mod.FilePatch(path, trial))
    except patch_mod.PatchError:
        ctx.warn("Your edited hunk does not apply. Edit again.")
        return _EDIT_RETRY
    return candidate


# ----------------------------------------------------------------------
# prompting
# ----------------------------------------------------------------------
def _whole_file(
    ctx, entry: FileStatus, display: str, work_bytes: bytes
) -> Optional[bool]:
    """Binary files have no hunks to choose between: all or nothing."""
    ctx.echo("diff --git a/%s b/%s" % (entry.path, entry.path))
    ctx.echo("Binary file %s cannot be split into hunks." % display)
    answer = _ask(ctx, 0, [], prompt="Stage this file")
    if answer is None or answer == QUIT:
        return None
    if answer in ("y", "a"):
        blob = ctx.state.objects.write(work_bytes)
        ctx.state.stage(entry.path, entry.index if entry.index == ADD else MODIFY, blob)
        ctx.state.save()
        return True
    return False


def _available(position: int, queue: List[_Item], splittable: bool) -> List[str]:
    """Which answers this hunk actually offers, in git's display order.

    Offering `k` at the first hunk, or `j` at the last, would be a prompt that
    lies about what it can do.
    """
    letters = ["y", "n", "q", "a", "d"]
    if len(queue) > 1:
        letters.append("/")
    if any(item.decision is None for item in queue[position + 1 :]):
        letters.append("j")
    if position + 1 < len(queue):
        letters.append("J")
    if any(item.decision is None for item in queue[:position]):
        letters.append("k")
    if position > 0:
        letters.append("K")
    if len(queue) > 1:
        letters.append("g")
    if splittable:
        letters.append("s")
    if queue:
        letters.append("e")
    letters.append("?")
    return letters


def _ask(
    ctx,
    position: int,
    queue: List[_Item],
    splittable: bool = False,
    prompt: str = "Stage this hunk",
) -> Optional[str]:
    """Read one answer. None means end of input, which git treats as quit.

    Case is significant here -- `j` and `J` differ -- so only the
    case-insensitive answers are folded.
    """
    letters = _available(position, queue, splittable)
    total = len(queue) if queue else 1
    header = "(%d/%d) %s [%s]? " % (position + 1, total, prompt, ",".join(letters))

    while True:
        ctx.stdout.write(header)
        ctx.stdout.flush()
        line = _read_line(ctx)
        if not line:
            ctx.echo("")
            return None
        raw = line.strip()
        answer = raw if raw in ("J", "K") else raw.lower()
        if answer == "?":
            ctx.echo(HELP)  # help is printed here, never handed to the caller
            continue
        if answer in letters:
            return answer
        if answer == "s":
            ctx.echo("Sorry, cannot split this hunk")
            continue
        if answer == "e":
            ctx.echo("Sorry, cannot edit this hunk")
            continue
        if answer in ("j", "J"):
            ctx.echo("No next hunk")
            continue
        if answer in ("k", "K"):
            ctx.echo("No previous hunk")
            continue
        ctx.echo(HELP)


# ----------------------------------------------------------------------
# git add -e
# ----------------------------------------------------------------------
def stage_edit(ctx, entries: List[FileStatus]) -> int:
    """`git add -e`: hand the whole diff to the editor, stage what comes back."""
    from .. import editor as editor_mod

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
            ctx.echo(
                "%s is binary; skipping (no patch to edit)."
                % ctx.display_path(entry.path)
            )
            continue

        base = base_bytes.decode("utf-8", errors="replace")
        rendered = patch_mod.render_file_patch(
            entry.path, base, work_bytes.decode("utf-8", errors="replace")
        )
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
            ctx.warn(
                "fatal: the edited patch refers to an unknown file: %s"
                % file_patch.path
            )
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
        _stage_blob(ctx, entry, content)
        ctx.echo("Staged the edited patch for %s." % ctx.display_path(entry.path))
    return 0
