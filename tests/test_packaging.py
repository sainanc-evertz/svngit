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


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no executable bit")
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


def test_the_reported_version_matches_pyproject():
    """`svngit --version` prints `svngit.__version__`, which is a second copy
    of the number rather than a read of the metadata.

    The first bump proved why this is worth pinning: pyproject and all three
    recipes were updated, `test_recipes_track_the_project_version` went green
    because it only compares recipes against pyproject, and the CLI carried on
    reporting the old version -- the one number a user actually sees, and the
    one a bug report quotes.
    """
    from svngit import __version__

    assert __version__ == VERSION, (
        "svngit.__version__ is %s but pyproject says %s; `svngit --version` "
        "would report the wrong one" % (__version__, VERSION)
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


# ----------------------------------------------------------------------
# the shim actually dispatching
# ----------------------------------------------------------------------
def _shim_command():
    """How to invoke the shim on this platform."""
    directory = shim_directory()
    if sys.platform == "win32":
        return [str(directory / WINDOWS_SHIM)]
    return ["sh", str(directory / POSIX_SHIM)]


def _svngit_bin_dir():
    """Where the `svngit` entry point is, however svngit was installed.

    Prefers this project's venv, then anything on PATH, so the same tests run
    against a checkout and against a CI runner with a global install.
    """
    import shutil

    for name in ("bin", "Scripts"):
        candidate = ROOT / ".venv" / name
        if (candidate / "svngit").exists() or (candidate / "svngit.exe").exists():
            return candidate
    found = shutil.which("svngit")
    return pathlib.Path(found).parent if found else None


def _shim_env():
    """An environment with the shim first on PATH and svngit reachable."""
    import os

    bin_dir = _svngit_bin_dir()
    env = dict(os.environ)
    entries = [str(shim_directory())]
    if bin_dir is not None:
        entries.append(str(bin_dir))
    entries.append(env.get("PATH", ""))
    env["PATH"] = os.pathsep.join(entries)
    env.pop("SVNGIT_DISABLE", None)
    return env


def _run_shim(cwd, *args, env=None):
    """Run the shim and return the result.

    `--version` is the probe: svngit answers "svngit version" and the real
    git answers "git version", so the two are told apart without svn being
    installed at all -- which is what lets these run on Windows.
    """
    return subprocess.run(
        _shim_command() + list(args),
        cwd=str(cwd),
        env=env or _shim_env(),
        capture_output=True,
        text=True,
    )


needs_svngit = pytest.mark.skipif(
    _svngit_bin_dir() is None, reason="svngit is not installed"
)


@needs_svngit
def test_shim_routes_to_svngit_inside_a_working_copy(tmp_path):
    (tmp_path / ".svn").mkdir()
    result = _run_shim(tmp_path, "--version")
    assert "svngit version" in result.stdout, result.stdout + result.stderr


@needs_svngit
def test_shim_routes_to_real_git_inside_a_git_repository(tmp_path):
    (tmp_path / ".git").mkdir()
    result = _run_shim(tmp_path, "--version")
    assert result.stdout.startswith("git version"), result.stdout + result.stderr


@needs_svngit
def test_shim_routes_to_real_git_outside_any_working_copy(tmp_path):
    result = _run_shim(tmp_path, "--version")
    assert result.stdout.startswith("git version"), result.stdout + result.stderr


@needs_svngit
def test_shim_finds_the_working_copy_from_a_subdirectory(tmp_path):
    (tmp_path / ".svn").mkdir()
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    result = _run_shim(nested, "--version")
    assert "svngit version" in result.stdout, result.stdout + result.stderr


@needs_svngit
def test_shim_disable_forces_the_real_git(tmp_path):
    (tmp_path / ".svn").mkdir()
    env = _shim_env()
    env["SVNGIT_DISABLE"] = "1"
    result = _run_shim(tmp_path, "--version", env=env)
    assert result.stdout.startswith("git version"), result.stdout + result.stderr


# ----------------------------------------------------------------------
# the supported Python floor
# ----------------------------------------------------------------------
#: Standard library modules newer than svngit's floor. Importing one at module
#: scope breaks collection on an older interpreter, which is how tomllib got
#: into test_docs.py and failed only on the oldest legs of CI. Inside a
#: function guarded by a skipif it is fine, so only top-level imports count.
TOO_NEW = {
    "tomllib": (3, 11),
    "asyncio.taskgroups": (3, 11),
    "wsgiref.types": (3, 11),
}


def _floor():
    text = (ROOT / "pyproject.toml").read_text()
    match = re.search(r'requires-python\s*=\s*">=([0-9]+)\.([0-9]+)"', text)
    assert match, "could not read requires-python from pyproject.toml"
    return (int(match.group(1)), int(match.group(2)))


def _top_level_imports(tree):
    """Only imports at module scope: those run at collection time."""
    import ast as ast_module

    names = set()
    for node in tree.body:
        if isinstance(node, ast_module.Import):
            names |= {alias.name for alias in node.names}
        elif (
            isinstance(node, ast_module.ImportFrom) and node.module and node.level == 0
        ):
            names.add(node.module)
    return names


def test_no_module_imports_stdlib_newer_than_the_supported_floor():
    import ast as ast_module

    floor = _floor()
    offenders = []
    for path in sorted((ROOT / "src").rglob("*.py")) + sorted(
        (ROOT / "tests").glob("*.py")
    ):
        tree = ast_module.parse(path.read_text())
        for name in _top_level_imports(tree):
            needed = TOO_NEW.get(name)
            if needed and needed > floor:
                offenders.append(
                    "%s imports %s at module scope, which needs Python %d.%d"
                    % (path.relative_to(ROOT), name, *needed)
                )
    assert not offenders, (
        "svngit supports Python %d.%d; import these inside a guarded function "
        "instead:\n  " % floor
    ) + "\n  ".join(offenders)


def _versions(text):
    """Every `3.10`-style version in a fragment, as comparable tuples."""
    return [(int(a), int(b)) for a, b in re.findall(r"([0-9]+)\.([0-9]+)", text)]


def test_the_supported_floor_is_stated_consistently():
    """Everything that names the oldest supported Python must name the same one.

    These drifted apart once and nothing failed. mypy 2.x dropped the ability
    to target 3.9, and rather than erroring it printed a note, ignored the
    setting and carried on checking against whatever interpreter it happened to
    run under. pyproject went on claiming a floor the type checker was no
    longer enforcing, and CI stayed green the whole time.
    """
    floor = _floor()
    text = (ROOT / "pyproject.toml").read_text()

    black = re.search(r'target-version = \["([^"]+)"\]', text)
    assert black, "could not read black's target-version from pyproject.toml"
    assert (
        black.group(1) == "py%d%d" % floor
    ), "black targets %s but requires-python says %d.%d" % (black.group(1), *floor)

    mypy = re.search(r'python_version = "([^"]+)"', text)
    assert mypy, "could not read mypy's python_version from pyproject.toml"
    assert _versions(mypy.group(1)) == [
        floor
    ], "mypy checks against %s but requires-python says %d.%d" % (mypy.group(1), *floor)

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    matrix = re.search(r"^\s+python: \[([^\]]+)\]", workflow, re.M)
    assert matrix, "could not read the Python matrix from ci.yml"
    tested = _versions(matrix.group(1))
    assert (
        min(tested) == floor
    ), "CI's oldest leg is %d.%d but requires-python says %d.%d" % (
        *min(tested),
        *floor,
    )

    # Claim exactly what is tested: a classifier is a promise to the installer.
    claimed = _versions(" ".join(re.findall(r"Python :: ([0-9]+\.[0-9]+)", text)))
    assert sorted(claimed) == sorted(
        tested
    ), "pyproject's classifiers claim %s but CI tests %s" % (
        ", ".join("%d.%d" % v for v in sorted(claimed)),
        ", ".join("%d.%d" % v for v in sorted(tested)),
    )


# ----------------------------------------------------------------------
# GitHub templates
# ----------------------------------------------------------------------
#: The shapes GitHub accepts in an issue form. A malformed one is not a test
#: failure anywhere else -- GitHub simply stops offering the form, quietly.
FORM_TYPES = {"markdown", "input", "textarea", "dropdown", "checkboxes"}


def _issue_forms():
    import yaml

    directory = ROOT / ".github" / "ISSUE_TEMPLATE"
    for path in sorted(directory.glob("*.yml")):
        if path.name != "config.yml":
            yield path, yaml.safe_load(path.read_text())


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("yaml") is None, reason="needs PyYAML"
)
def test_issue_forms_match_githubs_schema():
    problems = []
    for path, form in _issue_forms():
        for key in ("name", "description", "body"):
            if key not in form:
                problems.append("%s: missing '%s'" % (path.name, key))

        seen = set()
        for index, item in enumerate(form.get("body", [])):
            where = "%s body[%d]" % (path.name, index)
            kind = item.get("type")
            if kind not in FORM_TYPES:
                problems.append("%s: unknown type %r" % (where, kind))
                continue
            attributes = item.get("attributes", {})
            if kind == "markdown":
                if not attributes.get("value"):
                    problems.append("%s: markdown needs a value" % where)
                continue
            if not attributes.get("label"):
                problems.append("%s: %s needs a label" % (where, kind))
            if kind == "dropdown" and not attributes.get("options"):
                problems.append("%s: dropdown needs options" % where)
            identifier = item.get("id")
            if identifier:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
                    problems.append("%s: invalid id %r" % (where, identifier))
                if identifier in seen:
                    problems.append("%s: duplicate id %r" % (where, identifier))
                seen.add(identifier)
    assert not problems, "\n  ".join([""] + problems)


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("yaml") is None, reason="needs PyYAML"
)
def test_issue_template_links_point_at_files_that_exist():
    import yaml

    config = yaml.safe_load(
        (ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text()
    )
    for link in config.get("contact_links", []):
        for key in ("name", "url", "about"):
            assert key in link, "a contact link is missing '%s'" % key
        # The links are blob URLs into this repository; the path after the
        # branch must be a real file, or they 404 for whoever follows them.
        match = re.search(r"/blob/[^/]+/(.+)$", link["url"])
        if match:
            assert (
                ROOT / match.group(1)
            ).exists(), "config.yml links to a missing file: %s" % match.group(1)


def test_issue_templates_do_not_quote_a_stale_count():
    """One template mentions how many commands are deliberately refused."""
    from svngit.commands import NO_EQUIVALENT

    words = {23: "Twenty-three", 24: "Twenty-four", 25: "Twenty-five"}
    text = (ROOT / ".github" / "ISSUE_TEMPLATE" / "missing-command.yml").read_text()
    expected = words.get(len(NO_EQUIVALENT))
    assert expected, "add a spelling for %d to `words`" % len(NO_EQUIVALENT)
    assert (
        expected in text
    ), "missing-command.yml should say '%s' for the %d refused commands" % (
        expected,
        len(NO_EQUIVALENT),
    )
