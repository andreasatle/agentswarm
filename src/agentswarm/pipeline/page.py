"""A finished document as one static web page: Markdown to HTML, its LaTeX left for KaTeX to render in the
browser, a note at the top saying how it was made and checked, in the style of the pages on atle.dev."""

import html
import re
from pathlib import Path
from typing import ClassVar

import markdown


class PageRenderer:
    # Mathematics is taken out before Markdown sees it (an underscore or asterisk inside it would turn into
    # emphasis) and put back after, for KaTeX's auto-render to find in the browser
    DISPLAY: ClassVar[re.Pattern[str]] = re.compile(r"\$\$.+?\$\$", re.S)
    INLINE: ClassVar[re.Pattern[str]] = re.compile(r"(?<![\\$])\$(?!\s)[^$\n]+?(?<!\s)\$")
    KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist"
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
            "p{margin:.55rem 0}code{font-size:.85em;color:var(--muted)}a{color:var(--accent)}",
            ".note{color:var(--muted);font-size:.95rem;border-left:3px solid var(--accent);"
            "padding-left:.8rem;margin:1rem 0 2rem}",
            "table{border-collapse:collapse;font-size:.9rem;margin:1rem 0;display:block;overflow-x:auto}"
            "th,td{border:1px solid var(--rule);padding:.35rem .5rem;vertical-align:top}",
            "h3{font-size:1.05rem;margin:1.6rem 0 .4rem}li{margin:.3rem 0}"
            ".katex-display{overflow-x:auto;overflow-y:hidden}",
        )
    )

    def render(self, text: str, note: str) -> str:
        """The page for a document's Markdown; `note` is HTML, written by the framework, not the document."""
        math: list[str] = []

        def kept(match: re.Match[str]) -> str:
            math.append(match.group(0))
            return f"@@MATH{len(math) - 1}@@"

        text = self.INLINE.sub(kept, self.DISPLAY.sub(kept, text))
        first, _, body = text.partition("\n")
        title = first.lstrip("# ").strip() or "Untitled"
        content = markdown.markdown(body, extensions=["tables", "sane_lists"])
        content = re.sub(r"@@MATH(\d+)@@", lambda m: html.escape(math[int(m.group(1))], quote=False), content)
        title = re.sub(r"@@MATH(\d+)@@", lambda m: math[int(m.group(1))], title)
        render = (
            "renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},"
            "{left:'$',right:'$',display:false}],throwOnError:false})"
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

    def write(self, document: Path, out: Path, note: str) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.render(document.read_text(), note))
        return out

    @staticmethod
    def title(document: Path) -> str:
        return document.read_text().partition("\n")[0].lstrip("# ").strip() or document.stem
