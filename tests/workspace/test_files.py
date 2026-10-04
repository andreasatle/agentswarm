from pathlib import Path

import pytest

from agentswarm.workspace.files import ConfinedFiles, UnsafePath


class TestConfinedFiles:
    def test_reads_and_writes_inside_the_root(self, tmp_path: Path) -> None:
        files = ConfinedFiles(tmp_path)
        files.write("docs/note.md", "note\n")
        assert files.read("./docs/note.md") == "note\n"
        files.delete("docs/note.md")
        assert not (tmp_path / "docs/note.md").exists()

    @pytest.mark.parametrize("path", ["/etc/passwd", "../outside", "a/../../outside", "", ".", ".git/config"])
    def test_refuses_paths_that_escape_or_touch_git(self, tmp_path: Path, path: str) -> None:
        with pytest.raises(UnsafePath):
            ConfinedFiles(tmp_path).relative(path)

    def test_refuses_git_metadata_anywhere(self, tmp_path: Path) -> None:
        with pytest.raises(UnsafePath):
            ConfinedFiles(tmp_path).write("vendor/.git/hooks/pre-commit", "#!/bin/sh\n")

    def test_refuses_a_symlink_that_leads_outside(self, tmp_path: Path) -> None:
        root, secret = tmp_path / "root", tmp_path / "secret.txt"
        root.mkdir()
        secret.write_text("secret")
        (root / "link.txt").symlink_to(secret)
        (root / "scratch").symlink_to(tmp_path)
        files = ConfinedFiles(root)
        with pytest.raises(UnsafePath):
            files.read("link.txt")
        with pytest.raises(UnsafePath):
            files.write("scratch/planted.txt", "x")
        assert not (tmp_path / "planted.txt").exists()

    def test_follows_a_symlink_that_stays_inside(self, tmp_path: Path) -> None:
        (tmp_path / "real.txt").write_text("real")
        (tmp_path / "alias.txt").symlink_to(tmp_path / "real.txt")
        assert ConfinedFiles(tmp_path).read("alias.txt") == "real"

    def test_reads_only_up_to_the_limit(self, tmp_path: Path) -> None:
        (tmp_path / "big.txt").write_text("x" * 10_000)
        assert ConfinedFiles(tmp_path).read("big.txt", limit=10) == "x" * 10
