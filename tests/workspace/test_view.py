from pathlib import Path

from agentswarm.workspace.view import LevelFolders
from tests.support.harness import Harness
from tests.support.samples import Samples


class TestLevelFolders:
    def test_each_levels_files_as_plain_folders_replacing_the_last_snapshot(self, tmp_path: Path) -> None:
        h = Harness(tmp_path)
        level = h.repository.checkout_level(Samples.EXPERIMENTAL)
        level.write("greeter.py", "def hello(): ...\n")
        level.commit("a merge")
        level.remove()
        folders = LevelFolders(h.repository.path, tmp_path, Harness.LADDER)
        (tmp_path / "levels" / "experimental").mkdir(parents=True)
        (tmp_path / "levels" / "experimental" / "stale.txt").write_text("old")
        snapshots = folders.write()
        experimental = next(s for s in snapshots if s.level == "experimental")
        assert (experimental.folder / "greeter.py").read_text() == "def hello(): ...\n"
        assert (
            not (experimental.folder / "stale.txt").exists() and not (experimental.folder / ".git").exists()
        )
        assert experimental.files == 1 and "experimental" in LevelFolders.render(snapshots)
