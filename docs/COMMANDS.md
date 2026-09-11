# Command mapping

Every git command svngit understands, and what it actually runs.

`^/` is Subversion shorthand for the repository root. Paths passed to `svn` are
always absolute, so behaviour does not depend on which subdirectory you are in.

## Working copy

| git | svn | Notes |
| --- | --- | --- |
| `git clone <url>` | `svn checkout <url>/trunk` | Descends into trunk, the way clone gives you the default branch. `--full` checks out the whole repository. |
| `git clone -b <name> <url>` | `svn checkout <url>/branches/<name>` | |
| `git clone --depth N` | *ignored* | git's `--depth` limits history; svn's limits tree depth. Use `-r` for an older tree. |
| `git init` | — | Explains that a working copy is always a checkout of a server-side repository. `--standalone <dir>` runs `svnadmin create` plus a layout commit and checks it out. |
| `git status` | `svn status --xml` | Rendered as git's two-column status, plus unpushed local commits. Local only unless `svngit.checkupstream=true`. |
| `git status -s` / `--porcelain` | `svn status --xml` | |
| `git add <new file>` | `svn add --parents <path>` | Also recorded in the emulated index. |
| `git add <modified file>` | *no svn call* | Subversion has no staging area; only the index changes. |
| `git add <deleted file>` | `svn delete --force <path>` | Stages the removal, as git does. |
| `git add .` / `-A` | as above, for everything | |
| `git add -u` | as above, tracked paths only | |
| `git add -p` | *no svn call* | Stages hunk by hunk into the emulated index. Full answer set: `y n q a d s e j J k K g / ?`. Only the moves that exist are offered. |
| `git add -e` | *no svn call* | Opens the whole diff in `$EDITOR`; what survives the edit is staged. Hunk headers are recalculated, so stale `@@` counts are fine. A patch that fails to apply stages nothing. |
| `git rm <path>` | `svn delete <path>` | |
| `git rm --cached <path>` | `svn delete --keep-local <path>` | |
| `git mv <a> <b>` | `svn move <a> <b>` | |
| `git restore <path>` | `svn revert -R <path>` | |
| `git restore --staged <path>` | *index only*, or `svn revert` | Unschedules an add or delete, which is what unstaging one means. |
| `git restore -s <rev> <path>` | `svn update -r <rev> <path>` | |
| `git reset <path>` | *index only*, or `svn revert` | Unstage. |
| `git reset --soft` | *local queue only* | Un-commits the last unpushed commit back into the index. |
| `git reset --hard` | `svn revert -R .` | |
| `git reset --hard <rev>` | `svn revert -R .` + `svn update -r <rev>` | |
| `git clean -f` | *unlinks unversioned paths* | `-n` previews, `-d` includes directories, `-x` includes ignored. |

## History

| git | svn | Notes |
| --- | --- | --- |
| `git log` | `svn log --xml` | Rendered as git log. Unpushed local commits appear on top. |
| `git log --oneline` | `svn log --xml` | `r413 message` |
| `git log -n N` / `-N` | `svn log -l N` | |
| `git log <rev>..<rev>` | `svn log -r A:B` | |
| `git log --grep <text>` | `svn log --search <text>` | |
| `git log --author <name>` | *filtered locally* | svn has no author filter. |
| `git log -p` | `svn log --diff` | |
| `git log --stat` / `--name-only` | `svn log -v` | |
| `git log --graph` | *ignored* | Subversion history is linear. |
| `git show <rev>` | `svn log -v -r N` + `svn diff -c N` | |
| `git diff` | *staged blobs vs worktree*, `svn diff` | Honours the emulated index. |
| `git diff --cached` | `svn cat -r BASE` vs staged blobs | |
| `git diff HEAD` | `svn diff` | |
| `git diff <a>..<b>` | `svn diff -r A:B` | |
| `git diff --stat` / `--numstat` / `--name-only` / `--name-status` | as above, summarised | |
| `git blame <file>` | `svn blame --xml` | Reformatted with revision, author and date per line. `-L` limits the range, `-w` ignores whitespace. |
| `git log -S` / `-G` | `svn diff` per revision | `svn log --search` matches messages only, so the pickaxe reads each revision's diff. Narrow the range first. |
| `git shortlog` | `svn log --xml` | Grouped by author. `-s` counts only, `-n` sorts by count. |
| `git describe` | `svn log` on `^/tags` | Names a revision after the newest tag copied at or before it: `v1.0-3-r412`. |
| `git whatchanged` | `svn log -v` | git's older spelling of `log --raw`. |
| `git grep <pattern>` | *no svn call* | Searches versioned files, skipping `.svn` and anything unversioned or ignored. `--untracked` widens it. |
| `git check-ignore <path>` | `svn propget svn:ignore` | Reads the patterns, so a path that does not exist yet can still be checked. |

## Patches and archives

| git | svn | Notes |
| --- | --- | --- |
| `git apply <patch>` | *no svn call* | svngit's own applier, so stale hunk headers and svn-style `Index:` headers both work. All-or-nothing: a patch that fails changes nothing. `--check`, `-R`, `--stat`, `-p<n>`. |
| `git format-patch <range>` | `svn log` + `svn diff -c N` | One mbox-format file per revision, with git-style headers so `git apply` elsewhere accepts them. `--stdout`, `-o`, `-<n>`. |
| `git archive -o <file> <rev>` | `svn export` | tar, tar.gz or zip, chosen by `--format` or the file extension. `--prefix` nests the contents. |

## Sharing

| git | svn | Notes |
| --- | --- | --- |
| `git commit -m` (deferred) | *local queue* | The default. Nothing reaches the server. |
| `git commit -m` (immediate) | `svn commit -m <msg> <paths>` | With `svngit.commitmode=immediate`. |
| `git commit -a` | stages tracked changes first | |
| `git commit` (no `-m`) | opens `$EDITOR` | |
| `git commit --amend` | *amends the queued commit* | Once pushed, only the message can change, via `svn propset --revprop svn:log` — and only if the server allows it. |
| `git push` | one `svn commit` per queued commit | Refuses if the server has moved ahead. `--dry-run` lists without committing. |
| `git push --delete <branch>` | `svn delete ^/branches/<name>` | |
| `git pull` | `svn update --accept postpone` | Reports conflicts the way git does. |
| `git pull --rebase` | `svn update` | svn update is already a rebase-shaped operation. |
| `git fetch` | `svn info -r HEAD` + `svn log` | Lists incoming revisions; cannot download them. |

## Branching

| git | svn | Notes |
| --- | --- | --- |
| `git branch` | `svn list ^/branches` | Trunk is listed as `trunk`. |
| `git branch -v` | `svn list` + `svn log -l 1` per branch | |
| `git branch <name>` | `svn copy ^/<current> ^/branches/<name> -m` | A server-side commit; visible to everyone at once. |
| `git branch -d <name>` | `svn delete ^/branches/<name> -m` | |
| `git branch -m <new>` | `svn move` (+ `svn switch` if current) | |
| `git branch -c <new>` | `svn copy` | |
| `git branch --merged` / `--no-merged` | `svn mergeinfo --show-revs eligible` | A branch with nothing eligible is fully merged. |
| `git checkout <name>` | `svn switch ^/branches/<name>` | |
| `git checkout -b <name>` | `svn copy` then `svn switch` | |
| `git checkout -- <paths>` | `svn revert -R <paths>` | |
| `git switch <name>` | `svn switch` | |
| `git switch -c <name>` | `svn copy` then `svn switch` | |
| `git merge <name>` | `svn merge ^/branches/<name>` then `svn commit .` | Commits the root so `svn:mergeinfo` is recorded. Requires an empty push queue. |
| `git merge --abort` | `svn revert -R .` | |
| `git cherry-pick <rev>` | `svn merge -c <rev> <source>` then commit | Source branch inferred from the revision's own paths. |
| `git revert <rev>` | `svn merge -c -<rev> .` then commit | A reverse merge — *not* `svn revert`. |
| `git tag` | `svn list ^/tags` | |
| `git tag <name>` | `svn copy ^/<current> ^/tags/<name> -m` | |
| `git tag -d <name>` | `svn delete ^/tags/<name> -m` | |

## Stash

Stash entries are a `svn diff` patch plus, with `-u`, the content of untracked
files, all kept in the local object store. The server is never involved.

| git | Behaviour |
| --- | --- |
| `git stash` / `push` | `svn diff` → object store, then `svn revert -R .` |
| `git stash -p` | Choose hunks to take out of the working copy. Accepting one removes it and saves it; declining leaves it alone. Stores both content versions, so `pop` restores exactly, or three-way merges if the file moved on. |
| `git stash -u` | Also saves and removes untracked files |
| `git stash list` | Newest first, as `stash@{0}` |
| `git stash pop` / `apply` | `svn patch`, plus restoring untracked files and the index |
| `git stash show` / `drop` / `clear` | |
| `git stash branch <name>` | `git checkout -b` then pop | For a stash that no longer applies where you are. |

## Sparse checkouts and tools

| git | svn | Notes |
| --- | --- | --- |
| `git sparse-checkout set <paths>` | `svn update --set-depth` | Excludes every other top-level directory and restores the named ones at full depth. Cone mode only, since Subversion excludes directories rather than matching patterns. |
| `git sparse-checkout list` / `add` / `init` / `disable` | | |
| `git difftool` | `svn diff --diff-cmd` | Tool from `--tool` or `diff.tool`, else the first of difft, delta, colordiff, diff. |
| `git mergetool` | conflict files + `svn resolve` | Hands the tool svn's `.mine` / `.rOLD` / `.rNEW` files, then records the result with `svn resolve --accept working`. |

## Plumbing

| git | Behaviour |
| --- | --- |
| `git config <key> [<value>]` | svngit's own per-working-copy settings |
| `git config --list` / `--unset` | |
| `git remote` / `-v` / `show origin` | Reports the working copy's URL as `origin` |
| `git remote add` / `rename` / `set-url` | Refused: a working copy is bound to one URL |
| `git rev-parse <rev>` | Resolves to `rNNN` |
| `git rev-parse --abbrev-ref HEAD` | Current branch name |
| `git rev-parse --show-toplevel` | Working copy root |
| `git rev-parse --git-dir` | svngit's state directory for this checkout |
| `git ls-files` | `svn list -R` |
| `git ls-files -m` / `-o` / `-d` | From `svn status` |
| `git version` | svngit and svn versions |

## Revision syntax

| git | Resolves to |
| --- | --- |
| `HEAD`, `@` | svn `BASE` — your working copy's revision, **not** the server's tip |
| `@{u}`, `@{upstream}`, `origin/HEAD`, `FETCH_HEAD` | svn `HEAD` — the newest revision on the server |
| `HEAD~N`, `HEAD^` | N revisions back *in that path's log* |
| `HEAD^2` | Refused: no merge parents to choose between |
| `1234`, `r1234` | Revision 1234 |
| `<branch>`, `<tag>` | The revision that branch or tag was last changed at |
| `A..B`, `A...B` | `svn -r A:B` |

## Aliases

`co` `ci` `st` `br` `df` `lg` `cp` `stage` `unstage` `annotate` `praise`

## No equivalent

These report what is missing and what to reach for instead: `rebase`,
`bisect`, `submodule`, `worktree`, `reflog`, `gc`, `am`, `notes`, `bundle`,
`range-diff`, `rerere`, `filter-branch`, `fast-export`, `fast-import`,
`maintenance`, `scalar`, `backfill`, `count-objects`, `fsck`, `replace`,
`gitk`, `gui`, `citool`, `instaweb`, and `git clean -i`.
