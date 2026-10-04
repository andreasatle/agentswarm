from pathlib import Path

import pytest

from agentswarm.core.config import LevelLadder
from agentswarm.core.domain import ArtifactId, Level
from agentswarm.workspace.git import Git, Repository
from tests.support.samples import Samples

LADDER = LevelLadder(levels=(Level("raw"), Level("experimental"), Level("candidate")))


class Repo:
    """A fresh repository in a temporary directory."""

    def __init__(self, tmp_path: Path) -> None:
        self.repository = Repository.create(tmp_path / "repo", tmp_path / "worktrees", LADDER, Git())


class TestRepository:
    def test_create_makes_one_branch_per_level_above_the_lowest(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        assert repo.has_branch("experimental")
        assert repo.has_branch("candidate")
        assert not repo.has_branch("raw")
        assert repo.tip("experimental") == repo.tip("candidate")

    def test_open_or_create_reopens_an_existing_repository(self, tmp_path: Path) -> None:
        first = Repo(tmp_path).repository
        again = Repository.open_or_create(first.path, tmp_path / "worktrees", LADDER, Git())
        assert again.tip("experimental") == first.tip("experimental")

    def test_artifact_branch_forks_from_the_base_level(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        artifact = Samples.artifact()
        repo.ensure_branch(artifact)
        assert repo.tip(artifact.branch) == repo.tip("experimental")

    def test_diff_stat_shows_the_artifact_change(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        artifact = Samples.artifact()
        assert repo.diff_stat(artifact) == ""
        wt = repo.checkout_artifact(artifact)
        wt.write("hello.txt", "hi\n")
        wt.commit("hello")
        assert "hello.txt" in repo.diff_stat(artifact)


class TestMergedChange:
    def test_shows_only_the_artifacts_own_change_when_branches_overlap(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        first, second = Samples.artifact(ArtifactId("a-0001")), Samples.artifact(ArtifactId("a-0002"))
        for artifact, name in ((first, "a.txt"), (second, "b.txt")):  # both fork before either merges
            wt = repo.checkout_artifact(artifact)
            wt.write(name, f"{name}\n")
            wt.commit(name)
            wt.remove()
        level = repo.checkout_level(Level("experimental"))
        assert level.merge(first.branch, "merge a") and level.merge(second.branch, "merge b")
        diff = repo.diff(*Repository.merged_change(level.head()))
        assert "b.txt" in diff
        assert "a.txt" not in diff  # landed first; not deleted by the second artifact


class TestWorktree:
    def test_commit_moves_the_branch(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        artifact = Samples.artifact()
        wt = repo.checkout_artifact(artifact)
        wt.write("docs/note.md", "note\n")
        sha = wt.commit("add note")
        assert repo.tip(artifact.branch) == sha
        assert repo.files_at(artifact.branch) == ["docs/note.md"]

    def test_worktrees_are_separate_directories(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        a = repo.checkout_artifact(Samples.artifact(Samples.A))
        b = repo.checkout_artifact(Samples.artifact(Samples.B))
        a.write("a.txt", "a\n")
        assert not (b.path / "a.txt").exists()

    def test_checkout_replaces_a_stale_worktree(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        artifact = Samples.artifact()
        stale = repo.checkout_artifact(artifact)
        stale.write("uncommitted.txt", "lost\n")
        fresh = repo.checkout_artifact(artifact)
        assert fresh.path == stale.path
        assert not (fresh.path / "uncommitted.txt").exists()

    def test_remove_deletes_the_directory(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        wt = repo.checkout_artifact(Samples.artifact())
        wt.remove()
        assert not wt.path.exists()

    def test_merge_and_conflict(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        a, b = Samples.artifact(Samples.A), Samples.artifact(Samples.B)
        for artifact, text in ((a, "from a\n"), (b, "from b\n")):
            wt = repo.checkout_artifact(artifact)
            wt.write("shared.txt", text)
            wt.commit(f"write from {artifact.id}")
            wt.remove()

        level = repo.checkout_level(Level("experimental"))
        assert level.merge(repo.tip(a.branch), "merge a")
        assert level.read("shared.txt") == "from a\n"
        before = level.head()
        assert not level.merge(repo.tip(b.branch), "merge b")
        assert level.head() == before
        assert level.read("shared.txt") == "from a\n"


class TestGitTrust:
    """A checkout's contents must not make host Git run anything (CODEX_AUDIT.md F01)."""

    HOOK = "#!/bin/sh\ntouch {sentinel}\n"

    def test_a_forged_git_pointer_in_the_worktree_is_ignored(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        artifact = Samples.artifact()
        wt = repo.checkout_artifact(artifact)
        evil = Repository.create(wt.path / "evil", tmp_path / "evil-worktrees", LADDER, Git())
        hook = evil.path / ".git" / "hooks" / "pre-commit"
        sentinel = tmp_path / "hook-ran"
        hook.write_text(self.HOOK.format(sentinel=sentinel))
        hook.chmod(0o755)
        (wt.path / ".git").write_text(f"gitdir: {evil.path / '.git'}\n")
        wt.write("hello.txt", "hi\n")
        sha = wt.commit("hello")
        assert not sentinel.exists()
        assert repo.tip(artifact.branch) == sha

    def test_hooks_in_the_repository_do_not_run(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path).repository
        sentinel = tmp_path / "hook-ran"
        hook = repo.path / ".git" / "hooks" / "pre-commit"
        hook.write_text(self.HOOK.format(sentinel=sentinel))
        hook.chmod(0o755)
        wt = repo.checkout_artifact(Samples.artifact())
        wt.write("hello.txt", "hi\n")
        wt.commit("hello")
        assert not sentinel.exists()

    def test_git_does_not_see_the_callers_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOME_API_KEY", "secret")
        assert "SOME_API_KEY" not in Git()._env  # pyright: ignore[reportPrivateUsage]
