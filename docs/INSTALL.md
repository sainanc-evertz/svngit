# Installing svngit

svngit is pure Python with no dependencies outside the standard library. The
only thing it needs alongside Python 3.9+ is the `svn` command-line client,
because that is what it drives.

## Contents

[Pick a method](#pick-a-method) · [pipx](#pipx) · [pip](#pip) ·
[Homebrew](#homebrew) · [Single file, no install](#single-file-no-install) ·
[Arch](#arch-linux) · [Debian and Ubuntu](#debian-and-ubuntu) ·
[From source](#from-source) · [The git shim](#the-git-shim) ·
[Shell completions](#shell-completions) · [Uninstalling](#uninstalling)

## Pick a method

| You want | Use |
| --- | --- |
| The normal way to install a Python CLI | [pipx](#pipx) |
| It inside an existing virtualenv | [pip](#pip) |
| macOS or Linuxbrew | [Homebrew](#homebrew) |
| No install at all, one file you can copy around | [zipapp](#single-file-no-install) |
| A native package for your distribution | [Arch](#arch-linux), [Debian](#debian-and-ubuntu) |
| To hack on it | [From source](#from-source) |

Whichever you choose, finish with [the git shim](#the-git-shim) and
[shell completions](#shell-completions). Neither is installed automatically,
on purpose — see those sections for why.

## pipx

`pipx` keeps the tool in its own environment and puts the command on your
PATH, which is what you usually want for a CLI.

```bash
pipx install svngit
```

## pip

```bash
pip install svngit
```

Inside a virtualenv this is fine. Installing into the system Python is not
recommended, and newer distributions will refuse it outright.

## Homebrew

```bash
brew tap sainanc-evertz/svngit https://github.com/sainanc-evertz/svngit
brew install svngit
```

This pulls in `subversion` if you do not already have it, and installs the
bash, zsh and fish completions where Homebrew expects them — so on Homebrew
you can skip the completions section below.

## Single file, no install

Because svngit needs nothing but the standard library, it can be built as a
single executable archive that runs on any Python 3.9+:

```bash
sh packaging/make-zipapp.sh
./dist/svngit.pyz --version
```

Copy `svngit.pyz` anywhere — another machine, a shared drive, a locked-down
box with no `pip`. To use the shim with it, point `SVNGIT_BIN` at the archive
so the shim knows what to call:

```bash
export SVNGIT_BIN=/path/to/svngit.pyz
export PATH="$($SVNGIT_BIN --shim-path):$PATH"
```

The shim lives inside the archive, so `--shim-path` unpacks it into
`~/.cache/svngit/shim` and prints that directory.

## Arch Linux

```bash
cd packaging/arch
makepkg -si
```

## Debian and Ubuntu

```bash
cp -r packaging/debian/debian .
dpkg-buildpackage -us -uc -b
sudo dpkg -i ../svngit_*.deb
```

> **Not yet verified.** The Arch and Debian recipes are written and reviewed
> but have not been built, because there is no Arch or Debian machine in this
> project's test setup. The Homebrew formula, the wheel, the sdist and the
> zipapp are all built and installed by the test suite. If you build a native
> package, a report either way would be welcome.

## From source

```bash
git clone https://github.com/sainanc-evertz/svngit
cd svngit
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

## The git shim

The shim is what makes plain `git` work inside a Subversion checkout. It is
**not** put on your PATH by any install method, because a file called `git`
early on the PATH affects every program on the machine. You opt in:

```bash
export PATH="$(svngit --shim-path):$PATH"
```

That one line is the same however you installed svngit. Outside a Subversion
working copy the shim hands every command straight to the real git, so it is
safe to leave in your shell profile.

Add it permanently:

| Shell | File | Line |
| --- | --- | --- |
| bash | `~/.bashrc` | `export PATH="$(svngit --shim-path):$PATH"` |
| zsh | `~/.zshrc` | `export PATH="$(svngit --shim-path):$PATH"` |
| fish | `~/.config/fish/config.fish` | `fish_add_path (svngit --shim-path)` |
| PowerShell | `$PROFILE` | `$env:PATH = "$(svngit --shim-path);$env:PATH"` |
| cmd.exe | — | Add the output of `svngit --shim-path` to PATH in System Properties |

To bypass it for one command:

```bash
SVNGIT_DISABLE=1 git status    # the real git, even in an svn working copy
```

### How it decides

1. `SVNGIT_DISABLE=1` — always the real git.
2. Walk up from the current directory. Whichever it meets first wins: `.git`
   sends the command to the real git, `.svn` sends it to svngit.
3. Outside any working copy, `clone` of an `svn://` or `svn+ssh://` URL goes
   to svngit. An `https://` URL could belong to either system, so for those
   run `svngit clone <url>` explicitly the first time.
4. Anything else goes to the real git.

## Shell completions

Generated from the command list itself, so they cannot fall behind the
commands that exist.

**bash** — needs bash 4+ for the completion machinery, though the script
itself parses on 3.2:

```bash
svngit --completion bash > /usr/local/etc/bash_completion.d/svngit
# or, without installing a file:
echo 'eval "$(svngit --completion bash)"' >> ~/.bashrc
```

**zsh**:

```bash
svngit --completion zsh > "${fpath[1]}/_svngit"
# or:
echo 'eval "$(svngit --completion zsh)"' >> ~/.zshrc
```

**fish**:

```fish
svngit --completion fish > ~/.config/fish/completions/svngit.fish
```

These complete the `svngit` command. They deliberately leave `git` alone: if
you have git's own completions loaded they already cover the shim, and
replacing them would lose everything they know about refs and remotes.

## Uninstalling

```bash
pipx uninstall svngit          # or: pip uninstall svngit
brew uninstall svngit
```

Then remove the PATH line from your shell profile. svngit keeps per-checkout
state (the staging area, unpushed commits, stashes) outside your working
copies; to clear it:

```bash
rm -rf ~/.local/state/svngit ~/.cache/svngit
```

Nothing in your Subversion working copies is svngit's, so they are unaffected.
