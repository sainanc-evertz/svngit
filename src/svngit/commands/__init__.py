"""Command registry.

`ALIASES` covers git's own short forms plus the ones almost everyone has in
their gitconfig, so muscle memory keeps working.
"""

from __future__ import annotations

from typing import Callable, Dict, NamedTuple, Optional

from . import (
    branching,
    history,
    patches,
    plumbing,
    search,
    stash,
    sync,
    tools,
    workspace,
)


class Command(NamedTuple):
    run: Callable
    #: False for commands that create a working copy rather than act on one.
    needs_working_copy: bool = True


REGISTRY: Dict[str, Command] = {
    # workspace
    "clone": Command(workspace.cmd_clone, needs_working_copy=False),
    "init": Command(workspace.cmd_init, needs_working_copy=False),
    "status": Command(workspace.cmd_status),
    "add": Command(workspace.cmd_add),
    "rm": Command(workspace.cmd_rm),
    "mv": Command(workspace.cmd_mv),
    "reset": Command(workspace.cmd_reset),
    "restore": Command(workspace.cmd_restore),
    "clean": Command(workspace.cmd_clean),
    # history
    "log": Command(history.cmd_log),
    "show": Command(history.cmd_show),
    "diff": Command(history.cmd_diff),
    "blame": Command(history.cmd_blame),
    "shortlog": Command(history.cmd_shortlog),
    "describe": Command(history.cmd_describe),
    "whatchanged": Command(history.cmd_whatchanged),
    "grep": Command(search.cmd_grep),
    "check-ignore": Command(search.cmd_check_ignore),
    "apply": Command(patches.cmd_apply),
    "format-patch": Command(patches.cmd_format_patch),
    "archive": Command(patches.cmd_archive),
    "difftool": Command(tools.cmd_difftool),
    "mergetool": Command(tools.cmd_mergetool),
    # sharing
    "commit": Command(sync.cmd_commit),
    "push": Command(sync.cmd_push),
    "pull": Command(sync.cmd_pull),
    "fetch": Command(sync.cmd_fetch),
    # branching
    "branch": Command(branching.cmd_branch),
    "checkout": Command(branching.cmd_checkout),
    "switch": Command(branching.cmd_switch),
    "merge": Command(branching.cmd_merge),
    "cherry-pick": Command(branching.cmd_cherry_pick),
    "revert": Command(branching.cmd_revert),
    "tag": Command(branching.cmd_tag),
    "stash": Command(stash.cmd_stash),
    "sparse-checkout": Command(workspace.cmd_sparse_checkout),
    # plumbing
    "config": Command(plumbing.cmd_config),
    "remote": Command(plumbing.cmd_remote),
    "rev-parse": Command(plumbing.cmd_rev_parse),
    "ls-files": Command(plumbing.cmd_ls_files),
    "help": Command(plumbing.cmd_help, needs_working_copy=False),
    "version": Command(plumbing.cmd_version, needs_working_copy=False),
}

ALIASES = {
    "co": "checkout",
    "ci": "commit",
    "st": "status",
    "br": "branch",
    "df": "diff",
    "lg": "log",
    "cp": "cherry-pick",
    "unstage": "reset",
    "annotate": "blame",
    "praise": "blame",
    "stage": "add",
    "checkout-index": "checkout",
}

#: Commands that exist in git, have no Subversion equivalent, and are common
#: enough that a bare "unknown command" would be unhelpful.
NO_EQUIVALENT = {
    "rebase": (
        "Subversion has no way to rewrite history. `git pull` already replays "
        "your local changes on top of the server's, which is what a rebase "
        "onto the upstream would do."
    ),
    "bisect": (
        "there is no built-in bisect. You can step through revisions manually "
        "with `git checkout` and `svn update -r <rev>`."
    ),
    "submodule": (
        "the closest Subversion feature is externals: see `svn propedit "
        "svn:externals .`."
    ),
    "worktree": (
        "Subversion has no linked worktrees; make a second checkout with "
        "`git clone <url> <dir>` instead."
    ),
    "reflog": (
        "Subversion keeps no local history of where your working copy has "
        "been. `git log` shows the repository's history instead."
    ),
    "gc": "Subversion working copies need no garbage collection.",
    "am": (
        "there is no patch queue to replay onto. Apply a single patch with "
        "`git apply <file>`, then commit it."
    ),
    "notes": "Subversion has no commit notes.",
    "bundle": (
        "there is no pack format to hand around. To move history offline, use "
        "`svnadmin dump` on the server and `svnadmin load` at the far end; for "
        "a single change, `git format-patch` and `git apply`."
    ),
    "range-diff": (
        "comparing two versions of a patch series needs rewritable history. "
        "Compare two revisions directly with `git diff rA..rB`."
    ),
    "rerere": (
        "Subversion records no conflict resolutions to reuse. `svn resolve` "
        "handles one conflict at a time; see `git mergetool`."
    ),
    "filter-branch": "Subversion history cannot be rewritten in place.",
    "fast-export": (
        "to move a repository elsewhere, dump it with `svnadmin dump`, or use "
        "`git svn clone` to convert it to git."
    ),
    "fast-import": "to load history into Subversion, use `svnadmin load`.",
    "maintenance": "a Subversion working copy needs no scheduled upkeep.",
    "scalar": "Subversion has no equivalent large-repository tooling.",
    "backfill": "a working copy holds no partial history to backfill.",
    "count-objects": "there is no local object store to measure.",
    "fsck": (
        "a working copy stores no object graph to verify. The server side is "
        "`svnadmin verify`."
    ),
    "replace": "Subversion has no object graph in which to substitute a commit.",
    "gitk": "svngit ships no GUI; `git log` and `git show` are the views it has.",
    "gui": "svngit ships no GUI; try TortoiseSVN or RabbitVCS.",
    "citool": "svngit ships no GUI; use `git commit`.",
    "instaweb": "svngit ships no web interface; ViewVC serves Subversion.",
}


#: Options accepted because svngit already behaves that way, so honouring them
#: means doing nothing. Keyed by (function, option). Listed explicitly rather
#: than left to a comment, because `test_options.py` walks every declared
#: option and fails on any that is neither read, refused, reported as having
#: no effect, nor named here -- which is how a silently-ignored flag gets in.
DEFAULT_BEHAVIOUR = {
    (
        "cmd_branch",
        "a",
    ): "every branch is on the server, so the default listing is already --all",
    (
        "cmd_branch",
        "all",
    ): "every branch is on the server, so the default listing is already --all",
    ("cmd_revert", "no-edit"): "svngit never opens an editor for a revert",
    ("cmd_tag", "a"): "every Subversion tag is a copy made with a log message",
    ("cmd_tag", "annotate"): "every Subversion tag is a copy made with a log message",
    ("cmd_log", "abbrev-commit"): "a revision number is already its shortest form",
    ("cmd_log", "follow"): "svn log traverses copies unless --stop-on-copy is given",
    ("cmd_config", "local"): "per-working-copy is the only scope svngit has",
    ("cmd_rev_parse", "short"): "a revision number is already its shortest form",
    ("cmd_ls_files", "c"): "listing the tracked files is the default",
    ("cmd_ls_files", "cached"): "listing the tracked files is the default",
    ("cmd_status", "long"): "the long format is the default",
    ("cmd_reset", "mixed"): "--mixed is the default reset mode",
    ("cmd_grep", "E"): "Python's regex dialect is a superset of POSIX extended",
    (
        "cmd_grep",
        "extended-regexp",
    ): "Python's regex dialect is a superset of POSIX extended",
    ("cmd_grep", "G"): "Python's regex dialect is a superset of POSIX basic",
    ("cmd_grep", "basic-regexp"): "Python's regex dialect is a superset of POSIX basic",
    (
        "cmd_describe",
        "tags",
    ): "Subversion tags are the only thing svngit can describe from",
    ("cmd_format_patch", "numbered"): "output files are always numbered",
    ("cmd_format_patch", "n"): "-n is --numbered, which is already the only behaviour",
    ("cmd_difftool", "prompt"): "prompting is the default; --no-prompt turns it off",
    ("cmd_mergetool", "prompt"): "prompting is the default; --no-prompt turns it off",
    (
        "_sparse_init",
        "cone",
    ): "Subversion excludes directories, so cone mode is the only one",
    (
        "_sparse_apply",
        "cone",
    ): "Subversion excludes directories, so cone mode is the only one",
}


def resolve(name: str) -> Optional[Command]:
    return REGISTRY.get(ALIASES.get(name, name))
