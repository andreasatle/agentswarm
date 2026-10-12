from pathlib import Path

from agentswarm.evaluation.html5 import Html5Import, Html5TestFile, SpecText

README = """Tree Construction Tests

Then there must be a line that says "\\#document", which must be followed by a dump of the tree.

For example:

    #data
    <p>One<p>Two
"""

TESTS = (
    "#data\n<p>One\n#errors\n(1,3): expected-doctype\n#document\n"
    '| <html>\n|   <head>\n|   <body>\n|     <p>\n|       "One"\n'
    "\n"
    "#data\n<pre>a\n\nb\r</pre>\n#errors\n#document\n"
    '| <html>\n|   <head>\n|   <body>\n|     <pre>\n|       "a\n\nb\n"\n'
    "\n"
    '#data\n<td>x\n#errors\n#document-fragment\ntr\n#script-on\n#document\n| <td>\n|   "x"\n'
)


class Source:
    def __init__(self, root: Path) -> None:
        root.mkdir()
        (root / "README.md").write_text(README)
        (root / "tests2.dat").write_bytes(TESTS.encode())
        (root / "tests10.dat").write_bytes(TESTS.encode())
        (root / "adoption01.dat").write_bytes("".join([TESTS, "\n\n", TESTS]).encode())
        (root / "scripted_ark.dat").write_bytes(TESTS.encode())
        self.root = root


class TestHtml5TestFile:
    def test_sections_fragments_and_text_that_spans_blank_lines(self, tmp_path: Path) -> None:
        cases = Html5TestFile(Source(tmp_path / "t").root / "tests2.dat").cases()
        assert [c.name for c in cases] == ["tests2:1", "tests2:2", "tests2:3"]
        assert cases[0].html == "<p>One" and cases[0].context is None and not cases[0].scripting
        assert cases[1].html == "<pre>a\n\nb\r</pre>"  # a carriage return kept, as in the file
        assert cases[1].document.endswith('|       "a\n\nb\n"')  # a blank line inside a text node
        assert (cases[2].context, cases[2].scripting, cases[2].document) == ("tr", True, '| <td>\n|   "x"')


class TestHtml5Import:
    def test_files_in_order_scripted_left_out_and_the_dump_format(self, tmp_path: Path) -> None:
        summary = Html5Import(Source(tmp_path / "t").root, tmp_path / "out").write()
        assert (summary.groups, summary.cases, summary.visible, summary.held_out) == (3, 12, 11, 1)
        acceptance = summary.seed / "tests" / "acceptance"
        assert sorted(p.name for p in acceptance.glob("test_*.py")) == [
            "test_html5_01_tests2.py",  # numbered files in number order, then the rest by name
            "test_html5_02_tests10.py",
            "test_html5_03_adoption01.py",
        ]
        dump = (summary.seed / "docs" / "tree-dump.md").read_text()
        assert "a dump of the tree" in dump and "<p>One<p>Two" not in dump  # our example, not a test
        for path in acceptance.glob("*.py"):
            compile(path.read_text(), str(path), "exec")


class TestSpecText:
    def test_headings_paragraphs_and_what_is_left_out(self) -> None:
        html = (
            "<script>x()</script><h4 id=tokenization>Tokenization</h4><p>Consume the next character.<p>Then"
            "<div class=example><pre>&lt;b>1&lt;p>2&lt;/b></pre></div><pre>state ::= data</pre>"
            "<h4 id=an-introduction-to-error-handling-and-strange-cases-in-the-parser>Strange cases</h4>"
            "<p>Consider <code>&lt;table>&lt;b></code><ul class=domTree><li>html</ul>"
            "<h5 id=inner>Still left out</h5><p>Inner."
            "<h4 id=the-end>The end</h4><p>Done."
        )
        text = SpecText.convert(html)
        assert "#### Tokenization\n\nConsume the next character.\n\nThen" in text
        assert "```\nstate ::= data\n```" in text and "<b>1" not in text  # examples go, other <pre> stays
        assert "Strange" not in text and "<table>" not in text and "Inner" not in text and "x()" not in text
        assert "#### The end\n\nDone." in text
