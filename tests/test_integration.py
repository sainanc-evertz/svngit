"""End-to-end tests against a real Subversion repository over file://.

These verify semantics the recording harness cannot: that the svn command
lines svngit builds are actually accepted, and that the resulting repository
state is what a git user would expect.

Skipped automatically when svn/svnadmin are not installed.
"""

from __future__ import annotations

import subprocess
import sys

from conftest import needs_svn

pytestmark = needs_svn


def svn(*args, cwd=None):
    return subprocess.run(
        ["svn", "--non-interactive", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_status_add_commit_push_round_trip(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "hello.txt").write_text("hello\n")

    code, out, _ = cli("status")
    assert code == 0
    assert "Untracked files" in out
    assert "hello.txt" in out

    assert cli("add", "hello.txt")[0] == 0
    code, out, _ = cli("status")
    assert "Changes to be committed" in out
    assert "new file" in out

    code, out, _ = cli("commit", "-m", "add hello")
    assert code == 0
    assert "local" in out

    # Still nothing on the server until push.
    assert "hello.txt" not in svn("list", svn_repo["url"] + "/trunk")

    code, out, _ = cli("push")
    assert code == 0, out
    assert "hello.txt" in svn("list", svn_repo["url"] + "/trunk")

    code, out, _ = cli("log", "--oneline")
    assert "add hello" in out


def test_two_local_commits_become_two_revisions(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("one\n")
    cli("add", "a.txt")
    cli("commit", "-m", "first")

    (wc / "a.txt").write_text("two\n")
    cli("add", "a.txt")
    cli("commit", "-m", "second")

    code, out, err = cli("push")
    assert code == 0, err

    log = svn("log", "-q", svn_repo["url"] + "/trunk")
    assert log.count("|") >= 2
    messages = svn("log", svn_repo["url"] + "/trunk")
    assert "first" in messages and "second" in messages
    # The working copy ends at the newest content.
    assert (wc / "a.txt").read_text() == "two\n"


def test_uncommitted_work_survives_a_push(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("committed\n")
    cli("add", "a.txt")
    cli("commit", "-m", "first")

    (wc / "a.txt").write_text("still working on this\n")
    code, _, err = cli("push")
    assert code == 0, err
    assert (wc / "a.txt").read_text() == "still working on this\n"
    assert "committed" in svn("cat", svn_repo["url"] + "/trunk/a.txt")


def test_staged_content_wins_over_later_edits(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("first version\n")
    cli("add", "a.txt")
    cli("commit", "-m", "seed")
    cli("push")

    (wc / "a.txt").write_text("staged\n")
    cli("add", "a.txt")
    (wc / "a.txt").write_text("edited after staging\n")

    code, out, _ = cli("status")
    assert "MM" in out or "modified" in out

    cli("commit", "-m", "commit the staged version")
    cli("push")
    assert svn("cat", svn_repo["url"] + "/trunk/a.txt") == "staged\n"


def test_immediate_mode_commits_without_push(cli, svn_repo):
    wc = svn_repo["wc"]
    cli("config", "svngit.commitmode", "immediate")
    (wc / "a.txt").write_text("x\n")
    cli("add", "a.txt")
    code, out, err = cli("commit", "-m", "straight to svn")
    assert code == 0, err
    assert "a.txt" in svn("list", svn_repo["url"] + "/trunk")


def test_branch_create_switch_and_list(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("trunk content\n")
    cli("add", "a.txt")
    cli("commit", "-m", "seed")
    cli("push")

    code, out, err = cli("checkout", "-b", "feature-x")
    assert code == 0, err
    assert "feature-x" in out

    code, out, _ = cli("branch")
    assert "* feature-x" in out
    assert "  trunk" in out

    code, out, _ = cli("rev-parse", "--abbrev-ref", "HEAD")
    assert out.strip() == "feature-x"

    (wc / "b.txt").write_text("branch work\n")
    cli("add", "b.txt")
    cli("commit", "-m", "work on the branch")
    code, _, err = cli("push")
    assert code == 0, err
    assert "b.txt" in svn("list", svn_repo["url"] + "/branches/feature-x")
    assert "b.txt" not in svn("list", svn_repo["url"] + "/trunk")

    assert cli("checkout", "trunk")[0] == 0
    assert not (wc / "b.txt").exists()


def test_merge_a_branch_back_into_trunk(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("base\n")
    cli("add", "a.txt")
    cli("commit", "-m", "seed")
    cli("push")

    cli("checkout", "-b", "feature-y")
    (wc / "feature.txt").write_text("new feature\n")
    cli("add", "feature.txt")
    cli("commit", "-m", "add the feature")
    cli("push")

    cli("checkout", "trunk")
    code, out, err = cli("merge", "feature-y")
    assert code == 0, err + out
    assert "feature.txt" in svn("list", svn_repo["url"] + "/trunk")


def test_tag_creates_a_tags_directory(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("x\n")
    cli("add", "a.txt")
    cli("commit", "-m", "seed")
    cli("push")

    assert cli("tag", "v1.0")[0] == 0
    code, out, _ = cli("tag")
    assert "v1.0" in out
    assert "a.txt" in svn("list", svn_repo["url"] + "/tags/v1.0")

    assert cli("tag", "-d", "v1.0")[0] == 0
    code, out, _ = cli("tag")
    assert "v1.0" not in out


def test_pull_picks_up_another_checkout(cli, svn_repo, tmp_path):
    other = tmp_path / "other"
    subprocess.run(["svn", "checkout", "-q", svn_repo["url"] + "/trunk", str(other)], check=True)
    (other / "theirs.txt").write_text("from elsewhere\n")
    svn("add", "theirs.txt", cwd=other)
    svn("commit", "-m", "their work", cwd=other)

    code, out, _ = cli("fetch")
    assert code == 0
    assert "their work" in out
    assert not (svn_repo["wc"] / "theirs.txt").exists()  # fetch does not apply

    code, out, err = cli("pull")
    assert code == 0, err
    assert (svn_repo["wc"] / "theirs.txt").exists()


def test_push_is_rejected_when_the_server_moved_ahead(cli, svn_repo, tmp_path):
    wc = svn_repo["wc"]
    other = tmp_path / "other"
    subprocess.run(["svn", "checkout", "-q", svn_repo["url"] + "/trunk", str(other)], check=True)
    (other / "theirs.txt").write_text("x\n")
    svn("add", "theirs.txt", cwd=other)
    svn("commit", "-m", "their work", cwd=other)

    (wc / "mine.txt").write_text("y\n")
    cli("add", "mine.txt")
    cli("commit", "-m", "my work")

    code, _, err = cli("push")
    assert code != 0
    assert "git pull" in err

    assert cli("pull")[0] == 0
    code, _, err = cli("push")
    assert code == 0, err
    assert "mine.txt" in svn("list", svn_repo["url"] + "/trunk")


def test_rm_and_mv(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("x\n")
    cli("add", "a.txt")
    cli("commit", "-m", "seed")
    cli("push")

    assert cli("mv", "a.txt", "b.txt")[0] == 0
    cli("commit", "-m", "rename")
    cli("push")
    listing = svn("list", svn_repo["url"] + "/trunk")
    assert "b.txt" in listing and "a.txt" not in listing

    assert cli("rm", "b.txt")[0] == 0
    cli("commit", "-m", "remove")
    cli("push")
    assert "b.txt" not in svn("list", svn_repo["url"] + "/trunk")


def test_git_revert_undoes_a_pushed_revision(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("good\n")
    cli("add", "a.txt")
    cli("commit", "-m", "good change")
    cli("push")

    (wc / "a.txt").write_text("bad\n")
    cli("add", "a.txt")
    cli("commit", "-m", "bad change")
    cli("push")

    code, out, _ = cli("rev-parse", "HEAD")
    bad_revision = out.strip()

    code, out, err = cli("revert", bad_revision)
    assert code == 0, err + out
    assert svn("cat", svn_repo["url"] + "/trunk/a.txt") == "good\n"


def test_stash_round_trip(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("base\n")
    cli("add", "a.txt")
    cli("commit", "-m", "seed")
    cli("push")

    (wc / "a.txt").write_text("work in progress\n")
    code, out, err = cli("stash")
    assert code == 0, err
    assert (wc / "a.txt").read_text() == "base\n"

    code, out, _ = cli("stash", "list")
    assert "stash@{0}" in out

    code, out, err = cli("stash", "pop")
    assert code == 0, err
    assert (wc / "a.txt").read_text() == "work in progress\n"


def test_diff_distinguishes_staged_from_unstaged(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("one\n")
    cli("add", "a.txt")
    cli("commit", "-m", "seed")
    cli("push")

    (wc / "a.txt").write_text("two\n")
    cli("add", "a.txt")
    (wc / "a.txt").write_text("three\n")

    code, staged, _ = cli("diff", "--cached")
    assert "+two" in staged and "three" not in staged

    code, unstaged, _ = cli("diff")
    assert "+three" in unstaged and "-two" in unstaged


def test_reset_hard_discards_local_changes(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("base\n")
    cli("add", "a.txt")
    cli("commit", "-m", "seed")
    cli("push")

    (wc / "a.txt").write_text("scribble\n")
    assert cli("reset", "--hard")[0] == 0
    assert (wc / "a.txt").read_text() == "base\n"


def test_clean_removes_untracked_files(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "junk.txt").write_text("junk\n")

    code, out, _ = cli("clean", "-n")
    assert "junk.txt" in out
    assert (wc / "junk.txt").exists()

    assert cli("clean", "-f")[0] == 0
    assert not (wc / "junk.txt").exists()


def test_blame_reports_revisions(cli, svn_repo):
    wc = svn_repo["wc"]
    (wc / "a.txt").write_text("line one\n")
    cli("add", "a.txt")
    cli("commit", "-m", "seed")
    cli("push")

    code, out, err = cli("blame", "a.txt")
    assert code == 0, err
    assert "line one" in out
    assert out.strip().startswith("r")


def test_clone_descends_into_trunk(svn_repo, tmp_path):
    import io

    from svngit.cli import dispatch
    from svngit.context import Context

    target = tmp_path / "cloned"
    stdout, stderr = io.StringIO(), io.StringIO()
    ctx = Context(cwd=tmp_path, stdout=stdout, stderr=stderr)
    code = dispatch(ctx, "clone", [svn_repo["url"], str(target)])
    assert code == 0, stderr.getvalue()
    assert (target / ".svn").is_dir()
    info = svn("info", str(target))
    assert info.strip().endswith("trunk") or "/trunk" in info


def test_init_standalone_creates_a_usable_checkout(tmp_path, monkeypatch):
    import io

    from svngit.cli import dispatch
    from svngit.context import Context

    monkeypatch.setenv("SVNGIT_STATE_DIR", str(tmp_path / "state"))
    target = tmp_path / "fresh"
    stdout, stderr = io.StringIO(), io.StringIO()
    ctx = Context(cwd=tmp_path, stdout=stdout, stderr=stderr)
    code = dispatch(ctx, "init", ["--standalone", str(target)])
    assert code == 0, stderr.getvalue()
    assert (target / ".svn").is_dir()

    (target / "a.txt").write_text("x\n")
    out = io.StringIO()
    ctx2 = Context(cwd=target, stdout=out, stderr=io.StringIO())
    assert dispatch(ctx2, "add", ["a.txt"]) == 0
    assert dispatch(ctx2, "commit", ["-m", "first"]) == 0
    assert dispatch(ctx2, "push", []) == 0


PATCH_BASE = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n"


def _seed(cli, wc, name="a.txt", content=PATCH_BASE):
    (wc / name).write_text(content)
    cli("add", name)
    cli("commit", "-m", "seed")
    cli("push")


def _run_patch(svn_repo, answers, cwd=None):
    """Drive `git add -p` with scripted answers against the real checkout."""
    import io

    from svngit.cli import dispatch
    from svngit.context import Context

    stdout, stderr = io.StringIO(), io.StringIO()
    stdin = io.StringIO("".join(a + "\n" for a in answers))
    ctx = Context(cwd=cwd or svn_repo["wc"], stdout=stdout, stderr=stderr, stdin=stdin)
    code = dispatch(ctx, "add", ["-p"])
    return code, stdout.getvalue(), stderr.getvalue()


def test_add_patch_commits_only_the_staged_hunk(cli, svn_repo):
    wc = svn_repo["wc"]
    _seed(cli, wc)

    (wc / "a.txt").write_text(PATCH_BASE.replace("two", "TWO").replace("ten", "TEN"))
    code, out, err = _run_patch(svn_repo, ["y", "n"])
    assert code == 0, err

    code, out, _ = cli("status", "--short")
    assert "MM" in out

    cli("commit", "-m", "just the first change")
    code, _, err = cli("push")
    assert code == 0, err

    on_server = svn("cat", svn_repo["url"] + "/trunk/a.txt")
    assert "TWO" in on_server
    assert "TEN" not in on_server  # the hunk we declined must not be pushed
    # ...and the declined change is still in the working copy, unstaged.
    assert "TEN" in (wc / "a.txt").read_text()


def test_add_patch_then_stage_the_rest(cli, svn_repo):
    wc = svn_repo["wc"]
    _seed(cli, wc)
    final = PATCH_BASE.replace("two", "TWO").replace("ten", "TEN")
    (wc / "a.txt").write_text(final)

    _run_patch(svn_repo, ["y", "n"])
    cli("commit", "-m", "first half")
    _run_patch(svn_repo, ["y"])  # only the remaining hunk is offered
    cli("commit", "-m", "second half")
    code, _, err = cli("push")
    assert code == 0, err

    assert svn("cat", svn_repo["url"] + "/trunk/a.txt") == final
    code, out, _ = cli("status", "--short")
    assert out.strip() == ""  # working copy clean once both halves are pushed


def test_immediate_mode_commits_the_staged_hunk_not_the_worktree(cli, svn_repo):
    """svn commit sends the file on disk; git sends what was staged. In
    immediate mode the staged blob has to be swapped in around the commit."""
    wc = svn_repo["wc"]
    _seed(cli, wc)
    cli("config", "svngit.commitmode", "immediate")

    (wc / "a.txt").write_text(PATCH_BASE.replace("two", "TWO").replace("ten", "TEN"))
    _run_patch(svn_repo, ["y", "n"])
    code, out, err = cli("commit", "-m", "only the first hunk")
    assert code == 0, err

    on_server = svn("cat", svn_repo["url"] + "/trunk/a.txt")
    assert "TWO" in on_server and "TEN" not in on_server
    # The unstaged change must survive the commit.
    assert "TEN" in (wc / "a.txt").read_text()


def _run_edit(svn_repo, transform, cwd=None):
    """Drive `git add -e` with a scripted edit of the patch."""
    import io

    from svngit.cli import dispatch
    from svngit.context import Context

    stdout, stderr = io.StringIO(), io.StringIO()
    ctx = Context(cwd=cwd or svn_repo["wc"], stdout=stdout, stderr=stderr)
    ctx.edit_hook = transform
    code = dispatch(ctx, "add", ["-e"])
    return code, stdout.getvalue(), stderr.getvalue()


def test_add_edit_commits_only_the_kept_hunk(cli, svn_repo):
    wc = svn_repo["wc"]
    _seed(cli, wc)
    (wc / "a.txt").write_text(PATCH_BASE.replace("two", "TWO").replace("ten", "TEN"))

    drop_second = lambda text: "\n".join(
        line for line in text.splitlines() if line not in ("-ten", "+TEN")
    )
    code, out, err = _run_edit(svn_repo, drop_second)
    assert code == 0, err

    cli("commit", "-m", "only the first change")
    code, _, err = cli("push")
    assert code == 0, err

    on_server = svn("cat", svn_repo["url"] + "/trunk/a.txt")
    assert "TWO" in on_server and "TEN" not in on_server
    assert "TEN" in (wc / "a.txt").read_text()


def test_add_edit_can_stage_text_that_is_not_on_disk(cli, svn_repo):
    """The stated purpose of -e: stage something the working copy never held."""
    wc = svn_repo["wc"]
    _seed(cli, wc)
    (wc / "a.txt").write_text(PATCH_BASE.replace("two", "TWO"))

    rewrite = lambda text: text.replace("+TWO", "+REWRITTEN")
    code, out, err = _run_edit(svn_repo, rewrite)
    assert code == 0, err

    cli("commit", "-m", "staged something else entirely")
    assert cli("push")[0] == 0

    assert "REWRITTEN" in svn("cat", svn_repo["url"] + "/trunk/a.txt")
    assert "TWO" in (wc / "a.txt").read_text()  # disk is untouched


def test_add_edit_with_a_real_editor_process(cli, svn_repo, tmp_path):
    """Exercise the actual $EDITOR round trip, which the hook bypasses."""
    import io
    import os

    from svngit.cli import dispatch
    from svngit.context import Context

    wc = svn_repo["wc"]
    _seed(cli, wc)
    (wc / "a.txt").write_text(PATCH_BASE.replace("two", "TWO").replace("ten", "TEN"))

    script = tmp_path / "fake_editor.py"
    script.write_text(
        "import sys\n"
        "path = sys.argv[1]\n"
        "kept = [l for l in open(path).read().splitlines() if l not in ('-ten', '+TEN')]\n"
        "open(path, 'w').write('\\n'.join(kept) + '\\n')\n"
    )
    previous = os.environ.get("GIT_EDITOR")
    os.environ["GIT_EDITOR"] = "%s %s" % (sys.executable, script)
    try:
        stdout, stderr = io.StringIO(), io.StringIO()
        ctx = Context(cwd=wc, stdout=stdout, stderr=stderr)
        code = dispatch(ctx, "add", ["-e"])
        assert code == 0, stderr.getvalue()
    finally:
        if previous is None:
            os.environ.pop("GIT_EDITOR", None)
        else:
            os.environ["GIT_EDITOR"] = previous

    cli("commit", "-m", "via a real editor")
    assert cli("push")[0] == 0
    on_server = svn("cat", svn_repo["url"] + "/trunk/a.txt")
    assert "TWO" in on_server and "TEN" not in on_server


def test_add_patch_per_hunk_edit(cli, svn_repo):
    """`e` inside `git add -p`: edit one hunk, take another as-is."""
    import io

    from svngit.cli import dispatch
    from svngit.context import Context

    wc = svn_repo["wc"]
    _seed(cli, wc)
    (wc / "a.txt").write_text(PATCH_BASE.replace("two", "TWO").replace("ten", "TEN"))

    stdout, stderr = io.StringIO(), io.StringIO()
    ctx = Context(
        cwd=wc, stdout=stdout, stderr=stderr, stdin=io.StringIO("y\ne\n")
    )
    ctx.edit_hook = lambda text: text.replace("+TEN", "+EDITED")
    code = dispatch(ctx, "add", ["-p"])
    assert code == 0, stderr.getvalue()

    cli("commit", "-m", "one taken, one edited")
    assert cli("push")[0] == 0

    on_server = svn("cat", svn_repo["url"] + "/trunk/a.txt")
    assert "TWO" in on_server
    assert "EDITED" in on_server
    assert "TEN\n" not in on_server
    # The working copy still holds what was actually typed there.
    assert "TEN" in (wc / "a.txt").read_text()
