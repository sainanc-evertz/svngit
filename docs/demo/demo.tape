# The README's top demo: git commands, Subversion underneath.
#
#     sh docs/demo/make.sh
#
# Everything here runs for real against the checkout setup.sh builds; no part
# of the output is typed out by hand.

Output "/tmp/svngit-demo/frames/"

Set Shell "bash"
Set FontSize 16
Set Width 1020
Set Height 800
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms

# Put the `git` shim ahead of the real git, and work in the demo checkout.
Hide
Type "export PATH=$PWD/bin:$PWD/.venv/bin:$PATH" Enter
Type "export SVNGIT_STATE_DIR=/tmp/svngit-demo/state PS1='$ '" Enter
Type "cd /tmp/svngit-demo/work && clear" Enter
Show

Sleep 800ms
Type "# An ordinary Subversion checkout -- no .git anywhere." Enter
Sleep 300ms
Type "ls -a" Enter
Sleep 1.8s

Type "git status" Enter
Sleep 3.2s

Type "git add parser.c" Enter
Sleep 600ms
Type 'git commit -m "Fix the off-by-one in parse_header"' Enter
Sleep 3s

Type "git push" Enter
Sleep 3.2s

Type "# To everyone else on the team, it was always just Subversion." Enter
Sleep 300ms
Type "svn log -l 1 --quiet" Enter
Sleep 3.5s
