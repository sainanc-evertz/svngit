"""Test harness.

Two levels of fake:

* `RecordingSvn` replaces `SvnClient.run` so a test can assert the exact svn
  argv a git command produced, and hand back canned XML. This is where the
  translation rules are pinned down.
* `svn_repo` builds a real Subversion repository over file:// for the
  integration tests, which only run when svn is actually installed.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from svngit.context import Context  # noqa: E402
from svngit.svnclient import SvnClient, SvnResult  # noqa: E402

HAVE_SVN = shutil.which("svn") is not None and shutil.which("svnadmin") is not None
needs_svn = pytest.mark.skipif(not HAVE_SVN, reason="requires svn and svnadmin on PATH")


# ----------------------------------------------------------------------
# canned XML
# ----------------------------------------------------------------------
def info_xml(
    path: str = ".",
    url: str = "https://svn.example.com/repo/trunk",
    root: str = "https://svn.example.com/repo",
    revision: int = 42,
    relative_url: str = "^/trunk",
    wc_root: str = "/wc",
    last_rev: int = 40,
    author: str = "alice",
) -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<info>
<entry kind="dir" path="{path}" revision="{revision}">
<url>{url}</url>
<relative-url>{relative_url}</relative-url>
<repository><root>{root}</root><uuid>2b1f4c50-6a9d-11ee-b2c2-0242ac120002</uuid></repository>
<wc-info><wcroot-abspath>{wc_root}</wcroot-abspath><schedule>normal</schedule><depth>infinity</depth></wc-info>
<commit revision="{last_rev}"><author>{author}</author><date>2026-03-01T10:00:00.000000Z</date></commit>
</entry>
</info>""".format(
        path=path,
        url=url,
        root=root,
        revision=revision,
        relative_url=relative_url,
        wc_root=wc_root,
        last_rev=last_rev,
        author=author,
    )


def status_xml(entries: Sequence[tuple]) -> str:
    """entries: (path, item) or (path, item, props)."""
    rows = []
    for entry in entries:
        path, item = entry[0], entry[1]
        props = entry[2] if len(entry) > 2 else "none"
        rows.append(
            '<entry path="{path}"><wc-status props="{props}" item="{item}" revision="42">'
            "</wc-status></entry>".format(path=path, item=item, props=props)
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<status>\n<target path=".">\n'
        + "\n".join(rows)
        + "\n</target>\n</status>"
    )


def log_xml(entries: Sequence[dict]) -> str:
    rows = []
    for entry in entries:
        paths = ""
        if entry.get("paths"):
            items = "".join(
                '<path action="{action}" kind="file">{path}</path>'.format(**p)
                for p in entry["paths"]
            )
            paths = "<paths>%s</paths>" % items
        rows.append(
            '<logentry revision="{revision}"><author>{author}</author>'
            "<date>{date}</date>{paths}<msg>{message}</msg></logentry>".format(
                revision=entry["revision"],
                author=entry.get("author", "alice"),
                date=entry.get("date", "2026-03-01T10:00:00.000000Z"),
                paths=paths,
                message=entry.get("message", "a change"),
            )
        )
    return '<?xml version="1.0" encoding="UTF-8"?>\n<log>\n%s\n</log>' % "\n".join(rows)


# ----------------------------------------------------------------------
# recording svn client
# ----------------------------------------------------------------------
class RecordingSvn(SvnClient):
    """An SvnClient that never spawns a process.

    Responses are registered as (matcher, stdout, returncode) and consulted in
    registration order; the first match wins.
    """

    def __init__(self, cwd: str, **kwargs):
        super().__init__(cwd=cwd, binary="svn", **kwargs)
        self.calls: List[List[str]] = []
        self._rules: List[tuple] = []
        self.default_result = SvnResult([], 0, "", "")

    def respond(
        self,
        matcher,
        stdout: str = "",
        returncode: int = 0,
        stderr: str = "",
        once: bool = False,
        first: bool = False,
    ) -> "RecordingSvn":
        """Register a response. `matcher` is a subcommand name, a substring
        matched against the whole argv, or a callable taking the argv.

        Rules are consulted in order; `first` puts this one ahead of the
        defaults the harness installs.
        """
        rule = [matcher, stdout, returncode, stderr, once, False]
        self._rules.insert(0, rule) if first else self._rules.append(rule)
        return self

    def _match(self, argv: List[str]):
        joined = " ".join(argv)
        for rule in self._rules:
            matcher, stdout, returncode, stderr, once, used = rule
            if once and used:
                continue
            if callable(matcher):
                hit = matcher(argv)
            elif " " in matcher or "-" in matcher.lstrip("-"):
                hit = matcher in joined
            else:
                hit = len(argv) > 1 and argv[1] == matcher
            if hit:
                rule[5] = True
                return stdout, returncode, stderr
        return None

    def _execute(self, argv, stdin=None, capture=True) -> SvnResult:
        self.calls.append(argv)
        matched = self._match(argv)
        stdout, returncode, stderr = matched if matched else ("", 0, "")
        return SvnResult(argv, returncode, stdout, stderr)

    # convenience for assertions -------------------------------------
    def argv_for(self, subcommand: str) -> Optional[List[str]]:
        for argv in self.calls:
            if len(argv) > 1 and argv[1] == subcommand:
                return argv
        return None

    def all_for(self, subcommand: str) -> List[List[str]]:
        return [argv for argv in self.calls if len(argv) > 1 and argv[1] == subcommand]

    @property
    def subcommands(self) -> List[str]:
        return [argv[1] for argv in self.calls if len(argv) > 1]


class Harness:
    """A fake working copy plus a Context wired to a RecordingSvn."""

    def __init__(self, wc: Path, state_dir: Path):
        self.wc = wc
        self.state_dir = state_dir
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.stdin = io.StringIO()
        self.ctx = Context(
            cwd=wc, stdout=self.stdout, stderr=self.stderr, stdin=self.stdin
        )
        self.svn = RecordingSvn(cwd=str(wc), stderr=self.stderr)
        self.ctx.svn = self.svn
        from svngit.state import State

        self.ctx._state = State(wc, state_dir)
        self.svn.respond("info", info_xml(wc_root=str(wc), path=str(wc)))
        self.svn.respond("status", status_xml([]))
        self.svn.respond("log", log_xml([]))

    def set_status(self, entries: Sequence[tuple]) -> None:
        """Canned `svn status`, with paths made absolute the way svn reports
        them for the absolute targets svngit passes."""
        rows = [(str(self.wc / e[0]), *e[1:]) for e in entries]
        self.svn.respond("status", status_xml(rows), first=True)

    def answer(self, *responses: str) -> None:
        """Queue answers for an interactive prompt, e.g. `git add -p`."""
        self.stdin.write("".join(r + "\n" for r in responses))
        self.stdin.seek(0)

    def set_log(self, entries: Sequence[dict]) -> None:
        self.svn.respond("log", log_xml(entries), first=True)

    def write(self, relative: str, content: str = "hello\n") -> Path:
        path = self.wc / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def run(self, *argv: str) -> int:
        from svngit.cli import dispatch

        return dispatch(self.ctx, argv[0], list(argv[1:]))

    @property
    def out(self) -> str:
        return self.stdout.getvalue()

    @property
    def err(self) -> str:
        return self.stderr.getvalue()

    def reset_output(self) -> None:
        self.stdout.truncate(0)
        self.stdout.seek(0)
        self.stderr.truncate(0)
        self.stderr.seek(0)


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    wc = tmp_path / "wc"
    (wc / ".svn").mkdir(parents=True)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SVNGIT_STATE_DIR", str(state_dir))
    monkeypatch.delenv("SVNGIT_TRACE", raising=False)
    return Harness(wc, state_dir)


# ----------------------------------------------------------------------
# real subversion, for the integration tests
# ----------------------------------------------------------------------
@pytest.fixture
def svn_repo(tmp_path, monkeypatch):
    """A real repository plus a checkout of trunk, over file://."""
    repo = tmp_path / "repo"
    subprocess.run(["svnadmin", "create", str(repo)], check=True)
    url = repo.as_uri()
    subprocess.run(
        ["svn", "mkdir", "-q", "-m", "layout",
         url + "/trunk", url + "/branches", url + "/tags"],
        check=True,
    )
    wc = tmp_path / "wc"
    subprocess.run(["svn", "checkout", "-q", url + "/trunk", str(wc)], check=True)
    monkeypatch.setenv("SVNGIT_STATE_DIR", str(tmp_path / "state"))
    return {"repo": repo, "url": url, "wc": wc}


@pytest.fixture
def cli(svn_repo):
    """Run svngit against the real checkout, returning (code, stdout, stderr)."""

    def _run(*argv: str, cwd: Optional[Path] = None):
        from svngit.cli import dispatch

        stdout, stderr = io.StringIO(), io.StringIO()
        ctx = Context(cwd=cwd or svn_repo["wc"], stdout=stdout, stderr=stderr)
        code = dispatch(ctx, argv[0], list(argv[1:]))
        return code, stdout.getvalue(), stderr.getvalue()

    return _run
