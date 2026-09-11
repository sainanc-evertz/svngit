"""`git add -e`: rendering a patch, parsing a hand-edited one, applying it."""

import pytest

from svngit import patch as patch_mod
from svngit import status as status_mod

BASE = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n"


def render(base, work, path="a.txt"):
    return "\n".join(patch_mod.render_file_patch(path, base, work)) + "\n"


def round_trip(base, work, path="a.txt"):
    """Render then re-apply unedited: must reproduce the worktree exactly."""
    patches = patch_mod.parse_patch(render(base, work, path))
    return patch_mod.apply_file_patch(base, patches[0])


# ----------------------------------------------------------------------
# round trips
# ----------------------------------------------------------------------
def test_unedited_patch_reproduces_the_worktree():
    work = BASE.replace("two", "TWO").replace("ten", "TEN")
    assert round_trip(BASE, work) == work


def test_pure_insertion_round_trips():
    work = BASE.replace("one\n", "one\ninserted\n")
    assert round_trip(BASE, work) == work


def test_pure_deletion_round_trips():
    work = BASE.replace("five\n", "")
    assert round_trip(BASE, work) == work


def test_insertion_at_start_of_file_round_trips():
    work = "zero\n" + BASE
    assert round_trip(BASE, work) == work


def test_insertion_at_end_of_file_round_trips():
    work = BASE + "eleven\n"
    assert round_trip(BASE, work) == work


def test_creating_content_from_empty_round_trips():
    assert round_trip("", "brand new\nfile\n") == "brand new\nfile\n"


def test_deleting_all_content_round_trips():
    assert round_trip(BASE, "") == ""


def test_file_without_trailing_newline_round_trips():
    """The no-newline marker has to survive rendering and parsing, or the tool
    silently appends a newline to the staged content."""
    assert round_trip("one\ntwo", "one\nTWO") == "one\nTWO"


def test_gaining_a_trailing_newline_round_trips():
    assert round_trip("one\ntwo", "one\ntwo\n") == "one\ntwo\n"


def test_losing_a_trailing_newline_round_trips():
    assert round_trip("one\ntwo\n", "one\ntwo") == "one\ntwo"


def test_blank_lines_round_trip():
    base = "one\n\n\ntwo\n"
    work = "one\n\n\nTWO\n"
    assert round_trip(base, work) == work


# ----------------------------------------------------------------------
# hand edits
# ----------------------------------------------------------------------
def test_deleting_an_added_line_drops_it_from_the_staged_content():
    work = BASE.replace("one\n", "one\nkeep me\ndrop me\n")
    text = render(BASE, work)
    edited = "\n".join(line for line in text.splitlines() if line != "+drop me")
    result = patch_mod.apply_file_patch(BASE, patch_mod.parse_patch(edited)[0])
    assert "keep me" in result
    assert "drop me" not in result


def test_turning_a_removed_line_into_context_keeps_it():
    work = BASE.replace("five\n", "")
    text = render(BASE, work)
    edited = text.replace("-five", " five")
    result = patch_mod.apply_file_patch(BASE, patch_mod.parse_patch(edited)[0])
    assert result == BASE  # the deletion was declined


def test_editing_the_text_of_an_added_line_stages_the_edit():
    """git's stated purpose for -e: stage something other than what is on disk."""
    work = BASE.replace("two", "TWO")
    text = render(BASE, work)
    edited = text.replace("+TWO", "+SOMETHING ELSE")
    result = patch_mod.apply_file_patch(BASE, patch_mod.parse_patch(edited)[0])
    assert "SOMETHING ELSE" in result
    assert "TWO" not in result


def test_stale_hunk_headers_are_tolerated():
    """A human will not fix the @@ counts after editing, and git does not ask
    them to."""
    work = BASE.replace("two", "TWO")
    text = render(BASE, work)
    edited = text.replace("@@ -1,5 +1,5 @@", "@@ -99,999 +99,999 @@")
    result = patch_mod.apply_file_patch(BASE, patch_mod.parse_patch(edited)[0])
    assert result == work


def test_context_line_stripped_of_its_leading_space_is_still_context():
    """Editors strip trailing whitespace, so a blank context line arrives as an
    empty line. Rejecting that would make -e unusable in most editors."""
    base = "one\n\ntwo\n"
    work = "one\n\nTWO\n"
    text = render(base, work)
    edited = "\n".join(line.rstrip() for line in text.splitlines())
    result = patch_mod.apply_file_patch(base, patch_mod.parse_patch(edited)[0])
    assert result == work


def test_comment_lines_are_ignored():
    work = BASE.replace("two", "TWO")
    text = "# a comment\n# another\n" + render(BASE, work)
    result = patch_mod.apply_file_patch(BASE, patch_mod.parse_patch(text)[0])
    assert result == work


def test_deleting_a_whole_hunk_leaves_it_unstaged():
    work = BASE.replace("two", "TWO").replace("ten", "TEN")
    text = render(BASE, work)
    lines = text.splitlines()
    second = next(i for i, l in enumerate(lines) if l.startswith("@@") and i > 3)
    edited = "\n".join(lines[:second])
    result = patch_mod.apply_file_patch(BASE, patch_mod.parse_patch(edited)[0])
    assert result == BASE.replace("two", "TWO")


def test_a_hunk_that_does_not_match_is_rejected():
    work = BASE.replace("two", "TWO")
    text = render(BASE, work)
    edited = text.replace(" three", " NOT THE REAL CONTEXT")
    with pytest.raises(patch_mod.PatchError) as excinfo:
        patch_mod.apply_file_patch(BASE, patch_mod.parse_patch(edited)[0])
    assert "does not apply" in str(excinfo.value)


def test_an_unparseable_line_is_rejected():
    work = BASE.replace("two", "TWO")
    text = render(BASE, work).replace(" three", "!garbage")
    with pytest.raises(patch_mod.PatchError):
        patch_mod.parse_patch(text)


def test_repeated_content_uses_the_header_to_disambiguate():
    """Identical context appears twice; the header decides which one."""
    base = "x\nrepeat\ny\nrepeat\nz\n"
    work = "x\nrepeat\ny\nrepeat\nZ\n"
    assert round_trip(base, work) == work


def test_multiple_files_in_one_patch():
    text = render(BASE, BASE.replace("two", "TWO"), "a.txt") + render(
        BASE, BASE.replace("nine", "NINE"), "b.txt"
    )
    patches = patch_mod.parse_patch(text)
    assert [p.path for p in patches] == ["a.txt", "b.txt"]
    assert patch_mod.apply_file_patch(BASE, patches[0]) == BASE.replace("two", "TWO")
    assert patch_mod.apply_file_patch(BASE, patches[1]) == BASE.replace("nine", "NINE")


def test_empty_patch_parses_to_nothing():
    assert patch_mod.parse_patch("") == []
    assert patch_mod.parse_patch("# only comments\n") == []


# ----------------------------------------------------------------------
# through the CLI
# ----------------------------------------------------------------------
def setup(harness, work=None):
    work = work if work is not None else BASE.replace("two", "TWO").replace("ten", "TEN")
    harness.write("a.txt", work)
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", BASE, first=True)
    return work


def staged(harness, path="a.txt"):
    return harness.ctx.state.objects.read(harness.ctx.state.index[path].blob).decode()


def test_editing_out_a_hunk_stages_only_the_rest(harness):
    setup(harness)
    harness.ctx.edit_hook = lambda text: "\n".join(
        line for line in text.splitlines() if line not in ("-ten", "+TEN")
    )
    assert harness.run("add", "-e") == 0
    assert staged(harness) == BASE.replace("two", "TWO")


def test_unedited_patch_stages_everything(harness):
    work = setup(harness)
    harness.ctx.edit_hook = lambda text: text
    harness.run("add", "-e")
    assert staged(harness) == work


def test_quitting_with_an_empty_patch_stages_nothing(harness):
    setup(harness)
    harness.ctx.edit_hook = lambda text: ""
    assert harness.run("add", "-e") == 0
    assert not harness.ctx.state.index
    assert "No changes." in harness.out


def test_deleting_every_change_stages_nothing(harness):
    setup(harness)
    harness.ctx.edit_hook = lambda text: "\n".join(
        line for line in text.splitlines() if not line.startswith(("+", "-")) or line.startswith(("+++", "---"))
    )
    harness.run("add", "-e")
    assert not harness.ctx.state.index


def test_a_broken_patch_stages_nothing_and_says_so(harness):
    setup(harness)
    harness.ctx.edit_hook = lambda text: text.replace(" three", " CORRUPTED")
    code = harness.run("add", "-e")
    assert code == 1
    assert not harness.ctx.state.index
    assert "does not apply" in harness.err


def test_edited_staging_shows_as_MM(harness):
    setup(harness)
    harness.ctx.edit_hook = lambda text: "\n".join(
        line for line in text.splitlines() if line not in ("-ten", "+TEN")
    )
    harness.run("add", "-e")
    report = status_mod.compute(harness.ctx)
    assert {e.path: e.code for e in report.entries} == {"a.txt": "MM"}


def test_no_changes_at_all(harness):
    harness.set_status([])
    assert harness.run("add", "-e") == 0
    assert "No changes." in harness.out


def test_p_and_e_together_are_refused(harness):
    code = harness.run("add", "-p", "-e")
    assert code != 0
    assert "cannot be combined" in harness.err
