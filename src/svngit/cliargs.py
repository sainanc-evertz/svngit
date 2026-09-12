"""A small git-flavoured option parser.

argparse is the wrong shape here: git allows bundled short flags with a
trailing value (`-am "msg"`), attached values (`-n5`), and `--opt=value`, and
it never reorders positionals. This parser handles exactly those rules and
nothing more.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Sequence, Tuple

from .errors import Unsupported, UsageError

if TYPE_CHECKING:  # pragma: no cover
    from .context import Context


class Options:
    def __init__(
        self,
        values: Dict[str, object],
        positionals: List[str],
        after_dashdash: List[str],
    ):
        self._values = values
        self.positionals = positionals
        #: Paths given after `--`, which git treats as unambiguously pathnames.
        self.after_dashdash = after_dashdash

    def __contains__(self, name: str) -> bool:
        return name in self._values

    def has(self, *names: str) -> bool:
        return any(not self.negated(name) for name in names if name in self._values)

    def negated(self, name: str) -> bool:
        """True when the option was switched off with `--no-<name>`.

        Only an integer 0 counts, so an empty string value (`-m ""`) is still
        a present option.
        """
        value = self._values.get(name)
        return isinstance(value, int) and not isinstance(value, bool) and value == 0

    def get(self, name: str, default: Any = None) -> Any:
        value = self._values.get(name, default)
        return value

    def first(self, *names: str, default: Any = None) -> Any:
        for name in names:
            if name in self._values:
                return self._values[name]
        return default

    def count(self, name: str) -> int:
        value = self._values.get(name)
        return (
            int(value) if isinstance(value, int) else (1 if name in self._values else 0)
        )

    @property
    def paths(self) -> List[str]:
        return self.after_dashdash if self.after_dashdash else self.positionals

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Options(%r, %r)" % (self._values, self.positionals)


def parse(
    argv: Sequence[str],
    flags: Iterable[str] = (),
    values: Iterable[str] = (),
    optional_values: Iterable[str] = (),
    allow_numeric: bool = False,
    numeric_key: str = "n",
) -> Options:
    """Split argv into options and positionals.

    `flags` and `values` are option names without dashes; single characters
    are short options, longer names are long options. `allow_numeric` accepts
    git's bare `-5` shorthand for "limit to 5", stored under `numeric_key` --
    which has to move where `-n` already means something else, as it does for
    `format-patch`, where `-n` is --numbered.

    `optional_values` covers git's `--opt[=<when>]` shape, where the option
    means one thing on its own and another with a value: `--color` and
    `--color=always` are both valid, and `--color auto` is not -- the next
    word is a path, not the option's argument.
    """
    flag_set = set(flags)
    value_set = set(values)
    optional_set = set(optional_values)
    parsed: Dict[str, object] = {}
    positionals: List[str] = []
    after_dashdash: List[str] = []
    args = list(argv)
    index = 0
    seen_dashdash = False

    def store_flag(name: str) -> None:
        current = parsed.get(name)
        parsed[name] = (current + 1) if isinstance(current, int) else 1

    while index < len(args):
        arg = args[index]
        index += 1

        if seen_dashdash:
            after_dashdash.append(arg)
            continue
        if arg == "--":
            seen_dashdash = True
            continue

        if arg.startswith("--"):
            name, sep, inline = arg[2:].partition("=")
            if name in optional_set:
                # Only an attached value counts; a following word is a path.
                parsed[name] = inline if sep else 1
            elif name in value_set:
                if sep:
                    parsed[name] = inline
                else:
                    if index >= len(args):
                        raise UsageError("option --%s requires a value" % name)
                    parsed[name] = args[index]
                    index += 1
            elif name in flag_set:
                if sep:
                    parsed[name] = inline
                else:
                    store_flag(name)
            elif name.startswith("no-") and name[3:] in flag_set:
                parsed[name[3:]] = 0
            else:
                raise UsageError("unknown option: %s" % arg)
            continue

        if arg.startswith("-") and arg != "-":
            if allow_numeric and arg[1:].isdigit():
                parsed[numeric_key] = arg[1:]
                continue
            cursor = 1
            while cursor < len(arg):
                letter = arg[cursor]
                cursor += 1
                if letter in value_set:
                    remainder = arg[cursor:]
                    if remainder:
                        parsed[letter] = remainder
                    else:
                        if index >= len(args):
                            raise UsageError("option -%s requires a value" % letter)
                        parsed[letter] = args[index]
                        index += 1
                    break
                if letter in flag_set:
                    store_flag(letter)
                    continue
                raise UsageError("unknown option: -%s" % letter)
            continue

        positionals.append(arg)

    return Options(parsed, positionals, after_dashdash)


def split_revisions_and_paths(
    ctx: "Context", items: Sequence[str]
) -> Tuple[List[str], List[str]]:
    """git lets revisions and paths share the positional slot. Anything that
    exists on disk is a path; everything else is treated as a revision."""
    revisions: List[str] = []
    paths: List[str] = []
    for item in items:
        candidate = (ctx.cwd / item).expanduser()
        if candidate.exists():
            paths.append(item)
        else:
            revisions.append(item)
    return revisions, paths


# ----------------------------------------------------------------------
# options that cannot be honoured
# ----------------------------------------------------------------------
def refuse(command: str, opts: Options, reasons: Dict[str, str]) -> None:
    """Raise for any option present that svngit cannot honour.

    Accepting an option and then ignoring it is the worst outcome: the user
    believes they asked for something. Anything that cannot be done is
    refused here, with the reason and the nearest alternative.
    """
    for name, reason in reasons.items():
        if opts.has(name):
            dash = "-" if len(name) == 1 else "--"
            raise Unsupported("git %s %s%s: %s" % (command, dash, name, reason))


def no_effect(
    ctx: "Context", command: str, opts: Options, reasons: Dict[str, str]
) -> None:
    """Report any option that is accepted but changes nothing here."""
    for name, reason in reasons.items():
        if opts.has(name):
            dash = "-" if len(name) == 1 else "--"
            ctx.note("git %s %s%s: %s" % (command, dash, name, reason))
