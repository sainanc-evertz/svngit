"""`git add -p`: hunk splitting, selective application, and the prompt loop."""

from svngit import hunks as hunks_mod
from svngit import status as status_mod

BASE = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n"


def all_changed(diff):
    return {index for index, op in enumerate(diff.ops) if op[0] != "equal"}


# ----------------------------------------------------------------------
# hunk splitting
# ----------------------------------------------------------------------
def test_no_change_produces_no_hunks():
    diff = hunks_mod.diff_file(BASE, BASE)
    assert diff.empty


def test_single_edit_is_one_hunk():
    work = BASE.replace("three", "THREE")
    diff = hunks_mod.diff_file(BASE, work)
    assert len(diff.hunks) == 1


def test_distant_edits_are_separate_hunks():
    """Ten lines apart is more than twice the default context, so the two
    changes must not be grouped into one reviewable hunk."""
    work = BASE.replace("two", "TWO").replace("ten", "TEN")
    diff = hunks_mod.diff_file(BASE, work)
    assert len(diff.hunks) == 2


def test_nearby_edits_are_grouped():
    work = BASE.replace("two", "TWO").replace("four", "FOUR")
    diff = hunks_mod.diff_file(BASE, work)
    assert len(diff.hunks) == 1
    assert diff.hunks[0].splittable


def test_grouped_hunk_splits_into_its_parts():
    work = BASE.replace("two", "TWO").replace("four", "FOUR")
    diff = hunks_mod.diff_file(BASE, work)
    pieces = diff.split(diff.hunks[0])
    assert len(pieces) == 2
    assert all(not piece.splittable for piece in pieces)


def test_a_single_change_cannot_split():
    diff = hunks_mod.diff_file(BASE, BASE.replace("three", "THREE"))
    hunk = diff.hunks[0]
    assert not hunk.splittable
    assert diff.split(hunk) == [hunk]


# ----------------------------------------------------------------------
# selective application
# ----------------------------------------------------------------------
def test_applying_everything_reproduces_the_worktree():
    work = BASE.replace("two", "TWO").replace("ten", "TEN")
    diff = hunks_mod.diff_file(BASE, work)
    assert diff.apply(all_changed(diff)) == work


def test_applying_nothing_reproduces_the_base():
    work = BASE.replace("two", "TWO").replace("ten", "TEN")
    diff = hunks_mod.diff_file(BASE, work)
    assert diff.apply(set()) == BASE


def test_applying_one_hunk_takes_only_that_change():
    work = BASE.replace("two", "TWO").replace("ten", "TEN")
    diff = hunks_mod.diff_file(BASE, work)
    result = diff.apply(set(diff.hunks[0].changed_ops))
    assert "TWO" in result
    assert "TEN" not in result
    assert result == BASE.replace("two", "TWO")


def test_insertions_and_deletions_apply_selectively():
    work = "one\ninserted\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\n"
    diff = hunks_mod.diff_file(BASE, work)
    assert diff.apply(all_changed(diff)) == work
    assert diff.apply(set()) == BASE


def test_file_without_trailing_newline_round_trips():
    base = "one\ntwo"
    work = "one\nTWO"
    diff = hunks_mod.diff_file(base, work)
    assert diff.apply(all_changed(diff)) == work
    assert diff.apply(set()) == base


def test_summarise_counts_only_selected_hunks():
    work = BASE.replace("two", "TWO").replace("ten", "TEN")
    diff = hunks_mod.diff_file(BASE, work)
    insertions, deletions = hunks_mod.summarise(diff, set(diff.hunks[0].changed_ops))
    assert (insertions, deletions) == (1, 1)


def test_rendered_hunk_looks_like_a_unified_diff():
    diff = hunks_mod.diff_file(BASE, BASE.replace("three", "THREE"))
    lines = diff.render(diff.hunks[0])
    assert lines[0].startswith("@@ -")
    assert "-three" in lines
    assert "+THREE" in lines
    assert " two" in lines  # context


# ----------------------------------------------------------------------
# the prompt loop, end to end through the CLI
# ----------------------------------------------------------------------
def setup_two_hunks(harness):
    """A file with two changes far enough apart to be separate hunks."""
    work = BASE.replace("two", "TWO").replace("ten", "TEN")
    harness.write("a.txt", work)
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", BASE, first=True)
    return work


def staged_content(harness, path="a.txt"):
    blob = harness.ctx.state.index[path].blob
    return harness.ctx.state.objects.read(blob).decode()


def test_yes_then_no_stages_only_the_first_hunk(harness):
    setup_two_hunks(harness)
    harness.answer("y", "n")
    harness.run("add", "-p")
    assert staged_content(harness) == BASE.replace("two", "TWO")


def test_no_then_yes_stages_only_the_second_hunk(harness):
    setup_two_hunks(harness)
    harness.answer("n", "y")
    harness.run("add", "-p")
    assert staged_content(harness) == BASE.replace("ten", "TEN")


def test_a_stages_this_and_all_remaining(harness):
    work = setup_two_hunks(harness)
    harness.answer("a")
    harness.run("add", "-p")
    assert staged_content(harness) == work


def test_d_stages_nothing_further(harness):
    setup_two_hunks(harness)
    harness.answer("d")
    harness.run("add", "-p")
    assert not harness.ctx.state.index


def test_q_quits_without_staging(harness):
    setup_two_hunks(harness)
    harness.answer("y", "q")
    harness.run("add", "-p")
    # The hunk already answered yes is kept, matching git.
    assert staged_content(harness) == BASE.replace("two", "TWO")


def test_end_of_input_is_treated_as_quit(harness):
    setup_two_hunks(harness)
    harness.answer()  # no answers at all
    assert harness.run("add", "-p") == 0
    assert not harness.ctx.state.index


def test_unrecognised_answer_prints_help_and_reasks(harness):
    setup_two_hunks(harness)
    harness.answer("z", "y", "n")
    harness.run("add", "-p")
    assert "y - stage this hunk" in harness.out
    assert staged_content(harness) == BASE.replace("two", "TWO")


def test_s_splits_a_grouped_hunk(harness):
    work = BASE.replace("two", "TWO").replace("four", "FOUR")
    harness.write("a.txt", work)
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", BASE, first=True)
    harness.answer("s", "y", "n")
    harness.run("add", "-p")
    assert "Split into 2 hunks." in harness.out
    assert staged_content(harness) == BASE.replace("two", "TWO")


def test_s_on_an_unsplittable_hunk_says_so(harness):
    harness.write("a.txt", BASE.replace("three", "THREE"))
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", BASE, first=True)
    harness.answer("s", "n")
    harness.run("add", "-p")
    assert "cannot split this hunk" in harness.out


def test_partial_staging_shows_as_MM(harness):
    """The staged blob differs from both BASE and the worktree, which is
    exactly git's 'staged and further modified' state."""
    setup_two_hunks(harness)
    harness.answer("y", "n")
    harness.run("add", "-p")
    report = status_mod.compute(harness.ctx)
    assert {e.path: e.code for e in report.entries} == {"a.txt": "MM"}


def test_second_pass_only_offers_the_unstaged_hunk(harness):
    """git diffs index-to-worktree, so an already-staged hunk must not be
    offered again."""
    setup_two_hunks(harness)
    harness.answer("y", "n")
    harness.run("add", "-p")
    harness.reset_output()

    harness.answer("y")
    harness.run("add", "-p")
    assert "(1/1)" in harness.out  # one hunk left, not two
    assert staged_content(harness) == BASE.replace("two", "TWO").replace("ten", "TEN")


def test_untracked_file_is_explained_not_split(harness):
    harness.write("new.txt", "content\n")
    harness.set_status([("new.txt", "unversioned")])
    harness.run("add", "-p", "new.txt")
    assert "untracked" in harness.out
    assert not harness.ctx.state.index


def test_binary_file_offers_all_or_nothing(harness):
    harness.write("logo.png", "x")
    (harness.wc / "logo.png").write_bytes(b"\x89PNG\x00\x01binary")
    harness.set_status([("logo.png", "modified")])
    harness.svn.respond("cat", "\x89PNG\x00old", first=True)
    harness.answer("y")
    harness.run("add", "-p")
    assert "cannot be split into hunks" in harness.out
    assert harness.ctx.state.is_staged("logo.png")


def test_nothing_to_stage_is_quiet(harness):
    harness.set_status([])
    assert harness.run("add", "-p") == 0
    assert not harness.ctx.state.index


def test_summary_counts_hunks_answered_not_opcodes(harness):
    """A grouped hunk holds several changed regions; answering it once is one
    hunk, not one per region."""
    work = BASE.replace("two", "TWO").replace("four", "FOUR")
    harness.write("a.txt", work)
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", BASE, first=True)
    harness.answer("y")
    harness.run("add", "-p")
    assert "Staged 1 hunk from" in harness.out
