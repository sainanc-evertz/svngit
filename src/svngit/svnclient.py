"""Thin, typed wrapper around the `svn` binary.

Everything svngit knows about Subversion goes through here. Structured
subcommands (`info`, `status`, `log`, `ls`) are read via `--xml` rather than
scraped from human-readable output, which is both stable across svn versions
and locale-independent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence, TextIO

from .errors import SvnCommandError, SvnGitError

# svn status letters that mean "this path differs from BASE somehow".
DIRTY_ITEMS = frozenset(
    {"added", "conflicted", "deleted", "missing", "modified", "replaced", "obstructed"}
)


@dataclass
class SvnResult:
    argv: List[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class SvnInfo:
    path: str
    url: str
    relative_url: str
    repos_root: str
    repos_uuid: str
    revision: int
    kind: str
    wc_root: Optional[str]
    last_changed_rev: Optional[int] = None
    last_changed_author: Optional[str] = None
    last_changed_date: Optional[str] = None


@dataclass
class StatusEntry:
    path: str
    item: str
    props: str = "none"
    revision: Optional[int] = None
    copied: bool = False
    #: Only populated when status was requested with show_updates=True.
    repos_item: Optional[str] = None

    @property
    def dirty(self) -> bool:
        return self.item in DIRTY_ITEMS or self.props in ("modified", "conflicted")


@dataclass
class LogPath:
    action: str  # A, M, D, R
    path: str  # repository-absolute, e.g. /trunk/src/a.py
    kind: str = ""
    copyfrom_path: Optional[str] = None
    copyfrom_rev: Optional[int] = None


@dataclass
class LogEntry:
    revision: int
    author: str
    date: Optional[datetime]
    message: str
    paths: List[LogPath] = field(default_factory=list)


def _text(node: Optional[ET.Element], default: str = "") -> str:
    if node is None or node.text is None:
        return default
    return node.text


def parse_svn_date(raw: str) -> Optional[datetime]:
    """Parse svn's ISO-8601-with-microseconds timestamps ("...T..Z")."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    # svn emits 6-digit microseconds; some servers emit more. Trim to 6.
    if "." in cleaned:
        head, _, tail = cleaned.partition(".")
        digits = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                digits += ch
            else:
                rest = tail[i:]
                break
        cleaned = "%s.%s%s" % (head, digits[:6].ljust(6, "0"), rest)
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


class SvnClient:
    """Runs svn subcommands in a fixed working directory."""

    def __init__(
        self,
        cwd: str,
        binary: Optional[str] = None,
        dry_run: bool = False,
        trace: bool = False,
        stderr: Optional[TextIO] = None,
    ) -> None:
        self.cwd = cwd
        self.binary = binary or os.environ.get("SVNGIT_SVN", "svn")
        self.dry_run = dry_run
        self.trace = trace or os.environ.get("SVNGIT_TRACE") == "1"
        self._stderr = stderr if stderr is not None else sys.stderr
        #: Every argv this client has run. The test suite asserts against it,
        #: and --dry-run reports it.
        self.executed: List[List[str]] = []

    # ------------------------------------------------------------------
    # process plumbing
    # ------------------------------------------------------------------
    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _argv(self, args: Sequence[str], interactive: bool) -> List[str]:
        argv = [self.binary, str(args[0])]
        if not interactive:
            argv.append("--non-interactive")
        argv.extend(str(a) for a in args[1:])
        return argv

    def run(
        self,
        *args: str,
        check: bool = True,
        stdin: Optional[str] = None,
        interactive: bool = False,
        mutating: bool = False,
        capture: bool = True,
    ) -> SvnResult:
        """Run one svn subcommand.

        `mutating` marks commands that change the working copy or repository;
        those are the ones --dry-run refuses to actually execute.
        """
        argv = self._argv(args, interactive)
        self.executed.append(argv)
        if self.trace:
            print("svngit: %s" % " ".join(argv), file=self._stderr)
        if self.dry_run and mutating:
            print("would run: %s" % " ".join(argv), file=self._stderr)
            return SvnResult(argv, 0, "", "")

        result = self._execute(argv, stdin=stdin, capture=capture)
        if check and not result.ok:
            raise SvnCommandError(argv, result.returncode, result.stderr)
        return result

    def _execute(
        self, argv: List[str], stdin: Optional[str], capture: bool
    ) -> SvnResult:
        """Actually spawn svn. Overridden wholesale by the test double."""
        try:
            proc = subprocess.run(
                argv,
                cwd=self.cwd,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
                input=stdin,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            raise SvnGitError(
                "could not find the '%s' executable on PATH.\n"
                "svngit translates git commands into svn commands, so a working "
                "Subversion client is required.\n"
                "  macOS:  brew install subversion\n"
                "  Debian: apt install subversion" % self.binary
            )
        return SvnResult(argv, proc.returncode, proc.stdout or "", proc.stderr or "")

    def xml(self, *args: str, check: bool = True) -> Optional[ET.Element]:
        result = self.run(*args, "--xml", check=check)
        if not result.ok or not result.stdout.strip():
            return None
        return ET.fromstring(result.stdout)

    # ------------------------------------------------------------------
    # structured queries
    # ------------------------------------------------------------------
    def info(self, target: str = ".", revision: Optional[str] = None) -> SvnInfo:
        args = ["info", target]
        if revision:
            args.extend(["-r", revision])
        root = self.xml(*args)
        if root is None:
            raise SvnGitError("svn info returned no data for %s" % target)
        entry = root.find("entry")
        if entry is None:
            raise SvnGitError("svn info returned no entry for %s" % target)
        repository = entry.find("repository")
        wc_info = entry.find("wc-info")
        commit = entry.find("commit")

        wc_root = None
        if wc_info is not None:
            wc_root = _text(wc_info.find("wcroot-abspath")) or None

        last_rev = None
        last_author = None
        last_date = None
        if commit is not None:
            raw_rev = commit.get("revision")
            last_rev = int(raw_rev) if raw_rev else None
            last_author = _text(commit.find("author")) or None
            last_date = _text(commit.find("date")) or None

        return SvnInfo(
            path=entry.get("path", target),
            url=_text(entry.find("url")),
            relative_url=_text(entry.find("relative-url")),
            repos_root=_text(repository.find("root")) if repository is not None else "",
            repos_uuid=_text(repository.find("uuid")) if repository is not None else "",
            revision=int(entry.get("revision") or 0),
            kind=entry.get("kind", ""),
            wc_root=wc_root,
            last_changed_rev=last_rev,
            last_changed_author=last_author,
            last_changed_date=last_date,
        )

    def status(
        self,
        paths: Optional[Iterable[str]] = None,
        show_updates: bool = False,
        no_ignore: bool = False,
        depth: Optional[str] = None,
    ) -> List[StatusEntry]:
        args = ["status", "--no-ignore"] if no_ignore else ["status"]
        if show_updates:
            args.append("--show-updates")
        if depth:
            args.extend(["--depth", depth])
        args.extend(paths or ["."])
        root = self.xml(*args)
        entries: List[StatusEntry] = []
        if root is None:
            return entries
        for entry in root.iter("entry"):
            wc_status = entry.find("wc-status")
            if wc_status is None:
                continue
            revision = wc_status.get("revision")
            repos_status = entry.find("repos-status")
            entries.append(
                StatusEntry(
                    path=entry.get("path", ""),
                    item=wc_status.get("item", "none"),
                    props=wc_status.get("props", "none"),
                    revision=int(revision) if revision and revision != "-1" else None,
                    copied=wc_status.get("copied") == "true",
                    repos_item=(
                        repos_status.get("item") if repos_status is not None else None
                    ),
                )
            )
        return entries

    def log(
        self,
        target: str = ".",
        revision: Optional[str] = None,
        limit: Optional[int] = None,
        verbose: bool = False,
        stop_on_copy: bool = False,
        search: Optional[str] = None,
        extra: Optional[Sequence[str]] = None,
    ) -> List[LogEntry]:
        args: List[str] = ["log"]
        if revision:
            args.extend(["-r", revision])
        if limit:
            args.extend(["-l", str(limit)])
        if verbose:
            args.append("-v")
        if stop_on_copy:
            args.append("--stop-on-copy")
        if search:
            args.extend(["--search", search])
        if extra:
            args.extend(extra)
        args.append(target)
        root = self.xml(*args)
        entries: List[LogEntry] = []
        if root is None:
            return entries
        for node in root.iter("logentry"):
            paths = []
            paths_node = node.find("paths")
            if paths_node is not None:
                for p in paths_node.findall("path"):
                    copyfrom_rev = p.get("copyfrom-rev")
                    paths.append(
                        LogPath(
                            action=p.get("action", ""),
                            path=(p.text or "").strip(),
                            kind=p.get("kind", ""),
                            copyfrom_path=p.get("copyfrom-path"),
                            copyfrom_rev=(int(copyfrom_rev) if copyfrom_rev else None),
                        )
                    )
            entries.append(
                LogEntry(
                    revision=int(node.get("revision") or 0),
                    author=_text(node.find("author"), "(no author)"),
                    date=parse_svn_date(_text(node.find("date"))),
                    message=_text(node.find("msg")).rstrip("\n"),
                    paths=paths,
                )
            )
        return entries

    def ls(self, url: str, check: bool = True) -> List[str]:
        """List a repository directory. Returns names, directories keep their '/'."""
        root = self.xml("list", url, check=check)
        if root is None:
            return []
        names = []
        for entry in root.iter("entry"):
            name = _text(entry.find("name"))
            if not name:
                continue
            names.append(name + "/" if entry.get("kind") == "dir" else name)
        return names

    def path_exists(self, url: str, revision: Optional[str] = None) -> bool:
        args = ["info", url]
        if revision:
            args.extend(["-r", revision])
        return self.run(*args, check=False).ok

    def cat(self, target: str, revision: Optional[str] = None) -> str:
        args = ["cat", target]
        if revision:
            args.extend(["-r", revision])
        return self.run(*args).stdout

    def youngest_revision(self, url: str) -> int:
        return self.info(url, revision="HEAD").revision

    def propget(self, name: str, target: str) -> str:
        result = self.run("propget", name, target, check=False)
        return result.stdout if result.ok else ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
