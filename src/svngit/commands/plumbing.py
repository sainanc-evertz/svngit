"""config, remote, rev-parse, ls-files, help, version."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing import List

from .. import formatting
from ..cliargs import Options, parse, refuse
from ..errors import SvnGitError, UsageError
from .. import revisions as rev_mod

if TYPE_CHECKING:  # pragma: no cover
    from ..context import Context


# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------
def cmd_config(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=["list", "l", "global", "local", "system", "get", "bool", "int"],
        values=["unset", "add", "get-regexp"],
    )
    refuse(
        "config",
        opts,
        {
            "add": "svngit config keys hold a single value, so there is nothing to "
            "append to. Set the key instead.",
        },
    )
    if opts.has("global") or opts.has("system"):
        ctx.note("svngit config is stored per working copy; --global is ignored")
    # --local is the only scope there is, so it needs no handling.

    if opts.has("list", "l"):
        for key, value in sorted(ctx.state.config.items()):
            ctx.echo("%s=%s" % (key, value))
        return 0

    pattern = opts.first("get-regexp")
    if pattern:
        import re

        try:
            matcher = re.compile(str(pattern))
        except re.error as exc:
            raise UsageError("invalid regexp %s: %s" % (pattern, exc))
        found = False
        for key, value in sorted(ctx.state.config.items()):
            if matcher.search(key):
                ctx.echo("%s %s" % (key, value))
                found = True
        return 0 if found else 1

    unset = opts.first("unset")
    if unset:
        if not ctx.state.unset_config(str(unset)):
            return 5  # git's exit code for "key not found"
        ctx.state.save()
        return 0

    if not opts.positionals:
        raise UsageError("git config <key> [<value>]")

    key = opts.positionals[0]
    if len(opts.positionals) == 1 or opts.has("get"):
        value = ctx.state.get_config(key)
        if value is None:
            return 1
        ctx.echo(_typed(str(value), opts))
        return 0

    ctx.state.set_config(key, opts.positionals[1])
    ctx.state.save()
    return 0


def _typed(value: str, opts: Options) -> str:
    """Apply git's --bool / --int canonicalisation to a config value."""
    if opts.has("bool"):
        return (
            "true"
            if value.strip().lower() in ("true", "yes", "on", "1", "")
            else "false"
        )
    if opts.has("int"):
        try:
            return str(int(value.strip()))
        except ValueError:
            raise UsageError("bad numeric config value %r" % value)
    return value


# ----------------------------------------------------------------------
# remote
# ----------------------------------------------------------------------
def cmd_remote(ctx: "Context", argv: List[str]) -> int:
    opts = parse(argv, flags=["verbose", "v"])
    subcommand = opts.positionals[0] if opts.positionals else None

    if subcommand in ("add", "remove", "rm", "rename", "set-url"):
        raise SvnGitError(
            "a Subversion working copy is bound to exactly one repository URL, "
            "so remotes cannot be added or renamed.\n"
            "To point this checkout somewhere else, use `git checkout <branch>` "
            "or re-clone."
        )

    if subcommand == "show":
        info = ctx.info
        ctx.echo("* remote origin")
        ctx.echo("  Fetch URL: %s" % info.url)
        ctx.echo("  Push  URL: %s" % info.url)
        ctx.echo("  Repository root: %s" % info.repos_root)
        ctx.echo("  HEAD branch: %s" % ctx.branch)
        return 0

    if opts.has("verbose", "v"):
        url = ctx.info.url
        ctx.echo("origin\t%s (fetch)" % url)
        ctx.echo("origin\t%s (push)" % url)
    else:
        ctx.echo("origin")
    return 0


# ----------------------------------------------------------------------
# rev-parse
# ----------------------------------------------------------------------
def cmd_rev_parse(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=[
            "abbrev-ref",
            "show-toplevel",
            "git-dir",
            "svngit-dir",
            "is-inside-work-tree",
            "short",
            "verify",
            "quiet",
            "q",
            "symbolic-full-name",
        ],
    )
    handled = False
    # --short has nothing to shorten: a revision number is already its own
    # shortest form.

    if opts.has("is-inside-work-tree"):
        ctx.echo("true" if ctx.in_working_copy() else "false")
        handled = True
    if opts.has("show-toplevel"):
        ctx.echo(str(ctx.wc_root))
        handled = True
    if opts.has("git-dir", "svngit-dir"):
        # git keeps its metadata in .git; svngit keeps the parts Subversion
        # cannot store outside the working copy entirely.
        ctx.echo(str(ctx.state.dir))
        handled = True

    for spec in opts.positionals:
        if opts.has("abbrev-ref", "symbolic-full-name"):
            ctx.echo(ctx.branch if spec.upper() in ("HEAD", "@") else spec)
        else:
            try:
                revision = rev_mod.resolve(ctx, spec, str(ctx.wc_root))
            except (UsageError, SvnGitError):
                # --verify reports a bad revision by exit code; -q also
                # silences the message, as git does.
                if opts.has("verify"):
                    if not opts.has("quiet", "q"):
                        ctx.warn("fatal: Needed a single revision")
                    return 1
                raise
            ctx.echo(formatting.revision_id(revision))
        handled = True

    if not handled:
        raise UsageError(
            "git rev-parse <revision> | --show-toplevel | --abbrev-ref HEAD"
        )
    return 0


# ----------------------------------------------------------------------
# ls-files
# ----------------------------------------------------------------------
def cmd_ls_files(ctx: "Context", argv: List[str]) -> int:
    opts = parse(
        argv,
        flags=[
            "cached",
            "c",
            "modified",
            "m",
            "others",
            "o",
            "deleted",
            "d",
            "stage",
            "s",
        ],
    )
    refuse(
        "ls-files",
        opts,
        {
            "stage": "there are no git object ids or stage numbers to print.",
            "s": "there are no git object ids or stage numbers to print.",
        },
    )
    # -c/--cached is the default listing.
    from .. import status as status_mod

    if opts.has("modified", "m") or opts.has("others", "o") or opts.has("deleted", "d"):
        report = status_mod.compute(
            ctx, ctx.to_wc_paths(opts.paths) if opts.paths else None
        )
        for entry in report.entries:
            if opts.has("others", "o") and entry.untracked:
                ctx.echo(entry.path)
            elif opts.has("modified", "m") and entry.worktree == "M":
                ctx.echo(entry.path)
            elif opts.has("deleted", "d") and "D" in entry.code:
                ctx.echo(entry.path)
        return 0

    target = (
        ctx.svn_target(ctx.to_wc_path(opts.paths[0]))
        if opts.paths
        else str(ctx.wc_root)
    )
    result = ctx.svn.run("list", "-R", target, check=False)
    for line in result.stdout.splitlines():
        if line and not line.endswith("/"):
            ctx.echo(line)
    return 0


# ----------------------------------------------------------------------
# help / version
# ----------------------------------------------------------------------
USAGE = """\
svngit - run git commands against a Subversion repository

usage: git <command> [<args>]

Working copy
   clone      svn checkout (descends into trunk by default)
   init       explains the Subversion equivalent; --standalone creates a repo
   status     svn status, rendered as git's two-column status
   add        svn add for new files, plus svngit's staging area (-p, -e, -N)
   rm / mv    svn delete / svn move
   restore    svn revert (or unstage with --staged)
   reset      unstage, undo a local commit (--soft), or svn revert (--hard)
   clean      delete unversioned files
   sparse-checkout   svn update --set-depth

History and search
   log        svn log (-S / -G read each revision's diff)
   show       svn log -v plus the revision's diff
   diff       svn diff, honouring the staging area
   blame      svn blame
   shortlog   svn log grouped by author
   describe   name a revision after the nearest tag
   whatchanged   log --name-status
   grep       search versioned files
   check-ignore  test a path against svn:ignore

Sharing
   commit     record a local commit (see svngit.commitmode)
   push       replay local commits as svn commits
   pull       svn update
   fetch      list revisions available on the server
   stash      svn diff + svn patch, stored locally (-p picks hunks)

Branching
   branch     list / create / copy / delete branch directories
   checkout   svn switch, or restore files
   switch     svn switch
   merge      svn merge, committed with its mergeinfo
   cherry-pick / revert   svn merge -c / svn merge -c -N
   tag        list / create / delete tag directories

Patches and archives
   apply      apply a patch to the working copy
   format-patch   one mbox patch file per revision
   archive    svn export into a tar or zip

Tools and plumbing
   difftool / mergetool   hand files to an external program
   config     svngit's own per-working-copy settings
   remote     shows the repository URL
   rev-parse  resolve a revision, or print paths
   ls-files   list versioned files

Global options
   --dry-run        print the svn commands instead of running the ones that
                    would change something
   --trace          print every svn command as it runs
   --svngit-help    this text

Commands with no Subversion equivalent (rebase, bisect, submodule and
others) explain what to use instead when you run them.

Full command mapping: docs/COMMANDS.md
"""


def cmd_help(ctx: "Context", argv: List[str]) -> int:
    ctx.echo(USAGE.rstrip())
    return 0


def cmd_version(ctx: "Context", argv: List[str]) -> int:
    from .. import __version__

    ctx.echo("svngit version %s" % __version__)
    if not ctx.svn.available():
        ctx.echo("subversion: not found on PATH")
        return 0
    result = ctx.svn.run("--version", "--quiet", check=False)
    if result.ok:
        ctx.echo("subversion version %s" % result.stdout.strip())
    return 0
