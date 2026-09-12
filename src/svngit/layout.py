"""Mapping between git branch names and Subversion repository paths.

Git branches are refs; Subversion branches are directories. This module owns
that translation and nothing else. The default assumption is the conventional
trunk/branches/tags layout, but every piece of it is overridable through
config so non-standard repositories still work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .errors import SvnGitError
from .svnclient import SvnClient, SvnInfo

DEFAULT_TRUNK = "trunk"
DEFAULT_BRANCHES = "branches"
DEFAULT_TAGS = "tags"

#: Git's name for the default branch. Subversion calls it trunk; we show this
#: to the user so `git status` reads naturally.
TRUNK_BRANCH_NAME = "trunk"


@dataclass
class Layout:
    """Where trunk, branches and tags live, relative to the repository root."""

    trunk: str = DEFAULT_TRUNK
    branches: str = DEFAULT_BRANCHES
    tags: str = DEFAULT_TAGS
    #: False for repositories whose root *is* the trunk (no layout dirs at all).
    standard: bool = True

    def branch_path(self, name: str) -> str:
        """Repository-root-relative path for a git branch name."""
        if not self.standard:
            raise SvnGitError(
                "this repository has no trunk/branches/tags layout, so branches "
                "are not available.\n"
                "If it does use a layout under a different name, set it with:\n"
                "  git config svngit.branches <dir>"
            )
        if name in (TRUNK_BRANCH_NAME, self.trunk):
            return self.trunk
        return "%s/%s" % (self.branches, name)

    def tag_path(self, name: str) -> str:
        if not self.standard:
            raise SvnGitError("this repository has no tags directory")
        return "%s/%s" % (self.tags, name)

    def branch_name(self, relative_url: str) -> str:
        """Inverse of branch_path: '^/branches/foo/sub' -> 'foo'."""
        rel = relative_url.lstrip("^/").strip("/")
        if not self.standard:
            return TRUNK_BRANCH_NAME
        if rel == self.trunk or rel.startswith(self.trunk + "/"):
            return TRUNK_BRANCH_NAME
        for prefix, kind in ((self.branches, ""), (self.tags, "tags/")):
            if rel.startswith(prefix + "/"):
                remainder = rel[len(prefix) + 1 :]
                # A branch directory may contain sub-paths; the branch is the
                # first component (branches/foo/src -> foo).
                return kind + remainder.split("/", 1)[0]
        return rel or TRUNK_BRANCH_NAME


def detect(client: SvnClient, info: SvnInfo, config: Dict[str, str]) -> Layout:
    """Work out the layout, preferring explicit config over probing the server."""
    layout = Layout(
        trunk=config.get("svngit.trunk", DEFAULT_TRUNK),
        branches=config.get("svngit.branches", DEFAULT_BRANCHES),
        tags=config.get("svngit.tags", DEFAULT_TAGS),
    )
    configured = any(
        key in config for key in ("svngit.trunk", "svngit.branches", "svngit.tags")
    )
    if configured:
        layout.standard = True
        return layout

    cached = config.get("svngit.layout")
    if cached in ("standard", "flat"):
        layout.standard = cached == "standard"
        return layout

    # Probe once: does <repos-root>/trunk exist?
    if not info.repos_root:
        layout.standard = False
        return layout
    layout.standard = client.path_exists(
        "%s/%s" % (info.repos_root.rstrip("/"), layout.trunk)
    )
    return layout


def current_branch(layout: Layout, info: SvnInfo) -> str:
    rel = info.relative_url or _relative_from_urls(info.url, info.repos_root)
    return layout.branch_name(rel)


def _relative_from_urls(url: str, repos_root: str) -> str:
    if repos_root and url.startswith(repos_root):
        return "^" + url[len(repos_root) :]
    return url


def branch_url(info: SvnInfo, layout: Layout, name: str) -> str:
    return "%s/%s" % (info.repos_root.rstrip("/"), layout.branch_path(name))


def tag_url(info: SvnInfo, layout: Layout, name: str) -> str:
    return "%s/%s" % (info.repos_root.rstrip("/"), layout.tag_path(name))


def list_branches(client: SvnClient, info: SvnInfo, layout: Layout) -> List[str]:
    if not layout.standard:
        return [TRUNK_BRANCH_NAME]
    root = info.repos_root.rstrip("/")
    names = [TRUNK_BRANCH_NAME]
    for entry in client.ls("%s/%s" % (root, layout.branches), check=False):
        if entry.endswith("/"):
            names.append(entry.rstrip("/"))
    return names


def list_tags(client: SvnClient, info: SvnInfo, layout: Layout) -> List[str]:
    if not layout.standard:
        return []
    root = info.repos_root.rstrip("/")
    return [
        entry.rstrip("/")
        for entry in client.ls("%s/%s" % (root, layout.tags), check=False)
    ]


def guess_branch_for_paths(layout: Layout, paths: List[str]) -> Optional[str]:
    """Given repository-absolute paths from a log entry, guess which branch
    they belong to. Used by cherry-pick to find a merge source."""
    for path in paths:
        rel = path.lstrip("/")
        if not layout.standard:
            return ""
        if rel == layout.trunk or rel.startswith(layout.trunk + "/"):
            return layout.trunk
        if rel.startswith(layout.branches + "/"):
            remainder = rel[len(layout.branches) + 1 :]
            return "%s/%s" % (layout.branches, remainder.split("/", 1)[0])
    return None
