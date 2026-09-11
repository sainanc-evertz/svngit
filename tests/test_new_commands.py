"""grep, check-ignore, apply, archive, format-patch, shortlog, describe,
sparse-checkout, difftool/mergetool and the rest of the added surface."""

import pytest

from conftest import log_xml


# ----------------------------------------------------------------------
# grep
# ----------------------------------------------------------------------
def setup_tree(harness):
    harness.write("a.txt", "alpha\nbeta\nGAMMA\n")
    harness.write("src/b.txt", "beta again\ndelta\n")
    harness.write("junk.tmp", "beta in junk\n")
    harness.set_status([("junk.tmp", "unversioned")])


def test_grep_searches_tracked_files(harness):
    setup_tree(harness)
    harness.run("grep", "beta")
    assert "a.txt:beta" in harness.out
    assert "src/b.txt:beta again" in harness.out


def test_grep_skips_untracked_files(harness):
    setup_tree(harness)
    harness.run("grep", "beta")
    assert "junk.tmp" not in harness.out


def test_grep_untracked_includes_them(harness):
    setup_tree(harness)
    harness.run("grep", "--untracked", "beta")
    assert "junk.tmp" in harness.out


def test_grep_never_descends_into_dot_svn(harness):
    setup_tree(harness)
    (harness.wc / ".svn" / "secret.txt").write_text("beta\n")
    harness.run("grep", "beta")
    assert ".svn" not in harness.out


def test_grep_ignore_case_and_line_numbers(harness):
    setup_tree(harness)
    harness.run("grep", "-in", "gamma")
    assert "a.txt:3:GAMMA" in harness.out


def test_grep_files_with_matches(harness):
    setup_tree(harness)
    harness.run("grep", "-l", "beta")
    assert harness.out.split() == ["a.txt", "src/b.txt"]


def test_grep_count(harness):
    setup_tree(harness)
    harness.run("grep", "-c", "beta")
    assert "a.txt:1" in harness.out


def test_grep_invert_match(harness):
    harness.write("a.txt", "keep\ndrop\n")
    harness.set_status([])
    harness.run("grep", "-v", "drop")
    assert "a.txt:keep" in harness.out
    assert "drop" not in harness.out


def test_grep_fixed_strings_escapes_the_pattern(harness):
    harness.write("a.txt", "a.b\naxb\n")
    harness.set_status([])
    harness.run("grep", "-F", "a.b")
    assert "a.b" in harness.out
    assert "axb" not in harness.out


def test_grep_word_regexp(harness):
    harness.write("a.txt", "tester\ntest\n")
    harness.set_status([])
    harness.run("grep", "-w", "test")
    assert "a.txt:test" in harness.out
    assert "tester" not in harness.out


def test_grep_exit_code_when_nothing_matches(harness):
    setup_tree(harness)
    assert harness.run("grep", "nowhere-to-be-found") == 1


def test_grep_limits_to_given_paths(harness):
    setup_tree(harness)
    harness.run("grep", "beta", "src")
    assert "src/b.txt" in harness.out
    assert "a.txt:" not in harness.out


def test_grep_reports_binary_matches_without_the_line(harness):
    (harness.wc / "blob.bin").write_bytes(b"prefix\x00needle\n")
    harness.set_status([])
    harness.run("grep", "needle")
    assert "Binary file blob.bin matches" in harness.out


# ----------------------------------------------------------------------
# check-ignore
# ----------------------------------------------------------------------
def test_check_ignore_matches_svn_ignore(harness):
    harness.svn.respond("propget", "*.log\nbuild\n", first=True)
    assert harness.run("check-ignore", "debug.log") == 0
    assert "debug.log" in harness.out


def test_check_ignore_reports_no_match_by_exit_code(harness):
    harness.svn.respond("propget", "", first=True)
    assert harness.run("check-ignore", "source.c") == 1


def test_check_ignore_falls_back_to_svn_defaults(harness):
    harness.svn.respond("propget", "", first=True)
    assert harness.run("check-ignore", "module.pyc") == 0


def test_check_ignore_verbose_names_the_source(harness):
    harness.svn.respond("propget", "*.log\n", first=True)
    harness.run("check-ignore", "-v", "debug.log")
    assert "svn:ignore" in harness.out and "*.log" in harness.out


def test_check_ignore_works_on_a_path_that_does_not_exist(harness):
    """Reading the patterns rather than asking `svn status` is what allows
    this; status can only speak about files that are there."""
    harness.svn.respond("propget", "*.log\n", first=True)
    assert harness.run("check-ignore", "never-created.log") == 0


# ----------------------------------------------------------------------
# apply
# ----------------------------------------------------------------------
PATCH = """\
diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
"""


def write_patch(harness, text=PATCH):
    path = harness.wc / "change.patch"
    path.write_text(text)
    return str(path)


def test_apply_changes_the_working_copy(harness):
    harness.write("a.txt", "one\ntwo\nthree\n")
    harness.set_status([])
    assert harness.run("apply", write_patch(harness)) == 0
    assert (harness.wc / "a.txt").read_text() == "one\nTWO\nthree\n"


def test_apply_check_does_not_write(harness):
    harness.write("a.txt", "one\ntwo\nthree\n")
    harness.set_status([])
    assert harness.run("apply", "--check", write_patch(harness)) == 0
    assert (harness.wc / "a.txt").read_text() == "one\ntwo\nthree\n"


def test_apply_reverse(harness):
    harness.write("a.txt", "one\nTWO\nthree\n")
    harness.set_status([])
    harness.run("apply", "-R", write_patch(harness))
    assert (harness.wc / "a.txt").read_text() == "one\ntwo\nthree\n"


def test_apply_refuses_a_patch_that_does_not_fit(harness):
    harness.write("a.txt", "completely\ndifferent\ncontent\n")
    harness.set_status([])
    code = harness.run("apply", write_patch(harness))
    assert code != 0
    assert "no files were changed" in harness.err
    assert (harness.wc / "a.txt").read_text() == "completely\ndifferent\ncontent\n"


def test_apply_is_all_or_nothing_across_files(harness):
    """The first file must not be written when the second fails."""
    harness.write("a.txt", "one\ntwo\nthree\n")
    harness.write("b.txt", "nothing\nlike\nthe patch\n")
    harness.set_status([])
    combined = PATCH + PATCH.replace("a.txt", "b.txt")
    code = harness.run("apply", write_patch(harness, combined))
    assert code != 0
    assert (harness.wc / "a.txt").read_text() == "one\ntwo\nthree\n"


def test_apply_stat(harness):
    harness.write("a.txt", "one\ntwo\nthree\n")
    harness.set_status([])
    harness.run("apply", "--stat", write_patch(harness))
    assert "1 file changed" in harness.out


def test_apply_strip_level(harness):
    harness.write("a.txt", "one\ntwo\nthree\n")
    harness.set_status([])
    deep = PATCH.replace("a/a.txt", "x/y/a.txt").replace("b/a.txt", "x/y/a.txt")
    harness.run("apply", "-p", "2", write_patch(harness, deep))
    assert (harness.wc / "a.txt").read_text() == "one\nTWO\nthree\n"


# ----------------------------------------------------------------------
# shortlog
# ----------------------------------------------------------------------
def test_shortlog_groups_by_author(harness):
    harness.set_log([
        {"revision": 3, "author": "alice", "message": "third"},
        {"revision": 2, "author": "bob", "message": "second"},
        {"revision": 1, "author": "alice", "message": "first"},
    ])
    harness.run("shortlog")
    assert "alice (2):" in harness.out
    assert "      third" in harness.out
    assert "bob (1):" in harness.out


def test_shortlog_summary_and_numbered(harness):
    harness.set_log([
        {"revision": 3, "author": "alice", "message": "third"},
        {"revision": 2, "author": "bob", "message": "second"},
        {"revision": 1, "author": "alice", "message": "first"},
    ])
    harness.run("shortlog", "-sn")
    lines = [l for l in harness.out.splitlines() if l.strip()]
    assert lines[0].split() == ["2", "alice"]
    assert lines[1].split() == ["1", "bob"]


# ----------------------------------------------------------------------
# describe
# ----------------------------------------------------------------------
def tags_listing(harness, names):
    harness.svn.respond(
        "list",
        '<?xml version="1.0"?><lists><list>%s</list></lists>'
        % "".join('<entry kind="dir"><name>%s</name></entry>' % n for n in names),
        first=True,
    )


def tag_created_at(harness, copyfrom):
    harness.svn.respond(
        lambda argv: argv[1] == "log" and "tags/" in " ".join(argv),
        log_xml([{"revision": copyfrom + 1, "paths": [{"action": "A", "path": "/tags/v1.0"}]}])
        .replace('kind="file"', 'kind="dir" copyfrom-rev="%d"' % copyfrom),
        first=True,
    )


def test_describe_names_the_nearest_tag(harness):
    tags_listing(harness, ["v1.0"])
    tag_created_at(harness, 42)
    harness.run("describe")
    assert harness.out.strip() == "v1.0"


def test_describe_counts_revisions_since_the_tag(harness):
    tags_listing(harness, ["v1.0"])
    tag_created_at(harness, 40)
    harness.svn.respond(
        lambda argv: argv[1] == "log" and "tags/" not in " ".join(argv),
        log_xml([{"revision": 42}, {"revision": 41}]),
        first=True,
    )
    harness.run("describe")
    assert harness.out.strip() == "v1.0-2-r42"


def test_describe_without_tags_needs_always(harness):
    tags_listing(harness, [])
    code = harness.run("describe")
    assert code != 0
    assert "no tags can describe" in harness.err

    harness.reset_output()
    tags_listing(harness, [])
    harness.run("describe", "--always")
    assert harness.out.strip() == "r42"


# ----------------------------------------------------------------------
# archive
# ----------------------------------------------------------------------
def test_archive_needs_an_output_file(harness):
    code = harness.run("archive", "HEAD")
    assert code != 0
    assert "-o <file>" in harness.err


def test_archive_rejects_an_unknown_format(harness):
    code = harness.run("archive", "--format", "rar", "-o", "out.rar", "HEAD")
    assert code != 0
    assert "unknown archive format" in harness.err


def test_archive_lists_supported_formats(harness):
    harness.run("archive", "--list")
    assert "zip" in harness.out and "tar" in harness.out


# ----------------------------------------------------------------------
# sparse-checkout
# ----------------------------------------------------------------------
def test_sparse_checkout_set_excludes_the_rest(harness):
    (harness.wc / "keep").mkdir()
    (harness.wc / "drop").mkdir()
    harness.run("sparse-checkout", "set", "keep")

    updates = [" ".join(a) for a in harness.svn.all_for("update")]
    assert any("--set-depth exclude" in u and "drop" in u for u in updates)
    assert any("--set-depth infinity" in u and "keep" in u for u in updates)


def test_sparse_checkout_list_reports_the_cone(harness):
    (harness.wc / "keep").mkdir()
    harness.run("sparse-checkout", "set", "keep")
    harness.reset_output()
    harness.run("sparse-checkout", "list")
    assert harness.out.strip() == "keep"


def test_sparse_checkout_list_without_one(harness):
    assert harness.run("sparse-checkout", "list") == 1
    assert "not sparse" in harness.err


def test_sparse_checkout_disable_restores_full_depth(harness):
    harness.run("sparse-checkout", "disable")
    assert "--set-depth infinity" in " ".join(harness.svn.argv_for("update"))


def test_sparse_checkout_refuses_no_cone(harness):
    code = harness.run("sparse-checkout", "set", "--no-cone", "keep")
    assert code != 0
    assert "whole directories" in harness.err


# ----------------------------------------------------------------------
# mergetool
# ----------------------------------------------------------------------
def test_mergetool_with_no_conflicts(harness):
    harness.run("mergetool")
    assert "No files need merging" in harness.out


def test_mergetool_resolves_after_the_tool_runs(harness, monkeypatch):
    harness.write("a.txt", "conflicted\n")
    (harness.wc / "a.txt.mine").write_text("mine\n")
    (harness.wc / "a.txt.r1").write_text("old\n")
    (harness.wc / "a.txt.r2").write_text("new\n")
    harness.set_status([("a.txt", "conflicted")])
    harness.ctx.state.set_config("merge.tool", "true")

    calls = []
    monkeypatch.setattr(
        "subprocess.run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})()
    )
    harness.answer("y")
    harness.run("mergetool")

    assert calls and calls[0][0] == "true"
    assert "resolve" in harness.svn.subcommands


# ----------------------------------------------------------------------
# commands with no Subversion meaning
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "command, expected",
    [
        ("bundle", "svnadmin dump"),
        ("range-diff", "rewritable history"),
        ("rerere", "no conflict resolutions"),
        ("filter-branch", "cannot be rewritten"),
        ("fast-export", "git svn clone"),
        ("maintenance", "no scheduled upkeep"),
        ("fsck", "svnadmin verify"),
        ("gitk", "no GUI"),
        ("am", "git apply"),
    ],
)
def test_meaningless_commands_explain_themselves(harness, command, expected):
    code = harness.run(command)
    assert code == 128
    assert expected in harness.err
