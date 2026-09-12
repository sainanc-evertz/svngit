"""Colour for diffs, patches and status.

The tests that matter most here are the negative ones. Colour that leaks into
output something else parses -- a patch file, `--porcelain`, the buffer handed
to `$EDITOR` -- turns a display nicety into corruption.
"""

from __future__ import annotations

import io

import pytest

from svngit import colour as colour_mod
from svngit import patch as patch_mod
from svngit.colour import Palette

ESC = "\033"

DIFF = """\
diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
"""


class FakeTTY(io.StringIO):
    """A stream that claims to be a terminal, which is what turns colour on."""

    def isatty(self):
        return True


# ----------------------------------------------------------------------
# painting
# ----------------------------------------------------------------------
def test_added_and_removed_lines_get_their_colours():
    painted = colour_mod.paint_diff(DIFF, Palette.on())
    assert colour_mod.NEW + "+TWO" in painted
    assert colour_mod.OLD + "-two" in painted


def test_context_lines_are_left_alone():
    painted = colour_mod.paint_diff(DIFF, Palette.on()).split("\n")
    assert " one" in painted, "a context line should carry no escape codes"


def test_file_headers_are_not_mistaken_for_added_or_removed_lines():
    """`---` and `+++` start with - and +, so a naive rule paints them as
    content rather than as headers."""
    painted = colour_mod.paint_diff(DIFF, Palette.on())
    assert colour_mod.META + "--- a/a.txt" in painted
    assert colour_mod.META + "+++ b/a.txt" in painted
    assert colour_mod.OLD + "--- " not in painted
    assert colour_mod.NEW + "+++ " not in painted


def test_hunk_header_is_cyan_and_keeps_its_function_context():
    line = colour_mod.paint_diff_line("@@ -1,3 +1,3 @@ def parse():", Palette.on())
    assert line.startswith(colour_mod.FRAG + "@@ -1,3 +1,3 @@")
    assert line.endswith(" def parse():"), "trailing context should stay plain"


def test_a_disabled_palette_changes_nothing():
    assert colour_mod.paint_diff(DIFF, Palette.off()) == DIFF


def test_painting_is_reversible_in_shape():
    """Stripping the escapes must give back exactly the original text."""
    import re

    painted = colour_mod.paint_diff(DIFF, Palette.on())
    assert re.sub(r"\033\[[0-9;]*m", "", painted) == DIFF


# ----------------------------------------------------------------------
# when colour is on
# ----------------------------------------------------------------------
def test_colour_is_off_when_output_is_not_a_terminal(harness):
    assert not colour_mod.want_colour(harness.ctx)


def test_colour_is_on_for_a_terminal(harness):
    harness.ctx.stdout = FakeTTY()
    assert colour_mod.want_colour(harness.ctx)


def test_no_color_environment_variable_wins(harness, monkeypatch):
    harness.ctx.stdout = FakeTTY()
    monkeypatch.setenv("NO_COLOR", "1")
    assert not colour_mod.want_colour(harness.ctx)


def test_term_dumb_disables_colour(harness, monkeypatch):
    harness.ctx.stdout = FakeTTY()
    monkeypatch.setenv("TERM", "dumb")
    assert not colour_mod.want_colour(harness.ctx)


def test_config_can_force_colour_on(harness):
    harness.ctx.state.set_config("color.ui", "always")
    assert colour_mod.want_colour(harness.ctx)


def test_config_can_force_colour_off(harness):
    harness.ctx.stdout = FakeTTY()
    harness.ctx.state.set_config("color.diff", "never")
    assert not colour_mod.want_colour(harness.ctx)


def test_diff_config_beats_ui_config(harness):
    harness.ctx.state.set_config("color.ui", "always")
    harness.ctx.state.set_config("color.diff", "never")
    assert not colour_mod.want_colour(harness.ctx)


def test_status_reads_its_own_config_key(harness):
    harness.ctx.state.set_config("color.status", "always")
    assert colour_mod.want_colour(harness.ctx, keys=("color.status",))
    assert not colour_mod.want_colour(harness.ctx, keys=("color.diff",))


# ----------------------------------------------------------------------
# through the CLI
# ----------------------------------------------------------------------
def setup_change(harness):
    harness.write("a.txt", "one\nTWO\nthree\n")
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("diff", DIFF, first=True)


def test_diff_is_plain_when_piped(harness):
    setup_change(harness)
    harness.run("diff", "HEAD")
    assert ESC not in harness.out


def test_diff_colour_can_be_forced(harness):
    setup_change(harness)
    harness.run("diff", "--color", "HEAD")
    assert ESC in harness.out


@pytest.mark.parametrize("flag", ["--color=always", "--color"])
def test_colour_always_forms(harness, flag):
    setup_change(harness)
    harness.run("diff", flag, "HEAD")
    assert ESC in harness.out


@pytest.mark.parametrize("flag", ["--no-color", "--color=never"])
def test_colour_never_forms(harness, flag):
    setup_change(harness)
    harness.ctx.state.set_config("color.ui", "always")
    harness.run("diff", flag, "HEAD")
    assert ESC not in harness.out


def test_status_colours_staged_and_untracked_differently(harness):
    harness.write("staged.txt", "x\n")
    harness.write("new.txt", "y\n")
    harness.set_status([("staged.txt", "modified"), ("new.txt", "unversioned")])
    harness.run("add", "staged.txt")
    harness.reset_output()

    harness.run("status", "--color")
    assert colour_mod.ADDED in harness.out, "a staged path should be green"
    assert colour_mod.UNTRACKED in harness.out, "an untracked path should be red"


def test_status_short_is_coloured(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("status", "--short", "--color")
    assert ESC in harness.out


def test_status_porcelain_is_never_coloured(harness):
    """--porcelain is a promised machine format; escape codes would break
    whatever is reading it."""
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.ctx.state.set_config("color.ui", "always")
    harness.run("status", "--porcelain", "--color")
    assert ESC not in harness.out


def test_add_patch_hunks_are_coloured_on_a_terminal(harness):
    base = "one\ntwo\nthree\n"
    harness.write("a.txt", "one\nTWO\nthree\n")
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", base, first=True)
    harness.ctx.stdout = FakeTTY()
    harness.answer("n")
    harness.run("add", "-p")
    assert ESC in harness.ctx.stdout.getvalue()


# ----------------------------------------------------------------------
# colour must not reach anything that gets parsed or written
# ----------------------------------------------------------------------
def test_the_editor_buffer_for_add_e_is_never_coloured(harness):
    """It is handed to $EDITOR and parsed back; escape codes would make it
    unreadable to the patch parser."""
    base = "one\ntwo\nthree\n"
    harness.write("a.txt", "one\nTWO\nthree\n")
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", base, first=True)
    harness.ctx.stdout = FakeTTY()
    harness.ctx.state.set_config("color.ui", "always")

    seen = {}
    harness.ctx.edit_hook = lambda text: seen.setdefault("text", text) or text
    harness.run("add", "-e")
    assert ESC not in seen["text"], "the patch sent to the editor carried colour"


def test_the_hunk_buffer_for_per_hunk_edit_is_never_coloured(harness):
    base = "one\ntwo\nthree\n"
    harness.write("a.txt", "one\nTWO\nthree\n")
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", base, first=True)
    harness.ctx.stdout = FakeTTY()
    harness.ctx.state.set_config("color.ui", "always")

    seen = {}
    harness.ctx.edit_hook = lambda text: seen.setdefault("text", text) or text
    harness.answer("e", "n")
    harness.run("add", "-p")
    assert ESC not in seen["text"], "the hunk sent to the editor carried colour"


def test_format_patch_files_are_never_coloured(harness, tmp_path):
    harness.set_log([{"revision": 5, "message": "a change"}])
    harness.svn.respond("diff", DIFF, first=True)
    harness.ctx.state.set_config("color.ui", "always")
    harness.run("format-patch", "-o", str(tmp_path), "-1")
    written = list(tmp_path.glob("*.patch"))
    assert written
    assert ESC not in written[0].read_text()


# ----------------------------------------------------------------------
# header normalisation, which colour work exposed
# ----------------------------------------------------------------------
SVN_DIFF = """\
Index: /abs/wc/a.txt
===================================================================
--- /abs/wc/a.txt\t(revision 2)
+++ /abs/wc/a.txt\t(working copy)
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
"""


def test_svn_headers_become_git_headers(tmp_path):
    converted = patch_mod.to_git_headers(SVN_DIFF.replace("/abs/wc", str(tmp_path)), tmp_path)
    assert "diff --git a/a.txt b/a.txt" in converted
    assert "--- a/a.txt" in converted
    assert "+++ b/a.txt" in converted
    assert "Index:" not in converted
    assert "(revision 2)" not in converted


def test_converting_git_headers_again_changes_nothing(tmp_path):
    """Both of `git diff`'s code paths run through this, and one already
    produces git headers. Prefixing twice would give `a/a/file`."""
    once = patch_mod.to_git_headers(DIFF, tmp_path)
    assert once == DIFF.rstrip("\n")
    assert patch_mod.to_git_headers(once, tmp_path) == once
    assert "a/a/" not in once


def test_dev_null_is_left_alone(tmp_path):
    text = "--- /dev/null\n+++ b/new.txt\n"
    converted = patch_mod.to_git_headers(text, tmp_path)
    assert "--- /dev/null" in converted
    assert "a//dev/null" not in converted


def test_converted_headers_survive_the_parser(tmp_path):
    """The point of converting: the result has to be appliable."""
    converted = patch_mod.to_git_headers(SVN_DIFF.replace("/abs/wc", str(tmp_path)), tmp_path)
    parsed = patch_mod.parse_patch(converted)
    # The parser strips the a/ prefix it was given, as git apply -p1 does.
    assert [p.path for p in parsed] == ["a.txt"]
    assert patch_mod.apply_file_patch("one\ntwo\nthree\n", parsed[0]) == "one\nTWO\nthree\n"
