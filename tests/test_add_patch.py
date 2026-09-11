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


# ----------------------------------------------------------------------
# the per-hunk `e` command
# ----------------------------------------------------------------------
def test_e_stages_the_edited_hunk(harness):
    setup_two_hunks(harness)
    harness.ctx.edit_hook = lambda text: text.replace("+TWO", "+EDITED")
    harness.answer("e", "n")
    harness.run("add", "-p")
    assert staged_content(harness) == BASE.replace("two", "EDITED")


def test_e_can_drop_an_added_line_from_the_hunk(harness):
    work = BASE.replace("one\n", "one\nkeep\ndrop\n")
    harness.write("a.txt", work)
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", BASE, first=True)
    harness.ctx.edit_hook = lambda text: "\n".join(
        line for line in text.splitlines() if line != "+drop"
    )
    harness.answer("e")
    harness.run("add", "-p")
    result = staged_content(harness)
    assert "keep" in result and "drop" not in result


def test_e_combines_with_a_plain_yes_on_another_hunk(harness):
    """An edited hunk and an as-is hunk have to coexist in one staged blob."""
    setup_two_hunks(harness)
    harness.ctx.edit_hook = lambda text: text.replace("+TEN", "+EDITED")
    harness.answer("y", "e")
    harness.run("add", "-p")
    assert staged_content(harness) == BASE.replace("two", "TWO").replace("ten", "EDITED")


def test_e_that_does_not_apply_reprompts_the_same_hunk(harness):
    setup_two_hunks(harness)
    calls = {"n": 0}

    def hook(text):
        calls["n"] += 1
        if calls["n"] == 1:
            return text.replace(" three", " CORRUPTED")  # context no longer matches
        return text.replace("+TWO", "+SECOND TRY")

    harness.ctx.edit_hook = hook
    harness.answer("e", "e", "n")
    harness.run("add", "-p")
    assert "does not apply" in harness.err
    assert calls["n"] == 2
    assert staged_content(harness) == BASE.replace("two", "SECOND TRY")


def test_e_with_every_line_removed_aborts_the_edit(harness):
    setup_two_hunks(harness)
    harness.ctx.edit_hook = lambda text: ""
    harness.answer("e", "n")
    harness.run("add", "-p")
    assert "Edit aborted" in harness.out
    assert not harness.ctx.state.index


def test_e_is_offered_in_the_prompt(harness):
    setup_two_hunks(harness)
    harness.answer("n", "n")
    harness.run("add", "-p")
    assert ",e,?]" in harness.out


def test_e_is_not_offered_for_a_binary_file(harness):
    (harness.wc / "logo.png").write_bytes(b"\x89PNG\x00new")
    harness.set_status([("logo.png", "modified")])
    harness.svn.respond("cat", "\x89PNG\x00old", first=True)
    harness.answer("n")
    harness.run("add", "-p")
    assert ",e," not in harness.out


def test_help_mentions_e(harness):
    setup_two_hunks(harness)
    harness.answer("?", "n", "n")
    harness.run("add", "-p")
    assert "e - manually edit the current hunk" in harness.out


# ----------------------------------------------------------------------
# navigation: j J k K g /
# ----------------------------------------------------------------------
def setup_three_hunks(harness):
    """Three changes, each far enough apart to be its own hunk."""
    base = "".join("line%02d\n" % n for n in range(1, 31))
    work = base.replace("line02", "TWO").replace("line15", "FIFTEEN").replace("line29", "TWENTYNINE")
    harness.write("a.txt", work)
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", base, first=True)
    return base, work


def test_J_moves_to_the_next_hunk_without_deciding(harness):
    base, _ = setup_three_hunks(harness)
    # Skip past hunk 1, stage hunk 2, skip hunk 3, then decline hunk 1.
    harness.answer("J", "y", "n", "n")
    harness.run("add", "-p")
    assert staged_content(harness) == base.replace("line15", "FIFTEEN")


def test_K_moves_back_to_a_previous_hunk(harness):
    base, _ = setup_three_hunks(harness)
    # Move forward twice without deciding, then back once and stage there.
    harness.answer("J", "J", "K", "y", "n", "n")
    harness.run("add", "-p")
    assert staged_content(harness) == base.replace("line15", "FIFTEEN")


def test_j_skips_hunks_that_are_already_decided(harness):
    base, _ = setup_three_hunks(harness)
    # Decide hunk 1, J back is unavailable; from hunk 2 `j` must land on 3.
    harness.answer("n", "j", "y", "n")
    harness.run("add", "-p")
    assert staged_content(harness) == base.replace("line29", "TWENTYNINE")


def test_undecided_hunks_are_revisited_rather_than_dropped(harness):
    """`j` means 'come back to this later', so leaving a hunk undecided and
    answering the last one must return to it, not silently skip it."""
    base, _ = setup_three_hunks(harness)
    harness.answer("J", "J", "n", "y", "n")
    harness.run("add", "-p")
    # After deciding hunk 3, the loop wraps to the still-undecided hunk 1.
    assert staged_content(harness) == base.replace("line02", "TWO")


def test_no_next_hunk_at_the_end(harness):
    setup_two_hunks(harness)
    harness.answer("n", "J", "n")
    harness.run("add", "-p")
    assert "No next hunk" in harness.out


def test_no_previous_hunk_at_the_start(harness):
    setup_two_hunks(harness)
    harness.answer("K", "n", "n")
    harness.run("add", "-p")
    assert "No previous hunk" in harness.out


def test_prompt_only_offers_moves_that_exist(harness):
    setup_two_hunks(harness)
    harness.answer("n", "n")
    harness.run("add", "-p")
    prompts = harness.out.split("Stage this hunk ")
    # First hunk: no way back. Last hunk: no way forward.
    assert "K" not in prompts[1].split("]")[0]
    assert "J" not in prompts[2].split("]")[0]


def test_g_jumps_to_a_chosen_hunk(harness):
    base, _ = setup_three_hunks(harness)
    harness.answer("g", "3", "y", "n", "n")
    harness.run("add", "-p")
    assert "go to which hunk?" in harness.out
    assert staged_content(harness) == base.replace("line29", "TWENTYNINE")


def test_g_lists_hunks_with_their_decisions(harness):
    setup_three_hunks(harness)
    harness.answer("y", "g", "", "n", "n")
    harness.run("add", "-p")
    listing = [l for l in harness.out.splitlines() if ": -1," in l]
    assert listing and listing[0].startswith("+  1")  # hunk 1 marked staged


def test_g_rejects_an_out_of_range_number(harness):
    setup_two_hunks(harness)
    harness.answer("g", "99", "n", "n")
    harness.run("add", "-p")
    assert "Invalid number: '99'" in harness.out


def test_search_jumps_to_a_matching_hunk(harness):
    base, _ = setup_three_hunks(harness)
    harness.answer("/", "TWENTYNINE", "y", "n", "n")
    harness.run("add", "-p")
    assert "search for regex?" in harness.out
    assert staged_content(harness) == base.replace("line29", "TWENTYNINE")


def test_search_reports_when_nothing_matches(harness):
    setup_two_hunks(harness)
    harness.answer("/", "nothing-like-this", "n", "n")
    harness.run("add", "-p")
    assert "No hunk matches the given pattern" in harness.out


def test_search_reports_a_malformed_regex(harness):
    setup_two_hunks(harness)
    harness.answer("/", "[unclosed", "n", "n")
    harness.run("add", "-p")
    assert "Malformed search regexp" in harness.out


def test_search_accepts_a_real_regex(harness):
    base, _ = setup_three_hunks(harness)
    harness.answer("/", r"^\+F.*TEEN$", "y", "n", "n")
    harness.run("add", "-p")
    assert staged_content(harness) == base.replace("line15", "FIFTEEN")


def test_navigation_does_not_disturb_file_order(harness):
    """Hunks answered out of order must still be applied front to back."""
    base, work = setup_three_hunks(harness)
    harness.answer("g", "3", "y", "g", "1", "y", "n")
    harness.run("add", "-p")
    assert staged_content(harness) == base.replace("line02", "TWO").replace(
        "line29", "TWENTYNINE"
    )


def test_help_lists_the_navigation_commands(harness):
    setup_two_hunks(harness)
    harness.answer("?", "n", "n")
    harness.run("add", "-p")
    for line in ("j - leave this hunk undecided", "J - leave this hunk undecided",
                 "g - select a hunk to go to", "/ - search for a hunk"):
        assert line in harness.out
