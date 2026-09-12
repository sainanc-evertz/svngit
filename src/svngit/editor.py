"""Handing text to the user's editor and reading it back.

Shared by `git commit` (message) and `git add -e` (patch). Tests replace the
whole step through `ctx.edit_hook` rather than spawning a real editor.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .errors import UsageError

if TYPE_CHECKING:  # pragma: no cover
    from .context import Context

#: Same precedence git uses.
EDITOR_VARIABLES = ("GIT_EDITOR", "SVN_EDITOR", "VISUAL", "EDITOR")


def find_editor() -> Optional[str]:
    for name in EDITOR_VARIABLES:
        value = os.environ.get(name)
        if value:
            return value
    return None


def edit_text(
    ctx: "Context", initial: str, suffix: str = ".txt", what: str = "text"
) -> str:
    """Open `initial` in the editor and return what came back."""
    hook = getattr(ctx, "edit_hook", None)
    if hook is not None:
        return str(hook(initial))

    editor = find_editor()
    if not editor:
        raise UsageError(
            "no editor configured, so the %s cannot be edited.\n"
            "Set $EDITOR (or $GIT_EDITOR)." % what
        )

    handle = tempfile.NamedTemporaryFile(
        "w", suffix=suffix, delete=False, encoding="utf-8"
    )
    try:
        handle.write(initial)
        handle.close()
        # Shell invocation, because $EDITOR routinely carries arguments
        # ("code --wait", "emacsclient -nw").
        subprocess.run("%s %s" % (editor, handle.name), shell=True, check=True)
        return Path(handle.name).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("#"))
