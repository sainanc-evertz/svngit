# svngit

Type git commands. Subversion does the work.

`svngit` is a middleware layer that sits between you and a Subversion working
copy. You run `git status`, `git add`, `git commit`, `git push`; it translates
each one into the equivalent `svn` invocation, runs it, and renders the result
the way git would have.

```console
$ git status
On branch trunk
Your working copy is at r412.

Changes to be committed:
  (use "git restore --staged <file>..." to unstage)
	modified:        src/parser.c

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	notes.md

$ git commit -m "Fix the off-by-one in the parser"
[trunk 4f1c8ab] Fix the off-by-one in the parser
 1 file changed, 3 insertions(+), 1 deletion(-)
This commit is local. Run "git push" to send it to Subversion.

$ git push
  4f1c8ab -> r413  Fix the off-by-one in the parser
To https://svn.example.com/repo/trunk
   r412..r413  trunk -> trunk
Pushed 1 commit.
```

Nothing about the repository changes. Colleagues keep using `svn`, the server
stays a Subversion server, and no mirror or import step is involved.

## Is this `git svn`?

No, and it is worth knowing the difference before you pick one.

`git svn` (which ships with git) creates a **real git repository** that mirrors
a Subversion one. You get genuine git objects, real local branches, rebase, and
bisect — at the cost of an import that can take hours on a large repository, a
`.git` directory that duplicates all the history, and `git svn dcommit` as the
only way back to the server.

`svngit` keeps the **Subversion working copy** as the only source of truth and
translates commands on the fly. There is no import, no duplicated history, and
no clone step to wait on. The trade is that git features which genuinely need
git's object model — rebase, bisect, reflog, local-only branches — do not
exist here, and say so when you reach for them.

Use `git svn` if you want git. Use `svngit` if you have to stay on Subversion
and want your git muscle memory back.

## Install

Requires Python 3.9+ and the `svn` command-line client.

```bash
pip install -e .
```

That installs the `svngit` command. To make plain `git` work inside Subversion
checkouts, put this repository's `bin/` directory early on your `PATH`:

```bash
export PATH="/path/to/svngit/bin:$PATH"
```

`bin/git` is a small shim that decides where each invocation goes:

| Where you are | What runs |
| --- | --- |
| Inside a git repository | the real `git`, untouched |
| Inside a Subversion working copy | `svngit` |
| Anywhere else | the real `git` |
| `SVNGIT_DISABLE=1` set | the real `git`, always |

Whichever of `.git` or `.svn` is found first walking up from your current
directory wins, so a git repo checked out inside an svn tree still routes
correctly. Set `SVNGIT_DISABLE=1` for a single command to bypass the shim:

```bash
SVNGIT_DISABLE=1 git status   # the real git, even in an svn working copy
```

Because an `https://` URL could belong to either system, the shim only claims
`clone` for unmistakably-Subversion URLs (`svn://`, `svn+ssh://`). For anything
else, clone explicitly the first time:

```bash
svngit clone https://svn.example.com/repo
```

## The mental model

Four mappings explain most of the behaviour.

**A branch is a directory.** `git checkout feature-x` becomes
`svn switch ^/branches/feature-x`. Creating a branch is a server-side copy, so
it is a commit and everyone can see it immediately. There is no such thing as a
local-only branch.

**git's HEAD is svn's BASE.** This one bites people. In git, `HEAD` is your
local tip; in Subversion, `HEAD` is the newest revision *on the server*. svngit
resolves `HEAD` to `BASE` and `@{u}` / `origin/HEAD` to svn's `HEAD`, so
`git diff HEAD` compares against what you checked out, exactly as it does in
git.

**Revisions stand in for hashes.** Where git prints a sha, svngit prints
`r413`. `HEAD~3` walks back three revisions *that touched the path in
question*, since svn revision numbers are repository-global.

**The staging area is emulated.** Subversion has no index. svngit keeps one,
along with the queue of commits you have not pushed and your stashes, in
`~/.local/state/svngit/<checkout>/` — outside the working copy, so `svn status`
stays clean for everyone else and nothing can be committed by accident.
Staging copies file content into a local object store, which is why editing a
file after `git add` shows up as `MM` and commits the version you staged.

## commit and push

Git commits locally and pushes later. Subversion commits straight to the
server. svngit reconciles this with a local commit queue, and you choose which
end of the trade you want:

```bash
git config svngit.commitmode deferred    # the default
```

`git commit` snapshots the staged content and queues the commit locally.
Nothing reaches the server. `git push` replays the queue as one `svn commit`
per local commit, so two local commits become two revisions with their own
messages. `git reset --soft` un-commits the last queued one.

```bash
git config svngit.commitmode immediate
```

`git commit` runs `svn commit` directly, and `git push` reports that there is
nothing to do. Closer to what your Subversion-using colleagues see happening.

Push is careful about work in progress. Replaying rewinds each file through
every queued commit's content, so before it starts it snapshots everything the
queue touches and restores it afterwards — edits you made after your last
`git commit` are still there when the push finishes. It also refuses to push
when the server has moved ahead, and tells you to `git pull` first. If a commit
midway through the queue is rejected, the ones already accepted are dropped
from the queue and the rest stay in it.

## Things that work differently, and why

Merges, cherry-picks and reverts commit directly to Subversion regardless of
`commitmode`. `svn merge` records `svn:mergeinfo` on the working copy root, and
if that property is not committed with the merge, Subversion will happily
re-merge the same revisions later. Committing the root means these commands
refuse to run while you have unpushed local commits — push first.

`git revert` is a reverse merge (`svn merge -c -N`), which creates a new
revision undoing an old one. That is git's meaning. Subversion's own
`svn revert` throws away local edits, which here is `git restore` or
`git checkout --`.

`git stash` is a local `svn diff` plus `svn patch`, stored in the object store.
Nothing touches the server. `git stash -p` picks hunks with the same prompt as
`git add -p`, but the sense is inverted: a hunk you accept is taken *out* of
the working copy and saved, and one you decline stays put. It diffs against
the last commit rather than the index, since a stash saves staged work too,
and `pop` three-way merges so a file you kept working on is not clobbered.

`git fetch` lists the revisions waiting on the server but cannot download them,
because a Subversion working copy has nowhere to keep revisions it has not
applied. `git pull` applies them.

`git add -p` and `git add -e` both work, because the emulated index stores
content rather than a flag: staging part of a file writes a blob that is
neither BASE nor the worktree, and everything downstream already reads that
blob. `-p` takes git's full answer set -- `y n q a d s e j J k K g / ?` -- so you
can move between hunks, jump to one by number, or search for one by regex, and
the prompt offers only the moves that exist from where you are. `e` opens the
current hunk alone in `$EDITOR`; `-e` opens the whole diff instead. Both can
stage text that was never on disk, which is what editing a patch is for. Hunk
headers are recalculated on the way back in, so there is no need to fix the
`@@` counts by hand. An edit that fails to apply is refused -- `-p` puts you
back in the editor, `-e` stages nothing at all.

A queued local commit counts as part of what your working copy is based on.
Subversion still calls such a file modified, since nothing has been pushed, but
`git status`, `git diff` and `git add -p` all treat it as committed — so a hunk
you have already committed locally is not offered to you a second time.

Commands with no honest translation say so and point at the nearest thing —
`rebase`, `bisect`, `reflog`, `submodule`, `worktree`, `gc`, `am`, and
`notes`.

The full command-by-command mapping is in [docs/COMMANDS.md](docs/COMMANDS.md).

## Configuration

Settings live per working copy and are read and written with `git config`.

| Key | Default | Meaning |
| --- | --- | --- |
| `svngit.commitmode` | `deferred` | `deferred` or `immediate` (see above) |
| `svngit.trunk` | `trunk` | Trunk directory name |
| `svngit.branches` | `branches` | Branches directory name |
| `svngit.tags` | `tags` | Tags directory name |
| `svngit.layout` | probed once | `standard` or `flat`; cached after the first probe |
| `svngit.checkupstream` | unset | `true` makes `git status` ask the server how far behind you are (a network round trip per status) |
| `svngit.quiet` | unset | `true` silences the explanatory notes |
| `user.name` | your login name | Author recorded on queued commits |

Environment variables: `SVNGIT_SVN` (path to the svn binary), `SVNGIT_STATE_DIR`
(where state is kept), `SVNGIT_TRACE=1` (print every svn command), and
`SVNGIT_DISABLE=1` (bypass the shim).

## Seeing what it does

Two global options make the translation visible, which is useful both for
learning Subversion and for filing a bug:

```bash
git --trace status      # print every svn command as it runs
git --dry-run push      # print the svn commands that would change something
```

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

The suite has two halves. Most tests use a recording client that replaces the
subprocess call, so they assert the exact `svn` argv each git command produces
— that is the contract of a translation layer, and it runs in well under a
second. The tests in `test_integration.py` build a real repository over
`file://` and check the resulting repository state; they skip automatically
when `svn` and `svnadmin` are not installed, so install Subversion before
trusting a green run.

```bash
brew install subversion     # macOS, to enable the integration tests
apt install subversion      # Debian/Ubuntu
```

## Status

Alpha. The command surface in `docs/COMMANDS.md` is implemented, and the whole
clone → add → commit → push → branch → merge cycle is exercised end to end
against a real Subversion repository in the test suite.

What that does *not* cover: a large real-world repository, an actual network
server (the tests use `file://`), authentication, externals, unusual layouts,
and merge-heavy histories. Treat `--dry-run` and `--trace` as your friends on
first contact with a repository that matters.

## License

MIT. See [LICENSE](LICENSE).
