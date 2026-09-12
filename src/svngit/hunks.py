"""Splitting a file's changes into hunks, and rebuilding content from a chosen
subset of them.

This is the machinery behind `git add -p`. It works on difflib's opcode list
rather than on patch text: a selection is a set of opcode indices, and applying
it walks the opcodes taking either the old or the new lines for each. That
makes application exact by construction -- there is no patch to re-parse and no
offset arithmetic that can drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Set

DEFAULT_CONTEXT = 3


@dataclass
class Hunk:
    """One reviewable group of changes, plus the context lines shown with it."""

    #: Indices of the *changed* opcodes this hunk stages. The selection unit.
    changed_ops: List[int]
    base_start: int
    base_end: int
    work_start: int
    work_end: int

    @property
    def splittable(self) -> bool:
        return len(self.changed_ops) > 1

    @property
    def header(self) -> str:
        return "@@ -%s +%s @@" % (
            _range(self.base_start, self.base_end),
            _range(self.work_start, self.work_end),
        )


def _range(start: int, end: int) -> str:
    count = end - start
    # A zero-length range points at the line *before* the gap, which is what
    # unified diff means by a 0 count.
    return "%d,%d" % (start + 1 if count else start, count)


@dataclass
class FileDiff:
    """The change between two versions of one file, grouped into hunks."""

    base: List[str]
    work: List[str]
    ops: List[tuple]
    hunks: List[Hunk] = field(default_factory=list)
    context: int = DEFAULT_CONTEXT

    @property
    def empty(self) -> bool:
        return not self.hunks

    def render(self, hunk: Hunk) -> List[str]:
        """The hunk as unified-diff lines, header first."""
        first = self.ops[hunk.changed_ops[0]]
        last = self.ops[hunk.changed_ops[-1]]
        lines = [hunk.header]
        lines.extend(" " + text for text in self.base[hunk.base_start : first[1]])
        for index in range(hunk.changed_ops[0], hunk.changed_ops[-1] + 1):
            tag, i1, i2, j1, j2 = self.ops[index]
            if tag == "equal":
                lines.extend(" " + text for text in self.base[i1:i2])
            else:
                lines.extend("-" + text for text in self.base[i1:i2])
                lines.extend("+" + text for text in self.work[j1:j2])
        lines.extend(" " + text for text in self.base[last[2] : hunk.base_end])
        return [line.rstrip("\n") for line in lines]

    def apply(self, selected_ops: Set[int]) -> str:
        """Rebuild the file taking the new lines only where selected."""
        out: List[str] = []
        for index, (tag, i1, i2, j1, j2) in enumerate(self.ops):
            if tag == "equal" or index not in selected_ops:
                out.extend(self.base[i1:i2])
            else:
                out.extend(self.work[j1:j2])
        return "".join(out)

    def split(self, hunk: Hunk) -> List[Hunk]:
        """Break a hunk into one hunk per changed region.

        A hunk with a single changed opcode has no internal context lines to
        split on, which is exactly when git reports it cannot be split.
        """
        if not hunk.splittable:
            return [hunk]
        return [self._build([index]) for index in hunk.changed_ops]

    def _build(self, changed: List[int]) -> Hunk:
        first, last = self.ops[changed[0]], self.ops[changed[-1]]
        base_start, work_start = first[1], first[3]
        if changed[0] > 0 and self.ops[changed[0] - 1][0] == "equal":
            previous = self.ops[changed[0] - 1]
            take = min(self.context, previous[2] - previous[1])
            base_start, work_start = previous[2] - take, previous[4] - take

        base_end, work_end = last[2], last[4]
        if changed[-1] + 1 < len(self.ops) and self.ops[changed[-1] + 1][0] == "equal":
            following = self.ops[changed[-1] + 1]
            take = min(self.context, following[2] - following[1])
            base_end, work_end = following[1] + take, following[3] + take

        return Hunk(list(changed), base_start, base_end, work_start, work_end)


def diff_file(base: str, work: str, context: int = DEFAULT_CONTEXT) -> FileDiff:
    """Split the change from `base` to `work` into reviewable hunks.

    Changed regions closer together than twice the context are grouped, so the
    displayed hunks never overlap -- the same rule unified diff uses.
    """
    base_lines = base.splitlines(keepends=True)
    work_lines = work.splitlines(keepends=True)
    ops = SequenceMatcher(None, base_lines, work_lines, autojunk=False).get_opcodes()

    diff = FileDiff(base_lines, work_lines, ops, context=context)
    changed = [index for index, op in enumerate(ops) if op[0] != "equal"]
    if not changed:
        return diff

    groups: List[List[int]] = [[changed[0]]]
    for index in changed[1:]:
        between = sum(
            op[2] - op[1] for op in ops[groups[-1][-1] + 1 : index] if op[0] == "equal"
        )
        if between <= 2 * context:
            groups[-1].append(index)
        else:
            groups.append([index])

    diff.hunks = [diff._build(group) for group in groups]
    return diff


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8000]


def summarise(diff: FileDiff, selected_ops: Set[int]) -> tuple:
    """(insertions, deletions) contributed by the selected opcodes."""
    insertions = deletions = 0
    for index, (tag, i1, i2, j1, j2) in enumerate(diff.ops):
        if tag == "equal" or index not in selected_ops:
            continue
        deletions += i2 - i1
        insertions += j2 - j1
    return insertions, deletions
