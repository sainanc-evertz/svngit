# Contributing to svngit

Thanks for looking. This file covers the things that are specific to svngit —
the rules the test suite enforces, and the handful of design decisions that
explain why the code is shaped the way it is. If something here is wrong or
out of date, that is a bug worth reporting on its own.

## Contents

- [Getting set up](#getting-set-up)
- [Running the tests](#running-the-tests)
- [What the suite enforces](#what-the-suite-enforces)
- [Adding a command](#adding-a-command)
- [Adding an option](#adding-an-option)
- [Design rules worth knowing](#design-rules-worth-knowing)
- [Documentation and screenshots](#documentation-and-screenshots)
- [Sending a change](#sending-a-change)

## Getting set up

```bash
git clone https://github.com/sainanc-evertz/svngit
cd svngit
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,lint]'
```

`dev` is the test suite. `lint` is black and mypy, kept separate because black
formats differently from version to version: it is pinned tightly and run once
on one Python, rather than by every leg of the test matrix.

Two things CI checks are not Python packages:

```bash
brew install subversion shellcheck     # or apt install subversion shellcheck
```

Without Subversion the integration tests skip themselves, and a green run
then means much less than it looks. Install it before trusting one.

## Running the tests

```bash
.venv/bin/python -m pytest              # everything
.venv/bin/python -m pytest -q tests/test_colour.py
.venv/bin/black src tests docs/demo/render_svg.py
.venv/bin/mypy
```

The suite has two halves, and they are testing different things.

**Unit tests** use a recording client (`tests/conftest.py`) that replaces the
subprocess call entirely. They assert the exact `svn` argv a git command
produces, which is the actual contract of a translation layer. Reach for
these first: they are fast and they pin down *what svngit asks svn to do*.

**Integration tests** (`tests/test_integration.py`) build a real repository
over `file://` and check the resulting repository state. Use one when the
thing you care about is whether Subversion accepts the command and what it
does — not whether the right string was assembled.

Some bugs are only visible to one half. `svn cat` returning newline-translated
content could not be seen by the recording client, because the fake supplied
the bytes itself. If a change concerns how svngit and svn actually interact,
write the integration test.

## What the suite enforces

These are not style preferences; each exists because something went wrong
once. A failure here usually means the change is incomplete rather than
wrong.

| Test | What it will not let you do |
| --- | --- |
| `test_no_option_is_silently_ignored` | Declare an option the command never reads. Read it, pass it to `refuse()` or `no_effect()`, or record it in `DEFAULT_BEHAVIOUR` with a reason. |
| `test_every_command_is_in_the_readme` / `_mapping` / `_help_text` | Add a command without documenting it in all three places. |
| `test_completions_list_every_command` | Leave the shell completions behind (they are generated, so this usually passes for free). |
| `test_sample_output_is_not_hand_written` | Put a hand-typed ```` ```console ```` block in the docs. Capture it instead — see below. |
| `test_documented_extras_exist` | Point a reader at an install extra that pyproject does not define. |
| `test_no_module_imports_stdlib_newer_than_the_supported_floor` | Import something like `tomllib` at module scope, which breaks collection on Python 3.10. |
| `test_the_supported_floor_is_stated_consistently` | Raise or lower `requires-python` without moving the classifiers, black's target, mypy's `python_version` and the CI matrix with it. |
| `test_recipes_track_the_project_version` | Bump the version without updating the Homebrew, Arch and Debian recipes. |
| `test_source_is_black_formatted` / `test_package_type_checks_strictly` | Land unformatted or untyped code. Both skip when the tools are missing, so CI's **format and types** job is the authority. |

`mypy` runs in `--strict` mode with no suppressions anywhere. Keep it that
way: it has already caught two loop variables reused for different types and
a `None` reaching a function that could not take one.

## Adding a command

1. Write `cmd_<name>(ctx, argv) -> int` in the right module under
   `src/svngit/commands/`. Group by area rather than one file per command.
2. Register it in `REGISTRY` in `commands/__init__.py`. Set
   `needs_working_copy=False` only if it creates one (`clone`, `init`).
3. Document it in `README.md`, `docs/COMMANDS.md` and the `USAGE` text in
   `commands/plumbing.py`. The tests check all three.
4. Update the command count in the README — a test checks that too.

If the command has **no** Subversion equivalent, do not leave it out. Add it
to `NO_EQUIVALENT` with a sentence saying what is missing and what to use
instead. A user reaching for `git rebase` should learn why it cannot work,
not that svngit has never heard of it.

Completions are generated from the registry, so they need no separate edit.

## Adding an option

Declare it in the command's `parse()` call, then make sure it does one of four
things:

- **Works.** Read it and act on it.
- **Is refused.** `refuse(...)` with the reason and the nearest alternative.
- **Reports that it changes nothing.** `no_effect(...)`, which prints a note.
- **Already describes what svngit does.** Record it in `DEFAULT_BEHAVIOUR`
  with a sentence explaining why it is a no-op.

An option that is accepted and quietly ignored is worse than one that is
rejected: the user asked for something and believes they got it. An audit
found 24 of these once, and one of them — `merge --squash` — was committing
to the server when git would not have committed at all.

## Design rules worth knowing

**git's `HEAD` is Subversion's `BASE`.** In Subversion, `HEAD` means the
newest revision *on the server*. Resolving a git `HEAD` to svn's `HEAD` would
silently give someone else's code. The mapping lives in `revisions.py`; use
it rather than reimplementing it.

**Colour is added at the moment text is printed, never earlier.** Patch text
is parsed again (`add -e`) or written to files (`format-patch`), and escape
codes in it would be corruption rather than decoration. `--porcelain` is never
coloured either; `--short` is.

**Content that will be compared byte-for-byte must come from `cat_bytes`.**
`run()` decodes in text mode, which universal-newline-translates, so a CRLF
file's content comes back as LF and every line then looks changed.

**Merges commit directly, whatever `commitmode` says.** `svn merge` records
`svn:mergeinfo` on the working copy root, and if that is not committed with
the merge, Subversion will merge the same revisions again later.

**State lives outside the working copy**, in
`~/.local/state/svngit/<checkout>/`. Colleagues share that checkout; `svn
status` must stay clean for them and nothing of svngit's should be committable
by accident.

**Paths are printed with forward slashes on every platform**, because git
does. Use `ctx.display_path`, not `os.path.relpath`.

## Documentation and screenshots

Every screenshot in the README is real captured output. Nothing is typed by
hand, and a test rejects fenced `console` blocks to keep it that way.

```bash
sh docs/demo/setup.sh       # build a throwaway repository
sh docs/demo/capture.sh     # capture real output for the stills
sh docs/demo/make.sh        # re-record the GIF (needs vhs and ffmpeg)
python3 docs/demo/render_svg.py <capture> <out.svg> "title"
```

The renderer reproduces the ANSI colour in the capture, so a still of a diff
looks like the terminal does. Captures force colour on through config, since
a pipe correctly turns it off.

## Sending a change

Before opening a pull request:

```bash
.venv/bin/python -m pytest
.venv/bin/black src tests docs/demo/render_svg.py
.venv/bin/mypy
shellcheck --shell=sh "$(.venv/bin/svngit --shim-path)/git" docs/demo/*.sh packaging/*.sh
```

CI runs five jobs on every push: the suite on Python 3.10–3.13 on Linux and
3.13 on macOS with Subversion installed, the suite plus the `git.cmd` shim on
Windows, black and mypy, shellcheck and the completions in three shells, and a
build-and-install check of the wheel, sdist and zipapp.

A few things worth doing in the change itself:

- **Say why in the commit message**, not just what. The diff shows what.
- **Add the test that would have caught the bug**, and check it fails before
  the fix. A guard that passes against the broken version is worse than none,
  and that has happened here more than once.
- **If you cannot verify something, say so** — in the message, the docs, or
  both. The Windows shim shipped twice described as unverified, and when CI
  finally ran it, it turned out to loop forever.

Bug reports are more useful with the output of `git --trace <command>`, which
prints every `svn` command as it runs.
