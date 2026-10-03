"""Checks the handbook's rules, deterministically. Run from the project root: python tools/check_doc.py

- every file in sections/ is Markdown and starts with a level-1 or level-2 heading;
- every section has at least one sentence of text under its heading;
- one sentence per line: no line of text holds two sentences (". " followed by a capital letter);
- every relative Markdown link points at a file that exists.

Exit code 0 when everything holds; otherwise one problem per line on stdout and exit code 1.
"""

import re
import sys
from pathlib import Path

LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
TWO_SENTENCES = re.compile(r"[.!?] +[A-Z]")


class DocumentCheck:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.problems: list[str] = []

    def run(self) -> list[str]:
        sections = self.root / "sections"
        files = sorted(sections.glob("*")) if sections.is_dir() else []
        for path in files:
            self.check_section(path)
        return self.problems

    def check_section(self, path: Path) -> None:
        name = path.relative_to(self.root)
        if path.suffix != ".md":
            self.problems.append(f"{name}: sections must be Markdown files")
            return
        lines = [line.rstrip() for line in path.read_text().splitlines()]
        content = [line for line in lines if line.strip()]
        if not content or not re.match(r"^#{1,2} \S", content[0]):
            self.problems.append(f"{name}: must start with a '# ' or '## ' heading")
        text = [line for line in content[1:] if not line.startswith("#")]
        if not text:
            self.problems.append(f"{name}: has no text under its heading")
        for number, line in enumerate(lines, 1):
            if line.startswith(("#", "```", "    ", "|")):
                continue
            if TWO_SENTENCES.search(line):
                self.problems.append(f"{name}:{number}: one sentence per line")
            for target in LINK.findall(line):
                if "://" not in target and not (path.parent / target).exists():
                    self.problems.append(f"{name}:{number}: link to missing file {target}")


if __name__ == "__main__":
    problems = DocumentCheck(Path.cwd()).run()
    print("\n".join(problems) or "document checks passed")
    sys.exit(1 if problems else 0)
