"""The emulated index and the local commit queue -- the parts with no
Subversion counterpart at all."""


def commit(harness, message="a change"):
    return harness.run("commit", "-m", message)


# ----------------------------------------------------------------------
# staging
# ----------------------------------------------------------------------
def test_add_stores_the_staged_content_not_just_its_hash(harness):
    """A file edited after `git add` must still commit the staged version, so
    staging has to keep the bytes."""
    path = harness.write("a.txt", "staged\n")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    path.write_text("edited after staging\n")

    blob = harness.ctx.state.index["a.txt"].blob
    assert harness.ctx.state.objects.read(blob) == b"staged\n"


def test_commit_records_the_staged_content(harness):
    path = harness.write("a.txt", "staged\n")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    path.write_text("edited after staging\n")
    commit(harness)

    queued = harness.ctx.state.commits
    assert len(queued) == 1
    assert harness.ctx.state.objects.read(queued[0].changes[0].blob) == b"staged\n"


def test_commit_clears_the_index_and_queues_locally(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    harness.reset_output()
    commit(harness, "do a thing")

    assert harness.ctx.state.index == {}
    assert [c.message for c in harness.ctx.state.commits] == ["do a thing"]
    # Nothing reached the server.
    assert "commit" not in harness.svn.subcommands
    assert "local" in harness.out


def test_commit_with_nothing_staged_reports_it(harness):
    code = commit(harness)
    assert code == 1
    assert "nothing to commit" in harness.out


def test_commit_a_stages_tracked_changes_first(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified"), ("new.txt", "unversioned")])
    harness.run("commit", "-am", "sweep")
    queued = harness.ctx.state.commits
    assert [c.path for c in queued[0].changes] == ["a.txt"]  # not the untracked file


def test_immediate_mode_commits_straight_to_subversion(harness):
    harness.ctx.state.set_config("svngit.commitmode", "immediate")
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    harness.svn.respond("commit", "Committed revision 43.\n", first=True)
    commit(harness, "straight through")

    argv = harness.svn.argv_for("commit")
    assert argv is not None
    assert "-m" in argv and "straight through" in argv
    assert harness.ctx.state.commits == []


def test_reset_soft_uncommits_a_local_commit(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    commit(harness, "oops")
    harness.reset_output()

    harness.run("reset", "--soft")
    assert harness.ctx.state.commits == []
    assert "a.txt" in harness.ctx.state.index


def test_reset_soft_refuses_when_nothing_is_queued(harness):
    code = harness.run("reset", "--soft")
    assert code != 0
    assert "cannot be un-committed" in harness.err


def test_reset_unstages_and_unschedules_an_add(harness):
    """`git reset` on a newly added file must also undo `svn add`, or the file
    stays scheduled for the next commit."""
    harness.write("new.txt")
    harness.set_status([("new.txt", "unversioned")])
    harness.run("add", "new.txt")
    harness.set_status([("new.txt", "added")])
    harness.run("reset", "new.txt")

    assert "new.txt" not in harness.ctx.state.index
    assert harness.svn.argv_for("revert") is not None


def test_reset_does_not_call_svn_for_a_plain_modification(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    harness.run("reset", "a.txt")
    assert "a.txt" not in harness.ctx.state.index
    assert harness.svn.argv_for("revert") is None


# ----------------------------------------------------------------------
# push replay
# ----------------------------------------------------------------------
def test_push_replays_each_commit_as_its_own_revision(harness):
    harness.write("a.txt", "one\n")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    commit(harness, "first")

    harness.write("a.txt", "two\n")
    harness.run("add", "a.txt")
    commit(harness, "second")

    harness.svn.respond("commit", "Committed revision 43.\n", first=True)
    harness.reset_output()
    harness.run("push")

    commits = harness.svn.all_for("commit")
    assert len(commits) == 2
    assert "first" in " ".join(commits[0])
    assert "second" in " ".join(commits[1])
    assert harness.ctx.state.commits == []


def test_push_leaves_the_working_copy_at_the_latest_content(harness):
    harness.write("a.txt", "one\n")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    commit(harness, "first")
    harness.write("a.txt", "two\n")
    harness.run("add", "a.txt")
    commit(harness, "second")

    harness.svn.respond("commit", "Committed revision 43.\n", first=True)
    harness.run("push")
    assert (harness.wc / "a.txt").read_text() == "two\n"


def test_push_preserves_edits_made_after_the_last_commit(harness):
    """Replay rewinds file content through each queued commit. Work done after
    the last `git commit` must survive that."""
    harness.write("a.txt", "committed\n")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    commit(harness, "first")

    harness.write("a.txt", "uncommitted work in progress\n")
    harness.svn.respond("commit", "Committed revision 43.\n", first=True)
    harness.run("push")

    assert (harness.wc / "a.txt").read_text() == "uncommitted work in progress\n"


def test_push_stops_at_the_first_failure_and_keeps_the_rest_queued(harness):
    for message in ("first", "second"):
        harness.write("a.txt", message + "\n")
        harness.set_status([("a.txt", "modified")])
        harness.run("add", "a.txt")
        commit(harness, message)

    calls = {"n": 0}

    def failing_commit(argv):
        if argv[1] != "commit":
            return False
        calls["n"] += 1
        return calls["n"] == 2

    harness.svn.respond(
        failing_commit, "", returncode=1, stderr="server rejected", first=True
    )
    harness.svn.respond("commit", "Committed revision 43.\n", first=False)

    code = harness.run("push")
    assert code != 0
    # The first revision landed, so only the second may remain queued.
    remaining = [c.message for c in harness.ctx.state.commits]
    assert remaining == ["second"]


def test_push_refuses_when_the_server_has_moved_ahead(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    commit(harness)

    from conftest import info_xml

    harness.svn.respond(
        lambda argv: argv[1] == "info" and "HEAD" in argv,
        info_xml(revision=99, wc_root=str(harness.wc)),
        first=True,
    )
    code = harness.run("push")
    assert code != 0
    assert "git pull" in harness.err
    assert harness.ctx.state.commits  # still queued


def test_push_dry_run_lists_without_committing(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    commit(harness, "pending work")
    harness.reset_output()

    harness.run("push", "--dry-run")
    assert "pending work" in harness.out
    assert "commit" not in harness.svn.subcommands


def test_push_replays_an_added_file_by_scheduling_it(harness):
    harness.write("new.txt", "content\n")
    harness.set_status([("new.txt", "unversioned")])
    harness.run("add", "new.txt")
    commit(harness, "add a file")

    harness.set_status([("new.txt", "added")])
    harness.svn.respond("commit", "Committed revision 43.\n", first=True)
    harness.run("push")
    assert harness.ctx.state.commits == []


def test_merge_refuses_while_commits_are_unpushed(harness):
    """A merge commits the whole working copy root, which would sweep up
    content belonging to queued commits."""
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    commit(harness)

    code = harness.run("merge", "feature-x")
    assert code != 0
    assert "git push" in harness.err
    assert "merge" not in harness.svn.subcommands


# ----------------------------------------------------------------------
# stash
# ----------------------------------------------------------------------
def test_stash_saves_a_patch_and_reverts(harness):
    harness.write("a.txt", "work\n")
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("diff", "Index: a.txt\n--- a.txt\n+++ a.txt\n", first=True)

    harness.run("stash")
    assert len(harness.ctx.state.stash) == 1
    assert harness.svn.argv_for("revert") is not None


def test_stash_with_nothing_to_save(harness):
    harness.run("stash")
    assert "No local changes to save" in harness.out
    assert harness.ctx.state.stash == []


def test_stash_untracked_removes_and_restores_the_file(harness):
    harness.write("scratch.txt", "notes\n")
    harness.set_status([("scratch.txt", "unversioned")])
    harness.run("stash", "-u")

    assert not (harness.wc / "scratch.txt").exists()

    harness.set_status([])
    harness.run("stash", "pop")
    assert (harness.wc / "scratch.txt").read_text() == "notes\n"
    assert harness.ctx.state.stash == []


def test_stash_list_and_drop(harness):
    harness.write("a.txt", "work\n")
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("diff", "Index: a.txt\n", first=True)
    harness.run("stash", "-m", "wip one")
    harness.reset_output()

    harness.run("stash", "list")
    assert "stash@{0}: wip one" in harness.out

    harness.reset_output()
    harness.run("stash", "drop")
    assert harness.ctx.state.stash == []


def test_stash_pop_with_no_entries_errors(harness):
    code = harness.run("stash", "pop")
    assert code != 0
    assert "No stash entries" in harness.err


def test_push_refreshes_the_base_revision(harness):
    """svn commit bumps only the committed paths. Without an update the stale
    BASE makes git log miss the new revision and the next push look rejected."""
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    commit(harness)

    harness.svn.respond("commit", "Committed revision 43.\n", first=True)
    harness.run("push")
    assert harness.svn.argv_for("update") is not None


# ----------------------------------------------------------------------
# a queued commit is part of what the working copy is "based on"
# ----------------------------------------------------------------------
def test_status_is_clean_after_a_deferred_commit(harness):
    """svn still calls the file modified because nothing is pushed, but git
    would call it committed."""
    harness.write("a.txt", "content\n")
    harness.set_status([("a.txt", "unversioned")])
    harness.run("add", "a.txt")
    harness.set_status([("a.txt", "added")])
    commit(harness, "local work")
    harness.reset_output()

    harness.run("status")
    assert (
        "nothing to commit" in harness.out
        or "Changes to be committed" not in harness.out
    )
    assert "local work" in harness.out  # still reported as unpushed


def test_editing_after_a_deferred_commit_shows_as_unstaged(harness):
    path = harness.write("a.txt", "committed\n")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    commit(harness)
    path.write_text("edited afterwards\n")

    from svngit import status as status_mod

    report = status_mod.compute(harness.ctx)
    assert {e.path: e.code for e in report.entries} == {"a.txt": " M"}


def test_diff_after_a_deferred_commit_shows_nothing(harness):
    harness.write("a.txt", "content\n")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    commit(harness)
    harness.reset_output()

    harness.run("diff")
    assert harness.out.strip() == ""


def test_queued_blobs_takes_the_newest_commit_for_a_path(harness):
    for text in ("first\n", "second\n"):
        harness.write("a.txt", text)
        harness.set_status([("a.txt", "modified")])
        harness.run("add", "a.txt")
        commit(harness, text.strip())

    blobs = harness.ctx.state.queued_blobs()
    assert harness.ctx.state.objects.read(blobs["a.txt"]) == b"second\n"


# ----------------------------------------------------------------------
# git stash -p
# ----------------------------------------------------------------------
STASH_BASE = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n"


def setup_stash_patch(harness):
    """Two changes far enough apart to be separate hunks."""
    work = STASH_BASE.replace("two", "TWO").replace("ten", "TEN")
    harness.write("a.txt", work)
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", STASH_BASE, first=True)
    return work


def worktree(harness, path="a.txt"):
    return (harness.wc / path).read_text()


def test_stash_p_removes_the_accepted_hunk_and_keeps_the_rest(harness):
    """The sense is the opposite of `git add -p`: yes means take it away."""
    setup_stash_patch(harness)
    harness.answer("y", "n")
    harness.run("stash", "-p")
    assert worktree(harness) == STASH_BASE.replace("ten", "TEN")
    assert len(harness.ctx.state.stash) == 1


def test_stash_p_declining_everything_saves_nothing(harness):
    work = setup_stash_patch(harness)
    harness.answer("n", "n")
    harness.run("stash", "-p")
    assert worktree(harness) == work
    assert harness.ctx.state.stash == []
    assert "No local changes to save" in harness.out


def test_stash_p_prompt_says_stash_not_stage(harness):
    setup_stash_patch(harness)
    harness.answer("n", "n")
    harness.run("stash", "-p")
    assert "Stash this hunk" in harness.out
    assert "Stage this hunk" not in harness.out


def test_stash_p_pop_restores_the_full_content(harness):
    work = setup_stash_patch(harness)
    harness.answer("y", "n")
    harness.run("stash", "-p")
    harness.set_status([("a.txt", "modified")])

    harness.run("stash", "pop")
    assert worktree(harness) == work
    assert harness.ctx.state.stash == []


def test_stash_p_pop_merges_with_work_done_since(harness):
    """Popping onto a file edited since the stash has to merge, not clobber."""
    setup_stash_patch(harness)
    harness.answer("y", "n")
    harness.run("stash", "-p")

    # An unrelated edit, far from the stashed hunk.
    harness.write("a.txt", worktree(harness).replace("five", "FIVE"))
    harness.set_status([("a.txt", "modified")])
    harness.run("stash", "pop")

    result = worktree(harness)
    assert "TWO" in result  # the stashed hunk came back
    assert "FIVE" in result  # the later edit survived
    assert "TEN" in result  # the hunk that was never stashed


def test_stash_p_operates_on_all_changes_not_just_unstaged(harness):
    """Stash saves staged work too, so the diff is against the last commit."""
    harness.write("a.txt", STASH_BASE.replace("two", "TWO"))
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", STASH_BASE, first=True)
    harness.run("add", "a.txt")
    harness.reset_output()

    harness.answer("y")
    harness.run("stash", "-p")
    assert worktree(harness) == STASH_BASE
    assert "a.txt" not in harness.ctx.state.index


def test_stash_p_ignores_untracked_files(harness):
    harness.write("scratch.txt", "notes\n")
    harness.set_status([("scratch.txt", "unversioned")])
    harness.run("stash", "-p")
    assert (harness.wc / "scratch.txt").exists()
    assert harness.ctx.state.stash == []


def test_stash_p_show_renders_what_was_stashed(harness):
    setup_stash_patch(harness)
    harness.answer("y", "n")
    harness.run("stash", "-p")
    harness.reset_output()

    harness.run("stash", "show")
    assert "+TWO" in harness.out
    assert "TEN" not in harness.out  # that hunk was never stashed


def test_stash_p_supports_a_message(harness):
    setup_stash_patch(harness)
    harness.answer("y", "n")
    harness.run("stash", "-p", "-m", "half the work")
    assert harness.ctx.state.stash[0].message == "half the work"


def test_stash_p_leaves_the_tree_untouched_when_quitting_first(harness):
    work = setup_stash_patch(harness)
    harness.answer("q")
    harness.run("stash", "-p")
    assert worktree(harness) == work
    assert harness.ctx.state.stash == []


def test_stash_p_with_split_hunks(harness):
    """Split sub-hunks sit inside each other's context, so an exact restore
    matters more here than patch matching."""
    work = STASH_BASE.replace("two", "TWO").replace("four", "FOUR")
    harness.write("a.txt", work)
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", STASH_BASE, first=True)

    harness.answer("s", "y", "n")
    harness.run("stash", "-p")
    assert worktree(harness) == STASH_BASE.replace("four", "FOUR")

    harness.set_status([("a.txt", "modified")])
    harness.run("stash", "pop")
    assert worktree(harness) == work
