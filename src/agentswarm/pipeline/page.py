"""A finished document as one static web page: Markdown to HTML, its LaTeX left for KaTeX to render in the
browser, a note at the top saying how it was made and checked, in the style of the pages on atle.dev."""

import html
import re
from pathlib import Path
from typing import ClassVar

import markdown


class PageRenderer:
    # Text a writer means literally but Markdown, KaTeX or a browser would read as markup. Code spans and
    # mathematics are taken out before Markdown sees the text and put back after (an underscore or asterisk
    # inside them would turn into emphasis). Inline math is found here, by Pandoc's rule (no space inside
    # either dollar, no digit after the closing one, so "$45 billion and a $100 billion" is money), or in
    # \(...\), and handed to KaTeX as \(...\): the browser never pairs single dollars itself (2026-11-10: it
    # set the text between two amounts in the LLM-scaling analysis as one italic formula). Raw HTML is shown
    # as text, never run (a page or a source could hold a script); an asterisk inside a word or number is a
    # times sign (2*3*4, not 2<em>3</em>4); \$ is a dollar; a paragraph starting with a year is not a list.
    CODE: ClassVar[re.Pattern[str]] = re.compile(r"```.*?```|`[^`\n]+`", re.S)
    DISPLAY: ClassVar[re.Pattern[str]] = re.compile(r"\$\$.+?\$\$|\\\[.+?\\\]", re.S)
    INLINE: ClassVar[re.Pattern[str]] = re.compile(
        r"(?<![\\$\w])\$(?![\s$])[^$\n]+?(?<![\s\\])\$(?!\d)|\\\(.+?\\\)"
    )
    TIMES: ClassVar[re.Pattern[str]] = re.compile(r"(?<=[\w)])\*(?=[\w(])")
    YEAR: ClassVar[re.Pattern[str]] = re.compile(r"^(\d{4})\. ", re.M)
    KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist"
    # A long word (a bare link in the references) wraps rather than widening the page on a phone.
    # A table scrolls sideways on a phone, and holds KaTeX's hidden copy of its math for screen readers
    # (position:relative), which otherwise widened the whole page (the mech-interp tutorial, 2026-11-10).
    STYLE = "\n".join(
        (
            ":root{--bg:#fbfbf9;--fg:#1d1d1b;--muted:#5d5d58;--accent:#2f6f5e;--rule:#e2e2dc}",
            '@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#141513;--fg:#e8e8e3;'
            "--muted:#a3a39b;--accent:#7fc4ae;--rule:#2b2c29}}",
            ':root[data-theme="dark"]{--bg:#141513;--fg:#e8e8e3;--muted:#a3a39b;'
            "--accent:#7fc4ae;--rule:#2b2c29}",
            'body{background:var(--bg);color:var(--fg);font:17px/1.6 Georgia,"Iowan Old Style",serif;'
            "max-width:46rem;margin:0 auto;padding:2.5rem 16px 4rem}",
            "h1{font-size:1.9rem;line-height:1.25;margin:0 0 .4rem}"
            "h2{font-size:1.25rem;margin:2.2rem 0 .6rem;"
            "border-top:1px solid var(--rule);padding-top:1.2rem}",
            "p{margin:.55rem 0}code{font-size:.85em;color:var(--muted)}a{color:var(--accent)}"
            "p,li{overflow-wrap:break-word}",
            ".note{color:var(--muted);font-size:.95rem;border-left:3px solid var(--accent);"
            "padding-left:.8rem;margin:1rem 0 2rem}",
            "table{border-collapse:collapse;font-size:.9rem;margin:1rem 0;display:block;overflow-x:auto;"
            "position:relative}"
            "th,td{border:1px solid var(--rule);padding:.35rem .5rem;vertical-align:top}",
            "h3{font-size:1.05rem;margin:1.6rem 0 .4rem}li{margin:.3rem 0}"
            ".katex-display{overflow-x:auto;overflow-y:hidden}",
        )
    )

    def render(self, text: str, note: str) -> str:
        """The page for a document's Markdown; `note` is HTML, written by the framework, not the document."""
        kept: list[str] = []

        def keep(found: str) -> str:
            kept.append(found)
            return f"@@KEPT{len(kept) - 1}@@"

        def math(match: re.Match[str]) -> str:
            found = match.group(0)
            if found.startswith("$$") or found.startswith("\\"):
                return keep(found)
            return keep(f"\\({found[1:-1]}\\)")

        text = self.CODE.sub(lambda m: keep(m.group(0)), text)
        text = self.INLINE.sub(math, self.DISPLAY.sub(math, text))
        text = self.YEAR.sub(r"\1\\. ", self.TIMES.sub(r"\\*", text.replace("\\$", "&#36;")))
        first, _, body = text.partition("\n")
        title = first.lstrip("# ").strip() or "Untitled"
        converter = markdown.Markdown(extensions=["tables", "sane_lists", "fenced_code"])
        converter.preprocessors.deregister("html_block")
        converter.inlinePatterns.deregister("html")
        content = self.restore(converter.convert(self.restore(body, kept, code_only=True)), kept)
        title = self.restore(title, kept, escape=False).replace("&#36;", "$")
        render = (
            "renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},"
            "{left:'\\\\[',right:'\\\\]',display:true},"
            "{left:'\\\\(',right:'\\\\)',display:false}],throwOnError:false})"
        )
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)}</title>\n"
            f'<link rel="stylesheet" href="{self.KATEX}/katex.min.css">\n'
            f'<script defer src="{self.KATEX}/katex.min.js"></script>\n'
            f'<script defer src="{self.KATEX}/contrib/auto-render.min.js" onload="{render}"></script>\n'
            f"<style>{self.STYLE}</style></head><body><h1>{html.escape(title)}</h1>\n"
            f'<p class="note">{note}</p>\n{content}\n</body></html>\n'
        )

    @staticmethod
    def restore(text: str, kept: list[str], code_only: bool = False, escape: bool = True) -> str:
        """Code spans go back before Markdown (it renders them, escaping what they hold); mathematics after,
        escaped for HTML, for KaTeX to find in the browser."""

        def back(match: re.Match[str]) -> str:
            found = kept[int(match.group(1))]
            if found.startswith("`"):
                return found
            if code_only:
                return match.group(0)
            return html.escape(found, quote=False) if escape else found

        return re.sub(r"@@KEPT(\d+)@@", back, text)

    def write(self, document: Path, out: Path, note: str) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.render(document.read_text(), note))
        return out
