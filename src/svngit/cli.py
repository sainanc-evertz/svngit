"""Entry point: parse global options, dispatch, and turn exceptions into
git-shaped error messages."""

from __future__ import annotations

import difflib
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import commands
from .commands import NO_EQUIVALENT, REGISTRY
from .context import Context
from .errors import NotAWorkingCopy, SvnGitError, UsageError

def _split_global_options(argv: Sequence[str]):
    """Pull out the options that appear before the subcommand."""
    options = {"dry_run": False, "trace": False, "cwd": None, "help": False, "version": False}
    rest = list(argv)
    while rest:
        arg = rest[0]
        if arg == "--dry-run":
            options["dry_run"] = True
        elif arg == "--trace":
            options["trace"] = True
        elif arg in ("--help", "-h", "--svngit-help"):
            options["help"] = True
        elif arg == "--version":
            options["version"] = True
        elif arg in ("--no-pager", "--paginate", "-p", "--bare"):
            pass  # accepted and ignored: svngit never spawns a pager
        elif arg in ("-C", "--directory"):
            rest.pop(0)
            if not rest:
                raise UsageError("-C requires a directory")
            options["cwd"] = rest[0]
        elif arg.startswith("-c") and len(arg) > 2:
            pass  # `git -c key=value`: svngit has no transient config
        else:
            break
        rest.pop(0)
    return options, rest


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        options, rest = _split_global_options(argv)
    except UsageError as exc:
        print("fatal: %s" % exc, file=sys.stderr)
        return exc.exit_code

    ctx = Context(
        cwd=Path(options["cwd"]).expanduser() if options["cwd"] else None,
        dry_run=options["dry_run"],
        trace=options["trace"],
    )

    if options["version"] and not rest:
        rest = ["version"]
    if options["help"] and not rest:
        rest = ["help"]
    if not rest:
        rest = ["help"]

    name = rest[0]
    args = rest[1:]

    if options["help"] and name not in ("help", "version"):
        # `git <cmd> --help` and `git --help <cmd>` both mean "explain this".
        args = list(args) + ["--help"]

    return dispatch(ctx, name, args)


def dispatch(ctx: Context, name: str, args: List[str]) -> int:
    command = commands.resolve(name)

    if command is None:
        return _unknown_command(ctx, name)

    if "--help" in args or "-h" in args:
        from .commands.plumbing import cmd_help

        return cmd_help(ctx, [])

    if command.needs_working_copy and not ctx.in_working_copy():
        ctx.warn(
            "fatal: not a subversion working copy (or any parent up to mount point %s)"
            % ctx.cwd
        )
        return 128

    try:
        return command.run(ctx, args) or 0
    except UsageError as exc:
        ctx.warn("usage: %s" % exc)
        return exc.exit_code
    except (NotAWorkingCopy, SvnGitError) as exc:
        ctx.warn("fatal: %s" % exc)
        return exc.exit_code
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        ctx.warn("")
        return 130


def _unknown_command(ctx: Context, name: str) -> int:
    explanation = NO_EQUIVALENT.get(name)
    if explanation:
        ctx.warn("fatal: git %s has no Subversion equivalent: %s" % (name, explanation))
        return 128

    ctx.warn("svngit: '%s' is not a command it knows how to translate." % name)
    close = difflib.get_close_matches(name, sorted(REGISTRY), n=3)
    if close:
        ctx.warn("")
        ctx.warn("The most similar commands are")
        for candidate in close:
            ctx.warn("\t%s" % candidate)
    ctx.warn("")
    ctx.warn("Run 'git --svngit-help' for the list of supported commands.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
