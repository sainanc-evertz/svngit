#!/usr/bin/env python3
"""Render captured terminal output as an SVG "screenshot".

Used for the stills in the README. SVG rather than PNG because it stays sharp
at any zoom, weighs a few kilobytes, and shows up as readable text in a diff
rather than as a binary blob.

The colours match the GIF's theme (Catppuccin Mocha) so the two read as a
set. Only the shell's own parts are coloured -- the prompt, and comments you
typed. svngit's output is left plain, because svngit does not colourise it
and a screenshot that suggested otherwise would be a small lie.

    python3 docs/demo/render_svg.py <capture.txt> <out.svg> [title]

The capture is plain text: lines beginning "$ " are treated as typed. Long
lines are wrapped at COLUMNS, the way they would be in a terminal that
width, so one long error message cannot stretch the image off the page.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

BACKGROUND = "#1e1e2e"
CHROME = "#181825"
TEXT = "#cdd6f4"
PROMPT = "#a6e3a1"
COMMENT = "#6c7086"
TITLE = "#9399b2"
DOTS = ("#f38ba8", "#f9e2af", "#a6e3a1")

FONT = (
    "'SF Mono','SFMono-Regular',Menlo,Consolas,"
    "'DejaVu Sans Mono','Liberation Mono',monospace"
)
FONT_SIZE = 13.5
LINE_HEIGHT = 20.0
CHAR_WIDTH = 8.13  # measured for this family at this size
PAD_X = 18.0
PAD_TOP = 40.0  # below the title bar
PAD_BOTTOM = 16.0
CHROME_HEIGHT = 30.0
RADIUS = 8.0
COLUMNS = 76


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_line(text: str, y: float) -> str:
    """One terminal line, with the shell's own colouring only."""
    common = 'x="%.1f" y="%.1f" xml:space="preserve"' % (PAD_X, y)

    if text.startswith("$ "):
        body = text[2:]
        if body.lstrip().startswith("#"):
            return (
                '<text %s><tspan fill="%s">$ </tspan>'
                '<tspan fill="%s">%s</tspan></text>'
                % (common, PROMPT, COMMENT, escape(body))
            )
        return (
            '<text %s><tspan fill="%s">$ </tspan>'
            '<tspan fill="%s">%s</tspan></text>'
            % (common, PROMPT, TEXT, escape(body))
        )

    return '<text %s fill="%s">%s</text>' % (common, TEXT, escape(text))


def wrap(lines, columns: int = COLUMNS):
    """Fold long lines, as a terminal of this width would.

    Continuations are indented to the text of the line they came from, which
    a real terminal does not do -- but an unindented continuation of a
    `fatal:` message reads as a separate message, which is worse.
    """
    out = []
    for line in lines:
        if len(line) <= columns:
            out.append(line)
            continue
        indent = "  " if not line.startswith("$ ") else "    "
        out.extend(
            textwrap.wrap(
                line,
                width=columns,
                subsequent_indent=indent,
                break_long_words=True,
                break_on_hyphens=False,
                replace_whitespace=False,
                drop_whitespace=True,
            )
            or [line]
        )
    return out


def render(lines, title: str = "") -> str:
    lines = wrap(lines)
    widest = max([len(line) for line in lines] + [len(title) + 8, 28])
    width = widest * CHAR_WIDTH + PAD_X * 2
    height = PAD_TOP + len(lines) * LINE_HEIGHT + PAD_BOTTOM

    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
        'viewBox="0 0 %.0f %.0f" role="img" aria-label="%s">'
        % (width, height, width, height, escape(title or "terminal session")),
        '<rect width="%.0f" height="%.0f" rx="%.0f" fill="%s"/>'
        % (width, height, RADIUS, BACKGROUND),
        # Title bar: a rounded rect clipped to its top half by a plain one.
        '<path d="M0 %.0f V%.0f a%.0f %.0f 0 0 1 %.0f -%.0f H%.0f '
        'a%.0f %.0f 0 0 1 %.0f %.0f V%.0f Z" fill="%s"/>'
        % (CHROME_HEIGHT, RADIUS, RADIUS, RADIUS, RADIUS, RADIUS,
           width - RADIUS, RADIUS, RADIUS, RADIUS, RADIUS, CHROME_HEIGHT, CHROME),
    ]
    for index, colour in enumerate(DOTS):
        out.append(
            '<circle cx="%.0f" cy="15" r="5" fill="%s"/>' % (18 + index * 18, colour)
        )
    if title:
        out.append(
            '<text x="%.0f" y="19.5" font-family="%s" font-size="11.5" '
            'fill="%s" text-anchor="middle">%s</text>'
            % (width / 2, FONT, TITLE, escape(title))
        )

    out.append(
        '<g font-family="%s" font-size="%.1f">' % (FONT, FONT_SIZE)
    )
    for index, line in enumerate(lines):
        out.append(render_line(line, PAD_TOP + index * LINE_HEIGHT))
    out.append("</g></svg>")
    return "\n".join(out) + "\n"


def main(argv) -> int:
    if len(argv) < 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    source, target = Path(argv[1]), Path(argv[2])
    title = argv[3] if len(argv) > 3 else ""
    lines = source.read_text(encoding="utf-8").rstrip("\n").split("\n")
    target.write_text(render(lines, title), encoding="utf-8")
    print("%s  (%d lines, %d bytes)" % (target, len(lines), target.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
