"""Shell completions, generated from the command registry.

Generated rather than hand-written for the same reason the docs are checked
against the registry: a completion list that is maintained separately goes
stale the first time someone adds a command and forgets.

    svngit --completion bash|zsh|fish

The refused commands are deliberately offered too. Someone reaching for
`git rebase` here should be able to complete it and read the explanation,
rather than be left wondering whether they mistyped it.
"""

from __future__ import annotations

from typing import List

from .commands import ALIASES, NO_EQUIVALENT, REGISTRY

SHELLS = ("bash", "zsh", "fish")


def command_names() -> List[str]:
    return sorted(set(REGISTRY) | set(ALIASES) | set(NO_EQUIVALENT))


def _describe(name: str) -> str:
    if name in ALIASES:
        return "alias for %s" % ALIASES[name]
    if name in NO_EQUIVALENT:
        return "no Subversion equivalent"
    return "translated to Subversion"


def generate(shell: str) -> str:
    if shell not in SHELLS:
        raise ValueError(
            "unknown shell %r (choose from %s)" % (shell, ", ".join(SHELLS))
        )
    return {"bash": _bash, "zsh": _zsh, "fish": _fish}[shell]()


# ----------------------------------------------------------------------
# bash
# ----------------------------------------------------------------------
def _bash() -> str:
    names = " ".join(command_names())
    return """\
# svngit completions for bash.
#
#   svngit --completion bash > /usr/local/etc/bash_completion.d/svngit
#   # or, without installing anything:
#   eval "$(svngit --completion bash)"
#
# Completes the svngit command itself. It deliberately does not touch `git`:
# if you have git's own completions loaded, they already cover the shim, and
# replacing them would lose everything they know about refs and remotes.

_svngit_completions()
{
    local commands="%s"
    local current="${COMP_WORDS[COMP_CWORD]}"

    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$commands --help --version --dry-run --trace --shim-path --completion" -- "$current") )
        return
    fi

    case "${COMP_WORDS[1]}" in
        checkout|switch|branch|merge|cherry-pick)
            # Branch names come from the server, so ask only when the user
            # has started typing: it is a network round trip.
            if [ -n "$current" ]; then
                COMPREPLY=( $(compgen -W "$(svngit branch 2>/dev/null | tr -d '* ')" -- "$current") )
                return
            fi
            ;;
        help)
            COMPREPLY=( $(compgen -W "$commands" -- "$current") )
            return
            ;;
    esac

    COMPREPLY=( $(compgen -f -- "$current") )
}

complete -o default -F _svngit_completions svngit
""" % names


# ----------------------------------------------------------------------
# zsh
# ----------------------------------------------------------------------
def _zsh() -> str:
    rows = "\n".join(
        "        '%s:%s'" % (name, _describe(name)) for name in command_names()
    )
    return """\
#compdef svngit
# svngit completions for zsh.
#
#   svngit --completion zsh > "${fpath[1]}/_svngit"
#   # or, without installing anything:
#   eval "$(svngit --completion zsh)"
#
# Completes the svngit command itself, not `git`: git's own completions
# already cover the shim and know far more about refs than this could.

_svngit() {
    local -a commands
    commands=(
%s
    )

    _arguments -C \\
        '(- *)--help[show the command list]' \\
        '(- *)--version[show the version]' \\
        '--dry-run[print the svn commands instead of running them]' \\
        '--trace[print every svn command as it runs]' \\
        '(- *)--shim-path[print the directory holding the git shim]' \\
        '(- *)--completion[print a completion script]:shell:(bash zsh fish)' \\
        '1: :->command' \\
        '*:: :->argument'

    case $state in
        command)
            _describe -t commands 'svngit command' commands
            ;;
        argument)
            case $words[1] in
                checkout|switch|branch|merge|cherry-pick)
                    local -a branches
                    branches=( ${(f)"$(svngit branch 2>/dev/null | tr -d '* ')"} )
                    _describe -t branches 'branch' branches && return
                    ;;
            esac
            _files
            ;;
    esac
}

compdef _svngit svngit
""" % rows


# ----------------------------------------------------------------------
# fish
# ----------------------------------------------------------------------
def _fish() -> str:
    lines = [
        "# svngit completions for fish.",
        "#",
        "#   svngit --completion fish > ~/.config/fish/completions/svngit.fish",
        "#",
        "# Completes the svngit command itself, not `git`: fish ships git",
        "# completions that already cover the shim.",
        "",
        "complete -c svngit -f",
        "",
    ]
    for name in command_names():
        lines.append(
            "complete -c svngit -n __fish_use_subcommand -a %s -d '%s'"
            % (name, _describe(name).replace("'", ""))
        )
    lines.extend(
        [
            "",
            "complete -c svngit -l help -d 'show the command list'",
            "complete -c svngit -l version -d 'show the version'",
            "complete -c svngit -l dry-run -d 'print the svn commands instead of running them'",
            "complete -c svngit -l trace -d 'print every svn command as it runs'",
            "complete -c svngit -l shim-path -d 'print the directory holding the git shim'",
            "complete -c svngit -l completion -d 'print a completion script' "
            "-xa 'bash zsh fish'",
            "",
            "# Branch names cost a round trip to the server, so only offer them",
            "# for the commands that take one.",
            "complete -c svngit -n '__fish_seen_subcommand_from checkout switch branch "
            "merge cherry-pick' -a \"(svngit branch 2>/dev/null | string trim | string "
            "replace -r '^\\\\* ' '')\"",
            "",
            "complete -c svngit -n '__fish_seen_subcommand_from add rm mv restore diff "
            "grep blame' -F",
        ]
    )
    return "\n".join(lines) + "\n"
