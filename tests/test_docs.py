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
    # The invariant is the number, not any particular sentence around it.
    assert word in README, (
        "README should say '%s' somewhere: that is how many commands are "
        "refused (%d)" % (word, len(NO_EQUIVALENT))
    )


def test_the_readme_command_count_is_right():
    """The README states a number; a stale one is worse than none."""
    assert "%d commands" % len(REGISTRY) in README, (
        "README should say '%d commands'" % len(REGISTRY)
    )


# ----------------------------------------------------------------------
# links and images
# ----------------------------------------------------------------------
import re  # noqa: E402

DOCS = {"README.md": README, "docs/COMMANDS.md": MAPPING}


def _headings(text):
    """GitHub's heading slugs, near enough for link checking."""
    found = set()
    for line in text.splitlines():
        match = re.match(r"^#+\s+(.*)", line)
        if not match:
            continue
        slug = re.sub(r"[`*_]", "", match.group(1).lower())
        slug = re.sub(r"[^\w\s-]", "", slug)
        found.add(re.sub(r"\s+", "-", slug.strip()))
    return found


def test_referenced_images_exist():
    for doc, text in DOCS.items():
        base = (ROOT / doc).parent
        refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
        refs += re.findall(r'<img[^>]+src="([^"]+)"', text)
        for ref in refs:
            assert (base / ref).exists(), "%s references a missing image: %s" % (doc, ref)


def test_images_have_alt_text():
    """The images carry real terminal output; a reader who cannot see them
    should still learn what they show."""
    for doc, text in DOCS.items():
        for alt in re.findall(r"!\[([^\]]*)\]\([^)]+\)", text):
            assert len(alt) > 20, "%s has an image with thin alt text: %r" % (doc, alt)
        for tag in re.findall(r"<img[^>]*>", text, re.S):
            match = re.search(r'alt="([^"]*)"', tag)
            assert match and len(match.group(1)) > 20, (
                "%s has an <img> with thin alt text" % doc
            )


def test_internal_anchors_resolve():
    for doc, text in DOCS.items():
        available = _headings(text)
        for anchor in re.findall(r"\]\(#([^)]+)\)", text):
            assert anchor in available, "%s links to a missing section: #%s" % (doc, anchor)


def test_relative_links_resolve():
    for doc, text in DOCS.items():
        base = (ROOT / doc).parent
        for link in re.findall(r"\]\((?!#)(?!https?:)([^)]+)\)", text):
            assert (base / link).exists(), "%s links to a missing file: %s" % (doc, link)


def test_sample_output_is_not_hand_written():
    """The README claims every screenshot is real captured output. Keep that
    true by not letting fabricated `console` transcripts creep back in."""
    for doc, text in DOCS.items():
        assert "```console" not in text, (
            "%s has a hand-written console block; capture it with "
            "docs/demo/capture.sh and render it instead" % doc
        )
