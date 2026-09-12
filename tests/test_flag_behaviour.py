"""Behaviour of options that used to be accepted and ignored."""

import pytest

from conftest import log_xml
from svngit import status as status_mod


# ----------------------------------------------------------------------
# options that are now refused rather than ignored
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv, expected",
    [
        (["merge", "-s", "ours", "feature-x"], "one merge algorithm"),
        (["clone", "--bare", "https://svn.example.com/repo"], "always a working copy"),
        (["checkout", "--orphan", "fresh"], "no history"),
        (["cherry-pick", "--continue"], "no cherry-pick in progress"),
        (["cherry-pick", "-m", "1", "r5"], "no second parent"),
        (["tag", "-s", "v1.0"], "no signed tags"),
        (["blame", "--porcelain", "a.txt"], "machine-readable"),
        (["config", "--add", "k", "v"], "single value"),
        (["ls-files", "--stage"], "no git object ids"),
        (["reset", "--keep", "r3"], "history rewriting"),
        (["init", "--bare"], "svnadmin create"),
        (["pull", "--strategy", "ours"], "one merge algorithm"),
    ],
)
def test_impossible_options_are_refused_with_a_reason(harness, argv, expected):
    code = harness.run(*argv)
    assert code != 0, "%s should be refused" % " ".join(argv)
    assert expected in harness.err, harness.err


# ----------------------------------------------------------------------
# options that are accepted but say they change nothing
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv, expected",
    [
        (["merge", "--no-ff", "feature-x"], "fast-forward"),
        (["push", "--tags"], "nothing left to push"),
        (["push", "--set-upstream"], "single remote"),
        (["pull", "--ff-only"], "fast-forwards"),
        (["fetch", "--prune"], "nothing is cached"),
        (["log", "--decorate"], "no decoration"),
        (["checkout", "--track", "feature-x"], "no upstream to set"),
        (["commit", "--no-verify", "-m", "x"], "no commit hooks"),
    ],
)
def test_no_effect_options_say_so(harness, argv, expected):
    harness.run(*argv)
    assert expected in harness.err, harness.err


# ----------------------------------------------------------------------
# options that now actually work
# ----------------------------------------------------------------------
def test_commit_signoff_appends_a_trailer(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    harness.run("commit", "--signoff", "-m", "a change")
    message = harness.ctx.state.commits[0].message
    assert message.startswith("a change")
    assert "Signed-off-by:" in message


def test_commit_signoff_is_not_duplicated(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    harness.ctx.state.set_config("user.name", "alice")
    harness.run(
        "commit", "-s", "-m", "a change\n\nSigned-off-by: alice <alice@2b1f4c50.svn>"
    )
    assert harness.ctx.state.commits[0].message.count("Signed-off-by:") == 1


def test_commit_reuse_message(harness):
    harness.write("a.txt")
    harness.set_status([("a.txt", "modified")])
    harness.run("add", "a.txt")
    harness.set_log([{"revision": 30, "message": "the original wording"}])
    harness.run("commit", "-C", "r30")
    assert harness.ctx.state.commits[0].message == "the original wording"


def test_add_N_records_intent_without_content(harness):
    """git -N stages an empty blob, which is what makes `add -p` work on a
    file that is brand new."""
    harness.write("new.txt", "line one\nline two\n")
    harness.set_status([("new.txt", "unversioned")])
    harness.run("add", "-N", "new.txt")

    entry = harness.ctx.state.index["new.txt"]
    assert entry.intent
    assert harness.ctx.state.objects.read(entry.blob) == b""

    harness.set_status([("new.txt", "added")])
    report = status_mod.compute(harness.ctx)
    assert {e.path: e.code for e in report.entries} == {"new.txt": "AM"}


def test_intent_to_add_is_not_committed(harness):
    harness.write("new.txt", "content\n")
    harness.set_status([("new.txt", "unversioned")])
    harness.run("add", "-N", "new.txt")
    harness.reset_output()

    code = harness.run("commit", "-m", "should not include it")
    assert code == 1
    assert "no staged content" in harness.err
    assert harness.ctx.state.commits == []


def test_add_p_can_pick_hunks_from_an_intent_to_add_file(harness):
    harness.write("new.txt", "alpha\nbeta\n")
    harness.set_status([("new.txt", "unversioned")])
    harness.run("add", "-N", "new.txt")
    harness.set_status([("new.txt", "added")])
    harness.answer("y")
    harness.run("add", "-p")

    entry = harness.ctx.state.index["new.txt"]
    assert harness.ctx.state.objects.read(entry.blob) == b"alpha\nbeta\n"


def test_blame_L_limits_the_line_range(harness):
    harness.write("a.txt", "one\ntwo\nthree\n")
    harness.svn.respond(
        "blame",
        '<?xml version="1.0"?><blame><target path="a.txt">'
        + "".join(
            '<entry line-number="%d"><commit revision="%d"><author>a</author>'
            "<date>2026-03-01T10:00:00.000000Z</date></commit></entry>" % (n, n)
            for n in (1, 2, 3)
        )
        + "</target></blame>",
        first=True,
    )
    harness.svn.respond("cat", "one\ntwo\nthree\n", first=True)
    harness.run("blame", "-L", "2,3", "a.txt")
    assert "one" not in harness.out
    assert "two" in harness.out and "three" in harness.out


def test_blame_w_ignores_whitespace(harness):
    harness.write("a.txt", "x\n")
    harness.svn.respond(
        "blame", "<blame><target path='a.txt'></target></blame>", first=True
    )
    harness.run("blame", "-w", "a.txt")
    assert "-x -w" in " ".join(harness.svn.argv_for("blame"))


def test_log_all_targets_the_repository_root(harness):
    harness.run("log", "--all")
    assert "https://svn.example.com/repo" in " ".join(harness.svn.argv_for("log"))


def test_diff_unified_passes_the_context_size(harness):
    harness.run("diff", "-U", "7", "r1..r2")
    assert "-U7" in " ".join(harness.svn.argv_for("diff"))


def test_status_untracked_no_hides_untracked(harness):
    harness.write("new.txt")
    harness.write("a.txt")
    harness.set_status([("new.txt", "unversioned"), ("a.txt", "modified")])
    harness.run("status", "-u", "no")
    assert "new.txt" not in harness.out
    assert "a.txt" in harness.out


def test_status_rejects_an_invalid_untracked_mode(harness):
    code = harness.run("status", "--untracked-files", "sometimes")
    assert code != 0
    assert "invalid untracked files mode" in harness.err


def test_config_get_regexp(harness):
    harness.run("config", "svngit.trunk", "main")
    harness.run("config", "user.name", "alice")
    harness.reset_output()
    harness.run("config", "--get-regexp", "^svngit")
    assert "svngit.trunk main" in harness.out
    assert "user.name" not in harness.out


def test_config_bool_canonicalises(harness):
    harness.run("config", "svngit.quiet", "yes")
    harness.reset_output()
    harness.run("config", "--bool", "svngit.quiet")
    assert harness.out.strip() == "true"


def test_config_int_rejects_nonsense(harness):
    harness.run("config", "svngit.depth", "abc")
    harness.reset_output()
    code = harness.run("config", "--int", "svngit.depth")
    assert code != 0


def test_rev_parse_verify_reports_a_bad_revision_by_exit_code(harness):
    code = harness.run("rev-parse", "--verify", "not-a-revision")
    assert code == 1
    assert "Needed a single revision" in harness.err


def test_rev_parse_verify_quiet_says_nothing(harness):
    harness.run("rev-parse", "--verify", "-q", "not-a-revision")
    assert harness.err.strip() == ""


def test_branch_copy_makes_a_server_side_copy(harness):
    harness.svn.respond(
        lambda argv: argv[1] == "info" and "branches/copy-of" in " ".join(argv),
        "",
        returncode=1,
        first=True,
    )
    harness.run("branch", "-c", "copy-of")
    argv = " ".join(harness.svn.argv_for("copy"))
    assert "branches/copy-of" in argv


def test_branch_quiet_suppresses_the_note(harness):
    harness.svn.respond(
        lambda argv: argv[1] == "info" and "branches/new" in " ".join(argv),
        "",
        returncode=1,
        first=True,
    )
    harness.run("branch", "-q", "new")
    assert harness.err.strip() == ""


def test_checkout_detach_updates_to_a_revision(harness):
    harness.run("checkout", "--detach", "r7")
    assert "update -r 7" in " ".join(harness.svn.argv_for("update")).replace(
        "--non-interactive ", ""
    )


def test_tag_contains_filters_by_creation_revision(harness):
    harness.svn.respond(
        "list",
        '<?xml version="1.0"?><lists><list>'
        '<entry kind="dir"><name>old</name></entry>'
        '<entry kind="dir"><name>new</name></entry>'
        "</list></lists>",
        first=True,
    )

    def tag_log(argv):
        joined = " ".join(argv)
        return argv[1] == "log" and "tags/" in joined

    def responder(argv):
        return tag_log(argv) and "tags/old" in " ".join(argv)

    # first=True prepends, so register the catch-all before the specific rule
    # or it shadows it.
    harness.svn.respond(
        tag_log,
        log_xml(
            [{"revision": 20, "paths": [{"action": "A", "path": "/tags/new"}]}]
        ).replace('kind="file"', 'kind="dir" copyfrom-rev="18"'),
        first=True,
    )
    harness.svn.respond(
        responder,
        log_xml(
            [{"revision": 5, "paths": [{"action": "A", "path": "/tags/old"}]}]
        ).replace('kind="file"', 'kind="dir" copyfrom-rev="3"'),
        first=True,
    )
    harness.run("tag", "--contains", "r10")
    assert "new" in harness.out
    assert "old" not in harness.out
