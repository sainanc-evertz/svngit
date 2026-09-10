import pytest

from svngit.errors import UsageError, SvnGitError
from svngit.layout import Layout
from svngit import revisions as rev_mod

from conftest import info_xml


# ----------------------------------------------------------------------
# layout
# ----------------------------------------------------------------------
def test_trunk_maps_to_the_trunk_directory():
    layout = Layout()
    assert layout.branch_path("trunk") == "trunk"
    assert layout.branch_path("feature-x") == "branches/feature-x"
    assert layout.tag_path("v1.0") == "tags/v1.0"


def test_branch_name_from_relative_url():
    layout = Layout()
    assert layout.branch_name("^/trunk") == "trunk"
    assert layout.branch_name("^/trunk/src/app") == "trunk"
    assert layout.branch_name("^/branches/feature-x") == "feature-x"
    # A checkout of a subdirectory still belongs to its branch.
    assert layout.branch_name("^/branches/feature-x/src") == "feature-x"
    assert layout.branch_name("^/tags/v1.0") == "tags/v1.0"


def test_custom_layout_names_are_honoured():
    layout = Layout(trunk="main", branches="feature", tags="releases")
    assert layout.branch_path("x") == "feature/x"
    assert layout.branch_name("^/feature/x") == "x"
    assert layout.branch_name("^/main") == "trunk"


def test_flat_repository_reports_trunk_and_refuses_branches():
    layout = Layout(standard=False)
    assert layout.branch_name("^/anything") == "trunk"
    with pytest.raises(SvnGitError):
        layout.branch_path("feature")


# ----------------------------------------------------------------------
# revisions
# ----------------------------------------------------------------------
def test_head_resolves_to_svn_base_not_svn_head(harness):
    """git HEAD is the working copy's base revision, not the server's tip."""
    assert rev_mod.resolve(harness.ctx, "HEAD", str(harness.wc)) == 42
    info_calls = harness.svn.all_for("info")
    assert not any("-r" in argv and "HEAD" in argv for argv in info_calls)


def test_upstream_aliases_resolve_to_svn_head(harness):
    harness.svn.respond(
        lambda argv: argv[1] == "info" and "HEAD" in argv,
        info_xml(revision=99, wc_root=str(harness.wc)),
        first=True,
    )
    assert rev_mod.resolve(harness.ctx, "@{u}", str(harness.wc)) == 99
    assert rev_mod.resolve(harness.ctx, "origin/HEAD", str(harness.wc)) == 99


def test_plain_and_r_prefixed_numbers(harness):
    assert rev_mod.resolve(harness.ctx, "1234", str(harness.wc)) == 1234
    assert rev_mod.resolve(harness.ctx, "r1234", str(harness.wc)) == 1234


def test_tilde_walks_back_through_the_paths_own_log(harness):
    harness.set_log([{"revision": 42}, {"revision": 30}, {"revision": 12}])
    assert rev_mod.resolve(harness.ctx, "HEAD~2", str(harness.wc)) == 12
    assert rev_mod.resolve(harness.ctx, "HEAD^", str(harness.wc)) == 30


def test_tilde_past_the_start_of_history_is_an_error(harness):
    harness.set_log([{"revision": 42}])
    with pytest.raises(UsageError):
        rev_mod.resolve(harness.ctx, "HEAD~5", str(harness.wc))


def test_caret_with_a_parent_number_explains_itself(harness):
    with pytest.raises(UsageError) as excinfo:
        rev_mod.resolve(harness.ctx, "HEAD^2", str(harness.wc))
    assert "merge parent" in str(excinfo.value)


def test_range_becomes_an_svn_revision_range(harness):
    assert rev_mod.to_svn_range(harness.ctx, "r10..r20", str(harness.wc)) == "10:20"
    assert rev_mod.to_svn_range(harness.ctx, "r10...r20", str(harness.wc)) == "10:20"


def test_open_ended_range_uses_base(harness):
    assert rev_mod.to_svn_range(harness.ctx, "r10..", str(harness.wc)) == "10:42"


def test_split_range():
    assert rev_mod.split_range("a..b") == ("a", "b")
    assert rev_mod.split_range("a...b") == ("a", "b")
    assert rev_mod.split_range("a") == (None, "a")
    assert rev_mod.split_range("..b") == (None, "b")
