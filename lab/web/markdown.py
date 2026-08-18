"""
lab/web/markdown.py
====================
A minimal Markdown-to-HTML renderer for strategy write-ups.

Not a dependency, on purpose — `CLAUDE.md` asks before adding one, and the
subset of Markdown a research write-up needs (headings, paragraphs, bold and
inline code, fenced code blocks, pipe tables, lists, links, rules) is small
enough to hand-roll to the same standard as the hand-rolled SVG charts.

Output is always escaped before any tag is added, so the source files are the
only thing that can end up as markup — there is no way for a table cell or a
list item to inject an element that was not written by this module.
"""

from __future__ import annotations

import html
import re

__all__ = ["render"]

_ATX = re.compile(r'^(#{1,6})\s+(.*)$')
_HR = re.compile(r'^(?:-{3,}|\*{3,}|_{3,})\s*$')
_FENCE = re.compile(r'^```(\w*)\s*$')
_UL = re.compile(r'^[-*]\s+(.*)$')
_OL = re.compile(r'^\d+\.\s+(.*)$')
_TABLE_SEP = re.compile(r'^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$')
_CONTINUATION = re.compile(r'^\s+\S')


def render(text: str) -> str:
    """Render one Markdown document to an HTML fragment (no `<html>`/`<body>`)."""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            lang = fence.group(1)
            i += 1
            body: list[str] = []
            while i < n and not _FENCE.match(lines[i]):
                body.append(lines[i])
                i += 1
            i += 1  # the closing fence
            code = html.escape("\n".join(body))
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>{code}</code></pre>")
            continue

        atx = _ATX.match(line)
        if atx:
            level = len(atx.group(1))
            out.append(f"<h{level}>{_inline(atx.group(2).strip())}</h{level}>")
            i += 1
            continue

        if _HR.match(line):
            out.append("<hr>")
            i += 1
            continue

        if line.lstrip().startswith(">"):
            quoted = []
            while i < n and lines[i].lstrip().startswith(">"):
                quoted.append(re.sub(r'^\s*>\s?', '', lines[i]))
                i += 1
            out.append(f"<blockquote>{render(chr(10).join(quoted))}</blockquote>")
            continue

        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            header = _split_row(line)
            aligns = _aligns(lines[i + 1])
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            out.append(_table(header, aligns, rows))
            continue

        ul, ol = _UL.match(line), _OL.match(line)
        if ul or ol:
            pattern = _OL if ol else _UL
            items = []
            while i < n and pattern.match(lines[i]):
                items.append(pattern.match(lines[i]).group(1))
                i += 1
                while i < n and _CONTINUATION.match(lines[i]) \
                        and not pattern.match(lines[i]):
                    items[-1] += " " + lines[i].strip()
                    i += 1
            tag = "ol" if ol else "ul"
            body = "".join(f"<li>{_inline(item)}</li>" for item in items)
            out.append(f"<{tag}>{body}</{tag}>")
            continue

        para = [line]
        i += 1
        while i < n and lines[i].strip() and not _starts_block(lines[i]):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{_inline(' '.join(l.strip() for l in para))}</p>")

    return "\n".join(out)


def _starts_block(line: str) -> bool:
    return bool(_ATX.match(line) or _HR.match(line) or _FENCE.match(line)
                or _UL.match(line) or _OL.match(line)
                or line.lstrip().startswith(">") or "|" in line)


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _aligns(separator_line: str) -> list[str]:
    out = []
    for cell in _split_row(separator_line):
        left, right = cell.startswith(":"), cell.endswith(":")
        out.append("center" if left and right else
                    "right" if right else "left" if left else "")
    return out


def _table(header: list[str], aligns: list[str], rows: list[list[str]]) -> str:
    def cell(tag: str, text: str, align: str) -> str:
        style = f' style="text-align:{align}"' if align else ""
        return f"<{tag}{style}>{_inline(text)}</{tag}>"

    def row(cells: list[str]) -> str:
        return "<tr>" + "".join(
            cell("td", c, aligns[j] if j < len(aligns) else "")
            for j, c in enumerate(cells)) + "</tr>"

    head = "<tr>" + "".join(
        cell("th", h, aligns[j] if j < len(aligns) else "")
        for j, h in enumerate(header)) + "</tr>"
    body = "".join(row(r) for r in rows)
    return (f'<div class="table-wrap"><table class="data prose-table">'
            f'<thead>{head}</thead><tbody>{body}</tbody></table></div>')


_CODE = re.compile(r'`([^`]+)`')
_BOLD = re.compile(r'\*\*([^*]+)\*\*')
_ITALIC = re.compile(r'\*([^*]+)\*')
_LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')


def _inline(text: str) -> str:
    """Escape, then re-introduce only the tags this module put in.

    Order matters: escaping first means the source can contain a literal `<`
    or `&' (this repository's write-ups quote inequalities like `alpha < 0`
    freely) without it being read as markup.
    """
    text = html.escape(text, quote=False)
    text = _CODE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _LINK.sub(r'<a href="\2">\1</a>', text)
    return text
