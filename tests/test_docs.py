"""Documentation has to keep up with the registry.

Every command svngit answers to should be findable in the README, in the
command mapping, and in `git --svngit-help`. These checks exist because the
README had already fallen behind twice: adding a command and forgetting the
docs is the easiest mistake here, and the least visible.
"""

from __future__ import annotations

import pathlib

from svngit.commands import NO_EQUIVALENT, REGISTRY
from svngit.commands.plumbing import USAGE

ROOT = pathlib.Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
MAPPING = (ROOT / "docs" / "COMMANDS.md").read_text()

#: Commands that exist for the tool's own sake rather than as translations,
#: so the mapping table has nothing to say about them.
INTERNAL = {"help", "version"}


def test_every_command_is_in_the_readme():
    missing = [name for name in sorted(REGISTRY) if "`%s`" % name not in README]
    assert not missing, "not mentioned in README.md: %s" % missing


def test_every_command_is_in_the_mapping():
    missing = [
        name
        for name in sorted(REGISTRY)
        if name not in INTERNAL and "`git %s" % name not in MAPPING
    ]
    assert not missing, "not in docs/COMMANDS.md: %s" % missing


def test_every_command_is_in_the_help_text():
    missing = [name for name in sorted(REGISTRY) if name not in USAGE]
    assert not missing, "not in `git --svngit-help`: %s" % missing


def test_refused_commands_are_listed_in_the_readme():
    """A reader should be able to see it is deliberate, not an oversight."""
    missing = [name for name in sorted(NO_EQUIVALENT) if "`%s`" % name not in README]
    assert not missing, "refused but not explained in README.md: %s" % missing


def test_refused_commands_all_give_a_reason():
    for name, reason in NO_EQUIVALENT.items():
        assert len(reason) > 25, "%s needs a real explanation, got %r" % (name, reason)


#: The README spells the refused count as a word; keep the two in step.
COUNT_WORDS = {23: "Twenty-three", 24: "Twenty-four", 25: "Twenty-five", 26: "Twenty-six"}


def test_the_readme_refused_count_is_right():
    word = COUNT_WORDS.get(len(NO_EQUIVALENT))
    assert word, (
        "no spelling for %d refused commands; add one to COUNT_WORDS and "
        "update the README" % len(NO_EQUIVALENT)
    )
    assert "%s of them" % word in README, (
        "README should say '%s of them' for the %d refused commands"
        % (word, len(NO_EQUIVALENT))
    )


def test_the_readme_command_count_is_right():
    """The README states a number; a stale one is worse than none."""
    assert "%d commands" % len(REGISTRY) in README, (
        "README should say '%d commands'" % len(REGISTRY)
    )
