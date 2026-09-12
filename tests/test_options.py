"""Every declared option must do something, or say that it does not.

An option that is accepted and then quietly ignored is worse than one that is
rejected: the user believes they asked for something. This walks the option
lists in the source and fails on any name that is neither read by the command,
refused, reported as having no effect, nor recorded as already-the-default.
"""

from __future__ import annotations

import ast
import pathlib

from svngit.commands import DEFAULT_BEHAVIOUR

COMMANDS_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "svngit" / "commands"
PACKAGE_DIR = COMMANDS_DIR.parent


def _option_reads(tree) -> set:
    """Option names a module reads, via opts.* or refuse()/no_effect()."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
           and node.func.attr in ("has", "get", "first", "count", "negated"):
            names |= {
                a.value for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            }
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("refuse", "no_effect"):
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    names |= {
                        k.value for k in arg.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }
    return names


def _shared_reads() -> set:
    """Options consumed by helpers outside commands/.

    colour.py reads --color and --no-color on behalf of every command that
    offers them, so a per-module scan would call those silently ignored.
    """
    names = set()
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        names |= _option_reads(ast.parse(path.read_text()))
    return names


def _declared_and_handled(path: pathlib.Path):
    tree = ast.parse(path.read_text())
    handled = _option_reads(tree) | _shared_reads()

    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        declared = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "parse":
                for kw in node.keywords:
                    if kw.arg in ("flags", "values") and isinstance(kw.value, ast.List):
                        declared |= {
                            e.value for e in kw.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        }
        if declared:
            yield func.name, declared, handled


def test_no_option_is_silently_ignored():
    unaccounted = []
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        for name, declared, handled in _declared_and_handled(path):
            for option in sorted(declared):
                if option in handled:
                    continue
                if (name, option) in DEFAULT_BEHAVIOUR:
                    continue
                unaccounted.append("%s: %s (%s)" % (name, option, path.name))

    assert not unaccounted, (
        "these options are accepted but do nothing. Read them, pass them to "
        "refuse()/no_effect(), or record them in DEFAULT_BEHAVIOUR:\n  "
        + "\n  ".join(unaccounted)
    )


def test_default_behaviour_entries_are_real_options():
    """Guard the allowlist itself: an entry for an option that no longer
    exists would hide a future one that does."""
    declared_anywhere = set()
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        for name, declared, _ in _declared_and_handled(path):
            declared_anywhere |= {(name, option) for option in declared}

    stale = sorted(key for key in DEFAULT_BEHAVIOUR if key not in declared_anywhere)
    assert not stale, "DEFAULT_BEHAVIOUR lists options that are not declared: %s" % stale


def test_every_default_behaviour_entry_explains_itself():
    for key, reason in DEFAULT_BEHAVIOUR.items():
        assert reason and len(reason) > 15, "%s needs a real explanation" % (key,)
