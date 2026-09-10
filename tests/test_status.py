"""Subversion reports one state per path; git reports two. These tests pin
down the synthesis."""

from svngit import status as status_mod
from svngit.state import ADD, MODIFY


def codes(harness):
    report = status_mod.compute(harness.ctx)
    return {entry.path: entry.code for entry in report.entries}


def test_unversioned_is_untracked(harness):
    harness.write("new.txt")
    harness.set_status([("new.txt", "unversioned")])
    assert codes(harness) == {"new.txt": "??"}


def test_modified_and_unstaged(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    assert codes(harness) == {"a.txt": " M"}


def test_modified_and_staged(harness):
    harness.write("a.txt", "one\n")
    harness.set_status([("a.txt", "modified")])
    harness.ctx.state.stage("a.txt", MODIFY, harness.ctx.snapshot("a.txt"))
    assert codes(harness) == {"a.txt": "M "}


def test_staged_then_edited_again_shows_both_columns(harness):
    path = harness.write("a.txt", "one\n")
    harness.set_status([("a.txt", "modified")])
    harness.ctx.state.stage("a.txt", MODIFY, harness.ctx.snapshot("a.txt"))
    path.write_text("two\n")
    assert codes(harness) == {"a.txt": "MM"}


def test_svn_added_is_always_staged(harness):
    """There is no added-but-unstaged state in Subversion: scheduling an add
    *is* staging it."""
    harness.write("new.txt")
    harness.set_status([("new.txt", "added")])
    assert codes(harness) == {"new.txt": "A "}


def test_added_then_edited(harness):
    path = harness.write("new.txt", "one\n")
    harness.set_status([("new.txt", "added")])
    harness.ctx.state.stage("new.txt", ADD, harness.ctx.snapshot("new.txt"))
    path.write_text("two\n")
    assert codes(harness) == {"new.txt": "AM"}


def test_svn_deleted_is_a_staged_deletion(harness):
    harness.set_status([("gone.txt", "deleted")])
    assert codes(harness) == {"gone.txt": "D "}


def test_svn_missing_is_an_unstaged_deletion(harness):
    """`missing` means removed from disk but still versioned -- git calls that
    an unstaged deletion."""
    harness.set_status([("gone.txt", "missing")])
    assert codes(harness) == {"gone.txt": " D"}


def test_conflict_is_unmerged(harness):
    harness.set_status([("a.txt", "conflicted")])
    assert codes(harness) == {"a.txt": "UU"}


def test_property_only_change_counts_as_modified(harness):
    harness.set_status([("a.txt", "normal", "modified")])
    assert codes(harness) == {"a.txt": " M"}


def test_normal_files_are_omitted(harness):
    harness.set_status([("a.txt", "normal"), ("b.txt", "modified")])
    assert codes(harness) == {"b.txt": " M"}


def test_staged_path_svn_considers_clean_still_appears(harness):
    """Stage a file, then undo the edit by hand: git still lists it as staged
    so that `git reset` has something to act on."""
    harness.write("a.txt", "one\n")
    harness.set_status([])
    harness.ctx.state.stage("a.txt", MODIFY, harness.ctx.snapshot("a.txt"))
    assert codes(harness) == {"a.txt": "M "}


def test_report_partitions_entries(harness):
    harness.write("staged.txt", "x\n")
    harness.write("dirty.txt", "x\n")
    harness.set_status(
        [("staged.txt", "modified"), ("dirty.txt", "modified"), ("new.txt", "unversioned")]
    )
    harness.ctx.state.stage("staged.txt", MODIFY, harness.ctx.snapshot("staged.txt"))
    report = status_mod.compute(harness.ctx)
    assert [e.path for e in report.staged] == ["staged.txt"]
    assert [e.path for e in report.unstaged] == ["dirty.txt"]
    assert [e.path for e in report.untracked] == ["new.txt"]
    assert not report.clean
