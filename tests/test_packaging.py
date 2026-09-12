"""The shim, the completions, and the packaging recipes.

Packaging is the part nobody re-runs by hand, so the things that quietly rot
are checked here: a completion list that forgets a new command, a recipe
pinned to a version that has moved on, or a shim that is no longer where the
package says it is.
"""

from __future__ import annotations

import io
import pathlib
import re
import subprocess
import sys

import pytest

from svngit import completion
from svngit.cli import main
from svngit.commands import ALIASES, NO_EQUIVALENT, REGISTRY
from svngit.shim import POSIX_SHIM, WINDOWS_SHIM, shim_directory

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION = re.search(
    r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M
).group(1)


def run_cli(*argv):
    """Run the CLI, capturing what it prints to stdout."""
    buffer = io.StringIO()
    original = sys.stdout
    sys.stdout = buffer
    try:
        code = main(list(argv))
    finally:
        sys.stdout = original
    return code, buffer.getvalue()


# ----------------------------------------------------------------------
# the git shim
# ----------------------------------------------------------------------
def test_shim_directory_holds_both_shims():
    directory = shim_directory()
    assert (directory / POSIX_SHIM).is_file(), "the POSIX shim is missing"
    assert (directory / WINDOWS_SHIM).is_file(), "the Windows shim is missing"


def test_posix_shim_is_executable():
    """A shim on PATH that cannot be executed fails in a baffling way, and
    wheels do not carry the executable bit."""
    assert (shim_directory() / POSIX_SHIM).stat().st_mode & 0o111


def test_posix_shim_is_a_shell_script():
    text = (shim_directory() / POSIX_SHIM).read_text()
    assert text.startswith("#!/bin/sh"), "the shim needs a POSIX sh shebang"


def test_shim_path_is_reported_by_the_cli():
    code, out = run_cli("--shim-path")
    assert code == 0
    assert pathlib.Path(out.strip()).is_dir()


def test_shim_dispatches_on_both_markers():
    """Both shims must check .git and .svn, or one platform silently routes
    every command to the wrong tool."""
    for name in (POSIX_SHIM, WINDOWS_SHIM):
        text = (shim_directory() / name).read_text()
        assert ".git" in text and ".svn" in text, "%s lost a dispatch rule" % name
        assert "SVNGIT_DISABLE" in text, "%s lost the escape hatch" % name


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_posix_shim_is_valid_shell():
    result = subprocess.run(
        ["sh", "-n", str(shim_directory() / POSIX_SHIM)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ----------------------------------------------------------------------
# completions
# ----------------------------------------------------------------------
@pytest.mark.parametrize("shell", completion.SHELLS)
def test_completions_list_every_command(shell):
    """Generated from the registry, so adding a command cannot leave the
    completions behind."""
    script = completion.generate(shell)
    missing = [name for name in REGISTRY if name not in script]
    assert not missing, "%s completions are missing: %s" % (shell, missing)


@pytest.mark.parametrize("shell", completion.SHELLS)
def test_completions_include_aliases_and_refusals(shell):
    script = completion.generate(shell)
    for name in list(ALIASES) + list(NO_EQUIVALENT):
        assert name in script, "%s completions are missing %s" % (shell, name)


@pytest.mark.parametrize("shell", completion.SHELLS)
def test_completions_are_not_empty(shell):
    assert len(completion.generate(shell).splitlines()) > 10


def test_unknown_shell_is_rejected():
    with pytest.raises(ValueError):
        completion.generate("csh")


def test_completion_is_reported_by_the_cli():
    for shell in completion.SHELLS:
        code, out = run_cli("--completion", shell)
        assert code == 0
        assert "svngit" in out


def test_completion_cli_rejects_an_unknown_shell():
    code, _ = run_cli("--completion", "csh")
    assert code != 0


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.parametrize("shell,checker", [("bash", "bash"), ("zsh", "zsh")])
def test_generated_completions_parse(shell, checker, tmp_path):
    """Syntax-check with the real shell where we have one. fish is not
    checked because it is not installed everywhere; its generator is a plain
    line-per-command emitter with no control flow to get wrong."""
    if not _which(checker):
        pytest.skip("%s is not installed" % checker)
    script = tmp_path / ("completion.%s" % shell)
    script.write_text(completion.generate(shell))
    result = subprocess.run(
        [checker, "-n", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def _which(name):
    import shutil

    return shutil.which(name)


# ----------------------------------------------------------------------
# packaging recipes
# ----------------------------------------------------------------------
RECIPES = {
    "packaging/homebrew/svngit.rb": r'v([0-9][^"/]*)\.tar\.gz',
    "packaging/arch/PKGBUILD": r"^pkgver=(.+)$",
    "packaging/debian/debian/changelog": r"^svngit \(([^-)]+)",
}


@pytest.mark.parametrize("path,pattern", sorted(RECIPES.items()))
def test_recipes_track_the_project_version(path, pattern):
    """A recipe pinned to an old version installs the wrong thing, and
    nothing else would notice."""
    text = (ROOT / path).read_text()
    match = re.search(pattern, text, re.M)
    assert match, "could not find a version in %s" % path
    assert match.group(1) == VERSION, "%s is pinned to %s but the project is at %s" % (
        path,
        match.group(1),
        VERSION,
    )


def test_every_recipe_depends_on_subversion():
    """svngit can do nothing without an svn client, so a package that does
    not pull one in is broken on a clean machine."""
    for path in RECIPES:
        text = (ROOT / path).read_text().lower()
        if path.endswith("changelog"):
            continue
        assert "subversion" in text, "%s does not depend on subversion" % path


def test_pyproject_ships_the_shim_as_package_data():
    text = (ROOT / "pyproject.toml").read_text()
    assert '"svngit.shim"' in text, "the shim would be left out of the wheel"
    for name in (POSIX_SHIM, WINDOWS_SHIM):
        assert name in text, "%s is not declared as package data" % name


def test_no_recipe_puts_the_shim_on_the_system_path():
    """Installing the shim as /usr/bin/git would shadow the real git for
    every program on the machine. Every recipe must leave it opt-in."""
    for path in ("packaging/arch/PKGBUILD", "packaging/homebrew/svngit.rb"):
        text = (ROOT / path).read_text()
        assert "shim-path" in text or "shim" in text, (
            "%s should explain how the shim is reached" % path
        )
        assert "bin/git" not in text.replace("svngit", ""), (
            "%s looks like it installs a `git` binary" % path
        )


# ----------------------------------------------------------------------
# formatting and typing
# ----------------------------------------------------------------------
def _tool(name):
    """The dev tool from this project's venv, if it is installed."""
    import shutil

    candidate = ROOT / ".venv" / "bin" / name
    return str(candidate) if candidate.exists() else shutil.which(name)


@pytest.mark.skipif(_tool("black") is None, reason="black is not installed")
def test_source_is_black_formatted():
    result = subprocess.run(
        [
            _tool("black"),
            "--check",
            "--quiet",
            "src",
            "tests",
            "docs/demo/render_svg.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "run `black src tests docs/demo/render_svg.py`\n"
        + result.stdout
        + result.stderr
    )


@pytest.mark.skipif(_tool("mypy") is None, reason="mypy is not installed")
def test_package_type_checks_strictly():
    """The package is checked under `mypy --strict`.

    Kept as a test because the value is in it staying clean: strict mode is
    easy to satisfy once and easy to erode one untyped helper at a time.
    """
    result = subprocess.run([_tool("mypy")], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
