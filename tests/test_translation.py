"""The contract of a translation layer: which svn command line each git
command produces."""

from conftest import info_xml


def argv_string(harness, subcommand):
    argv = harness.svn.argv_for(subcommand)
    assert argv is not None, "expected an `svn %s`, got %s" % (
        subcommand,
        harness.svn.subcommands,
    )
    return " ".join(argv)


# ----------------------------------------------------------------------
# clone
# ----------------------------------------------------------------------
def test_clone_descends_into_trunk(harness):
    harness.run("clone", "https://svn.example.com/repo")
    assert argv_string(harness, "checkout").endswith(
        "https://svn.example.com/repo/trunk repo"
    )


def test_clone_full_takes_the_whole_repository(harness):
    harness.run("clone", "--full", "https://svn.example.com/repo")
    assert argv_string(harness, "checkout").endswith(
        "https://svn.example.com/repo repo"
    )


def test_clone_branch_maps_to_the_branches_directory(harness):
    harness.run("clone", "-b", "feature-x", "https://svn.example.com/repo")
    assert "https://svn.example.com/repo/branches/feature-x" in argv_string(
        harness, "checkout"
    )


def test_clone_honours_an_explicit_destination(harness):
    harness.run("clone", "https://svn.example.com/repo", "mydir")
    assert argv_string(harness, "checkout").endswith("mydir")


# ----------------------------------------------------------------------
# add / rm / mv
# ----------------------------------------------------------------------
def test_add_new_file_schedules_it_with_svn(harness):
    harness.write("new.txt")
    harness.set_status([("new.txt", "unversioned")])
    harness.run("add", "new.txt")
    assert harness.svn.argv_for("add") == [
        "svn",
        "add",
        "--non-interactive",
        "--parents",
        str(harness.wc / "new.txt"),
    ]


def test_add_modified_file_touches_only_the_index(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    # Subversion has no staging area, so a modification must not call svn at all.
    assert harness.svn.argv_for("add") is None
    assert harness.ctx.state.is_staged("a.txt")


def test_add_a_deleted_file_stages_the_removal(harness):
    harness.set_status([("gone.txt", "missing")])
    harness.run("add", "gone.txt")
    assert argv_string(harness, "delete").endswith(
        "--force %s" % (harness.wc / "gone.txt")
    )


def test_add_dot_stages_everything(harness):
    harness.write("new.txt")
    harness.write("a.txt")
    harness.set_status([("new.txt", "unversioned"), ("a.txt", "modified")])
    harness.run("add", ".")
    staged = harness.ctx.state.index
    assert set(staged) == {"new.txt", "a.txt"}


def test_add_u_skips_untracked(harness):
    harness.write("new.txt")
    harness.write("a.txt")
    harness.set_status([("new.txt", "unversioned"), ("a.txt", "modified")])
    harness.run("add", "-u")
    assert set(harness.ctx.state.index) == {"a.txt"}


def test_add_patch_stages_a_chosen_hunk(harness):
    harness.write("a.txt", "one\nCHANGED\nthree\n")
    harness.set_status([("a.txt", "modified")])
    harness.svn.respond("cat", "one\ntwo\nthree\n", first=True)
    harness.answer("y")
    assert harness.run("add", "-p") == 0
    assert harness.ctx.state.is_staged("a.txt")


def test_rm_maps_to_svn_delete(harness):
    harness.write("a.txt")
    harness.run("rm", "a.txt")
    assert argv_string(harness, "delete").endswith(str(harness.wc / "a.txt"))


def test_rm_cached_keeps_the_file_on_disk(harness):
    harness.write("a.txt")
    harness.run("rm", "--cached", "a.txt")
    assert "--keep-local" in argv_string(harness, "delete")


def test_mv_maps_to_svn_move(harness):
    harness.write("a.txt")
    harness.run("mv", "a.txt", "b.txt")
    argv = argv_string(harness, "move")
    assert str(harness.wc / "a.txt") in argv and str(harness.wc / "b.txt") in argv


# ----------------------------------------------------------------------
# branching
# ----------------------------------------------------------------------
def test_checkout_branch_maps_to_svn_switch(harness):
    harness.run("checkout", "feature-x")
    assert argv_string(harness, "switch").startswith(
        "svn switch --non-interactive https://svn.example.com/repo/branches/feature-x"
    )


def test_checkout_b_copies_then_switches(harness):
    harness.svn.respond(
        lambda argv: argv[1] == "info" and "branches/new-thing" in " ".join(argv),
        "",
        returncode=1,
        first=True,
    )
    harness.run("checkout", "-b", "new-thing")
    copy = argv_string(harness, "copy")
    assert "https://svn.example.com/repo/trunk" in copy
    assert "https://svn.example.com/repo/branches/new-thing" in copy
    assert "switch" in harness.svn.subcommands


def test_creating_a_branch_warns_that_it_is_a_server_side_commit(harness):
    harness.svn.respond(
        lambda argv: argv[1] == "info" and "branches/new-thing" in " ".join(argv),
        "",
        returncode=1,
        first=True,
    )
    harness.run("branch", "new-thing")
    assert "visible to everyone immediately" in harness.err


def test_branch_delete_maps_to_svn_rm_on_the_url(harness):
    harness.run("branch", "-d", "old-thing")
    argv = argv_string(harness, "delete")
    assert "https://svn.example.com/repo/branches/old-thing" in argv
    assert "-m" in argv


def test_branch_list_marks_the_current_branch(harness):
    harness.svn.respond(
        "list",
        '<?xml version="1.0"?><lists><list><entry kind="dir"><name>feature-x</name></entry></list></lists>',
    )
    harness.run("branch")
    assert "* trunk" in harness.out
    assert "  feature-x" in harness.out


def test_tag_maps_to_a_copy_into_tags(harness):
    harness.svn.respond(
        lambda argv: argv[1] == "info" and "tags/v1.0" in " ".join(argv),
        "",
        returncode=1,
        first=True,
    )
    harness.run("tag", "v1.0")
    assert "https://svn.example.com/repo/tags/v1.0" in argv_string(harness, "copy")


def test_merge_maps_to_svn_merge_then_commits_the_root(harness):
    harness.run("merge", "feature-x")
    merge = argv_string(harness, "merge")
    assert "https://svn.example.com/repo/branches/feature-x" in merge
    commit = argv_string(harness, "commit")
    # The root must be in the commit or svn:mergeinfo is lost.
    assert str(harness.wc) in commit
    assert "Merge branch 'feature-x'" in commit


def test_git_revert_is_a_reverse_merge_not_svn_revert(harness):
    harness.set_log([{"revision": 30, "message": "bad change"}])
    harness.run("revert", "r30")
    assert "-c -30" in argv_string(harness, "merge")


def test_cherry_pick_is_a_forward_merge_of_one_revision(harness):
    harness.set_log(
        [
            {
                "revision": 30,
                "message": "nice change",
                "paths": [{"action": "M", "path": "/branches/feature-x/a.txt"}],
            }
        ]
    )
    harness.run("cherry-pick", "r30")
    merge = argv_string(harness, "merge")
    assert "-c 30" in merge
    # The merge source is inferred from the revision's own paths.
    assert "https://svn.example.com/repo/branches/feature-x" in merge


# ----------------------------------------------------------------------
# history
# ----------------------------------------------------------------------
def test_log_renders_git_style(harness):
    harness.set_log([{"revision": 42, "author": "alice", "message": "do a thing"}])
    harness.run("log")
    assert "commit r42" in harness.out
    assert "Author: alice <alice@2b1f4c50.svn>" in harness.out
    assert "    do a thing" in harness.out


def test_log_oneline(harness):
    harness.set_log([{"revision": 42, "message": "do a thing"}])
    harness.run("log", "--oneline")
    assert harness.out.strip() == "r42 do a thing"


def test_log_n_maps_to_svn_limit(harness):
    harness.run("log", "-n", "5")
    assert "-l 5" in argv_string(harness, "log")


def test_log_numeric_shorthand(harness):
    harness.run("log", "-3")
    assert "-l 3" in argv_string(harness, "log")


def test_log_grep_maps_to_svn_search(harness):
    harness.run("log", "--grep", "fix")
    assert "--search fix" in argv_string(harness, "log")


def test_log_patch_asks_svn_for_the_diff(harness):
    harness.run("log", "-p")
    assert "--diff" in argv_string(harness, "log")


def test_show_uses_a_single_revision_diff(harness):
    harness.set_log([{"revision": 30, "message": "a change"}])
    harness.run("show", "r30")
    assert "commit r30" in harness.out
    assert "-c 30" in argv_string(harness, "diff")


def test_diff_with_revision_range(harness):
    harness.run("diff", "r10..r20")
    assert "-r 10:20" in argv_string(harness, "diff")


def test_diff_head_compares_worktree_against_base(harness):
    harness.run("diff", "HEAD")
    argv = argv_string(harness, "diff")
    assert "-r" not in argv


# ----------------------------------------------------------------------
# sync
# ----------------------------------------------------------------------
def test_pull_maps_to_svn_update(harness):
    harness.run("pull")
    assert "update" in harness.svn.subcommands


def test_fetch_reports_without_updating(harness):
    harness.svn.respond(
        lambda argv: argv[1] == "info" and "HEAD" in argv,
        info_xml(revision=50, wc_root=str(harness.wc)),
        first=True,
    )
    harness.set_log([{"revision": 50, "message": "newer work"}])
    harness.run("fetch")
    assert "update" not in harness.svn.subcommands
    assert "r42..r50" in harness.out


def test_push_with_nothing_queued_is_a_no_op(harness):
    harness.run("push")
    assert "Everything up-to-date" in harness.out
    assert "commit" not in harness.svn.subcommands


# ----------------------------------------------------------------------
# plumbing
# ----------------------------------------------------------------------
def test_remote_v_shows_the_repository_url(harness):
    harness.run("remote", "-v")
    assert "origin\thttps://svn.example.com/repo/trunk (fetch)" in harness.out


def test_remote_add_explains_why_it_cannot_work(harness):
    code = harness.run("remote", "add", "other", "https://example.com")
    assert code != 0
    assert "exactly one repository URL" in harness.err


def test_rev_parse_abbrev_ref_gives_the_branch(harness):
    harness.run("rev-parse", "--abbrev-ref", "HEAD")
    assert harness.out.strip() == "trunk"


def test_rev_parse_show_toplevel(harness):
    harness.run("rev-parse", "--show-toplevel")
    assert harness.out.strip() == str(harness.wc)


def test_rev_parse_head_gives_the_base_revision(harness):
    harness.run("rev-parse", "HEAD")
    assert harness.out.strip() == "r42"


def test_config_round_trips(harness):
    harness.run("config", "svngit.commitmode", "immediate")
    harness.reset_output()
    harness.run("config", "svngit.commitmode")
    assert harness.out.strip() == "immediate"


# ----------------------------------------------------------------------
# dispatch
# ----------------------------------------------------------------------
def test_aliases_resolve(harness):
    harness.run("st")
    assert "On branch trunk" in harness.out


def test_rebase_explains_the_absence(harness):
    code = harness.run("rebase", "main")
    assert code == 128
    assert "no way to rewrite history" in harness.err


def test_unknown_command_suggests_alternatives(harness):
    code = harness.run("stauts")
    assert code == 1
    assert "status" in harness.err


def test_dry_run_does_not_run_mutating_commands(tmp_path, monkeypatch):
    from conftest import Harness

    wc = tmp_path / "wc"
    (wc / ".svn").mkdir(parents=True)
    monkeypatch.setenv("SVNGIT_STATE_DIR", str(tmp_path / "state"))
    harness = Harness(wc, tmp_path / "state")
    harness.ctx.dry_run = True
    harness.svn.dry_run = True
    harness.write("a.txt")
    harness.set_status([("a.txt", "unversioned")])
    harness.run("add", "a.txt")
    assert "would run" in harness.err


def test_status_does_not_contact_the_server_by_default(harness):
    """git status is local and instant; asking svn for HEAD would make every
    status a network round trip."""
    harness.run("status")
    assert not any("HEAD" in argv for argv in harness.svn.all_for("info"))


def test_status_checks_upstream_when_asked(harness):
    harness.ctx.state.set_config("svngit.checkupstream", "true")
    harness.svn.respond(
        lambda argv: argv[1] == "info" and "HEAD" in argv,
        info_xml(revision=50, wc_root=str(harness.wc)),
        first=True,
    )
    harness.run("status")
    assert "8 revisions available" in harness.out
