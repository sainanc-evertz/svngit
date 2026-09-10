"""Command registry.

`ALIASES` covers git's own short forms plus the ones almost everyone has in
their gitconfig, so muscle memory keeps working.
"""

from __future__ import annotations

from typing import Callable, Dict, NamedTuple, Optional

from . import branching, history, plumbing, stash, sync, workspace


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
    "am": "there is no patch queue; apply patches with `svn patch`.",
    "notes": "Subversion has no commit notes.",
}


def resolve(name: str) -> Optional[Command]:
    return REGISTRY.get(ALIASES.get(name, name))
