from pathlib import Path

from agentswarm.workspace.repomap import RepoMap
from tests.support.harness import Harness


class TestRepoMap:
    def test_files_with_their_functions_classes_and_lines(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        worktree = h.repository.checkout_artifact(h.create())
        worktree.write("pkg/m.py", "def a():\n    return 1\n\n\nclass B:\n    def c(self):\n        pass\n")
        worktree.write("pkg/broken.py", "def (:\n")
        worktree.write("tests/acceptance/test_x.py", "def test_x():\n    pass\n")
        worktree.write("notes.md", "one\ntwo\n")
        text = RepoMap(worktree).render()
        assert "pkg/m.py (7 lines)\n  def a  lines 1-2\n  class B  lines 5-7\n    def c  lines 6-7" in text
        assert "pkg/broken.py (1 lines)\n  (doesn't parse)" in text
        assert (
            "tests/acceptance/test_x.py (2 lines)" in text and "def test_x" not in text
        )  # listed, not mapped
        assert "notes.md (2 lines)" in text
