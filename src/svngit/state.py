"""Local state that Subversion has nowhere to put: the staging area, the
queue of not-yet-pushed commits, the stash, and svngit's own config.

The state lives *outside* the working copy (under XDG state, keyed by working
copy path) so that `svn status` stays clean and nobody can accidentally commit
svngit's bookkeeping to the shared repository.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

STATE_VERSION = 1

#: Staged actions, using git's letters.
ADD = "A"
MODIFY = "M"
DELETE = "D"


def state_dir_for(wc_root: Path) -> Path:
    """Where the state for a given working copy lives.

    Keyed by absolute path plus a short hash, so two checkouts of the same
    repository in different directories keep independent state.
    """
    override = os.environ.get("SVNGIT_STATE_DIR")
    if override:
        return Path(override).expanduser()
    base = Path(
        os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    ) / "svngit"
    resolved = str(wc_root.resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", wc_root.name) or "wc"
    return base / ("%s-%s" % (slug, digest))


@dataclass
class IndexEntry:
    """One staged path.

    `blob` is the content hash *at the time of staging*, which is what lets us
    tell "staged and unchanged since" from "staged then edited again" -- the
    distinction git shows in the two status columns.
    """

    action: str
    blob: Optional[str] = None
    executable: bool = False
    #: Set by `git add -N`: the path is recorded with empty content so that
    #: `git add -p` can pick hunks out of it, but it is not ready to commit.
    intent: bool = False


@dataclass
class Change:
    path: str
    action: str
    blob: Optional[str] = None
    executable: bool = False


@dataclass
class LocalCommit:
    id: str
    message: str
    author: str
    timestamp: float
    base_revision: int
    changes: List[Change] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return self.id[:7]

    @property
    def summary(self) -> str:
        return self.message.strip().splitlines()[0] if self.message.strip() else ""


@dataclass
class StashEntry:
    id: str
    message: str
    timestamp: float
    base_revision: int
    patch_blob: Optional[str] = None
    #: Untracked files captured with `git stash -u`: [{"path": ..., "blob": ...}]
    untracked: List[Dict[str, str]] = field(default_factory=list)
    #: Index contents at stash time, so `stash pop` can restore staging.
    index: Dict[str, Dict] = field(default_factory=dict)
    #: Set only by `git stash -p`, as [{"path", "full", "kept"}] -- the content
    #: before stashing and the content left behind. Its presence is what marks
    #: an entry as partial, which `pop` restores differently.
    partial: List[Dict[str, str]] = field(default_factory=list)


class ObjectStore:
    """Content-addressed blob storage for staged content, queued commits and
    stashes. Same idea as git's loose objects, minus the zlib."""

    def __init__(self, root: Path):
        self.root = root

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest[2:]

    def write(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = self._path(digest)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name("%s.%d.tmp" % (target.name, os.getpid()))
            tmp.write_bytes(data)
            tmp.replace(target)
        return digest

    def write_file(self, path: Path) -> str:
        return self.write(path.read_bytes())

    def read(self, digest: str) -> bytes:
        target = self._path(digest)
        if not target.exists():
            raise KeyError("missing object %s" % digest)
        return target.read_bytes()

    def has(self, digest: str) -> bool:
        return self._path(digest).exists()

    def read_text(self, digest: str) -> str:
        return self.read(digest).decode("utf-8", errors="replace")


class State:
    """Reads and writes the on-disk state for one working copy."""

    def __init__(self, wc_root: Path, directory: Optional[Path] = None):
        self.wc_root = Path(wc_root)
        self.dir = Path(directory) if directory else state_dir_for(self.wc_root)
        self.objects = ObjectStore(self.dir / "objects")
        self._data = self._load()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    @property
    def _file(self) -> Path:
        return self.dir / "state.json"

    def _load(self) -> dict:
        if not self._file.exists():
            return {
                "version": STATE_VERSION,
                "index": {},
                "commits": [],
                "stash": [],
                "config": {},
                "wc_root": str(self.wc_root),
            }
        with self._file.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        data.setdefault("index", {})
        data.setdefault("commits", [])
        data.setdefault("stash", [])
        data.setdefault("config", {})
        return data

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._data["wc_root"] = str(self.wc_root)
        tmp = self._file.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp.replace(self._file)

    # ------------------------------------------------------------------
    # index
    # ------------------------------------------------------------------
    @property
    def index(self) -> Dict[str, IndexEntry]:
        return {
            path: IndexEntry(**entry) for path, entry in self._data["index"].items()
        }

    def stage(self, path: str, action: str, blob: Optional[str] = None,
              executable: bool = False, intent: bool = False) -> None:
        self._data["index"][path] = asdict(
            IndexEntry(action=action, blob=blob, executable=executable, intent=intent)
        )

    def unstage(self, path: str) -> bool:
        return self._data["index"].pop(path, None) is not None

    def clear_index(self) -> None:
        self._data["index"] = {}

    def is_staged(self, path: str) -> bool:
        return path in self._data["index"]

    # ------------------------------------------------------------------
    # local commit queue
    # ------------------------------------------------------------------
    @property
    def commits(self) -> List[LocalCommit]:
        return [
            LocalCommit(
                id=item["id"],
                message=item["message"],
                author=item["author"],
                timestamp=item["timestamp"],
                base_revision=item["base_revision"],
                changes=[Change(**c) for c in item.get("changes", [])],
            )
            for item in self._data["commits"]
        ]

    def queued_blobs(self) -> Dict[str, Optional[str]]:
        """Path -> blob recorded by the newest queued commit touching it, or
        None where that commit deleted the path.

        Queued commits are not on the server, so BASE alone is not what the
        working copy is "based on" locally: a file whose content matches its
        newest queued commit has no uncommitted change, even though Subversion
        still reports it as modified. Later commits overwrite earlier ones
        because the queue is walked oldest to newest.
        """
        result: Dict[str, Optional[str]] = {}
        for commit in self.commits:
            for change in commit.changes:
                result[change.path] = None if change.action == DELETE else change.blob
        return result

    def add_commit(self, commit: LocalCommit) -> None:
        self._data["commits"].append(asdict(commit))

    def replace_commits(self, commits: List[LocalCommit]) -> None:
        self._data["commits"] = [asdict(c) for c in commits]

    def pop_commit(self) -> Optional[LocalCommit]:
        if not self._data["commits"]:
            return None
        item = self._data["commits"].pop()
        return LocalCommit(
            id=item["id"],
            message=item["message"],
            author=item["author"],
            timestamp=item["timestamp"],
            base_revision=item["base_revision"],
            changes=[Change(**c) for c in item.get("changes", [])],
        )

    # ------------------------------------------------------------------
    # stash
    # ------------------------------------------------------------------
    @property
    def stash(self) -> List[StashEntry]:
        return [StashEntry(**item) for item in self._data["stash"]]

    def push_stash(self, entry: StashEntry) -> None:
        # Newest first, matching `git stash list` ordering (stash@{0} on top).
        self._data["stash"].insert(0, asdict(entry))

    def set_stash(self, entries: List[StashEntry]) -> None:
        self._data["stash"] = [asdict(e) for e in entries]

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------
    @property
    def config(self) -> Dict[str, str]:
        return dict(self._data["config"])

    def set_config(self, key: str, value: str) -> None:
        self._data["config"][key] = value

    def unset_config(self, key: str) -> bool:
        return self._data["config"].pop(key, None) is not None

    def get_config(self, key: str, default=None):
        return self._data["config"].get(key, default)


def make_commit_id(message: str, changes: List[Change], timestamp: float) -> str:
    """A stable, git-looking identifier for a queued commit.

    It is not a git object hash -- nothing here is a git object -- but it is
    deterministic in the commit's content, so it behaves like one.
    """
    hasher = hashlib.sha1()
    hasher.update(message.encode("utf-8"))
    hasher.update(repr(timestamp).encode("utf-8"))
    for change in sorted(changes, key=lambda c: c.path):
        hasher.update(("%s%s%s" % (change.action, change.path, change.blob or "")).encode("utf-8"))
    return hasher.hexdigest()


def now() -> float:
    return time.time()
