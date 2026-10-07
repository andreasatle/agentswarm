"""Git as the artifact store (proposal §9). These are the only classes that run git."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict

from agentswarm.core.config import LevelLadder
from agentswarm.core.domain import Artifact, Level
from agentswarm.workspace.files import ConfinedFiles


class GitError(Exception):
    def __init__(self, args: tuple[str, ...], returncode: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {stderr.strip()}")
        self.returncode = returncode
        self.stderr = stderr


class GitIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = "agentswarm"
    email: str = "agentswarm@localhost"


class Git:
    """Runs git commands with a fixed identity and no user or system configuration.

    Ignoring the user's config keeps behaviour identical across machines (no signing or aliases). Hooks and
    the filesystem monitor are switched off on every call, and git gets a minimal environment, so neither a
    repository's own files nor the caller's credentials can make it run anything (CODEX_AUDIT.md F01).
    """

    SAFETY = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")

    def __init__(self, identity: GitIdentity | None = None) -> None:
        identity = identity or GitIdentity()
        self._env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(Path.home()),
            "LANG": "C.UTF-8",
            "GIT_AUTHOR_NAME": identity.name,
            "GIT_AUTHOR_EMAIL": identity.email,
            "GIT_COMMITTER_NAME": identity.name,
            "GIT_COMMITTER_EMAIL": identity.email,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }

    def run(self, cwd: Path, *args: str) -> str:
        """Run git and return stdout; raise GitError on a non-zero exit."""
        result = self.attempt(cwd, *args)
        if result.returncode != 0:
            raise GitError(args, result.returncode, result.stderr)
        return result.stdout.strip()

    def archive(self, cwd: Path, commit: str) -> bytes:
        """The files of `commit` as a tar archive, without any git metadata."""
        result = subprocess.run(
            ["git", *self.SAFETY, "archive", "--format=tar", commit],
            cwd=cwd,
            env=self._env,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise GitError(("archive", commit), result.returncode, result.stderr.decode(errors="replace"))
        return result.stdout

    def attempt(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Run git and return the result whatever the exit code, for commands that may legitimately fail."""
        return subprocess.run(
            ["git", *self.SAFETY, *args], cwd=cwd, env=self._env, capture_output=True, text=True, check=False
        )


class Worktree:
    """A checkout of one branch in its own directory, so teams never share a working directory.

    Git runs with the Git directory recorded when the worktree was created, never the one the worktree's
    `.git` file names at the time: that file is inside the checkout, where it could be replaced.
    """

    def __init__(self, path: Path, ref: str, repository: "Repository", git_dir: str) -> None:
        self.path = path
        self.ref = ref  # the branch checked out, or the commit for a detached worktree
        self._repository = repository
        self._git = repository.git
        self._location = (f"--git-dir={git_dir}", f"--work-tree={path}")
        self._files = ConfinedFiles(path)

    def write(self, relative: str, content: str) -> None:
        self._files.write(relative, content)

    def read(self, relative: str, limit: int | None = None) -> str:
        return self._files.read(relative, limit)

    def delete(self, relative: str) -> None:
        self._files.delete(relative)

    def relative(self, path: str) -> str:
        """`path` normalised; raises UnsafePath if it could leave the worktree or touch Git metadata."""
        return self._files.relative(path)

    def _run(self, *args: str) -> str:
        return self._git.run(self.path, *self._location, *args)

    def _attempt(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._git.attempt(self.path, *self._location, *args)

    def files(self) -> list[str]:
        """Tracked files plus new files not ignored by .gitignore, sorted."""
        out = self._run("ls-files", "--cached", "--others", "--exclude-standard")
        return sorted({line for line in out.splitlines() if (self.path / line).is_file()})

    def diff_against(self, ref: str, context_lines: int = 3) -> str:
        """What HEAD changes relative to where it forked from `ref` (git diff ref...HEAD)."""
        return self._run("diff", f"--unified={context_lines}", f"{ref}...HEAD")

    def show(self, commit: str, path: str) -> str:
        """A file's text as it is in `commit` (any commit of the repository)."""
        return self._run("show", f"{commit}:{path}")

    def diff_between(self, base: str, head: str) -> str:
        return self._run("diff", base, head)

    def merge_base(self, ref: str) -> str:
        """The commit where HEAD forked from `ref`: what `diff_against(ref)` measures against."""
        return self._run("merge-base", ref, "HEAD")

    def has_changes(self) -> bool:
        return self._run("status", "--porcelain") != ""

    def commit(self, message: str) -> str:
        """Commit every change in the worktree and return the new commit's sha."""
        self._run("add", "--all")
        self._run("commit", "--quiet", "--allow-empty", "-m", message)
        return self.head()

    def head(self) -> str:
        return self._run("rev-parse", "HEAD")

    def merge(self, commit: str, message: str) -> bool:
        """Merge `commit` with a merge commit. On conflict, abort and return False."""
        result = self._attempt("merge", "--no-ff", "--quiet", "-m", message, commit)
        if result.returncode != 0:
            self._attempt("merge", "--abort")
            return False
        return True

    def reset_hard(self, commit: str) -> None:
        self._run("reset", "--quiet", "--hard", commit)

    def restore(self, folder: str) -> list[str]:
        """Put `folder` back as it is in the last commit: changes undone, new files there deleted.

        Returns the paths that had changed, so a caller can note it.
        """
        status = self._run("status", "--porcelain", "--untracked-files=all", "--", folder)
        changed = sorted(line.split(maxsplit=1)[1] for line in status.splitlines() if line.strip())
        if not changed:
            return []
        tracked = self._run("ls-tree", "-r", "--name-only", "HEAD", "--", folder)
        if tracked:
            self._run("checkout", "HEAD", "--", folder)
        self._run("clean", "--quiet", "--force", "-d", "--", folder)
        return changed

    def discard_changes(self) -> None:
        """Back to the last commit: changed files restored, new files deleted (ignored ones kept)."""
        self._run("reset", "--quiet", "--hard", "HEAD")
        self._run("clean", "--quiet", "--force", "-d")

    def remove(self) -> None:
        self._repository.remove_worktree(self)


class Repository:
    """A project's repository: one long-lived branch per level above the lowest, plus artifact branches.

    The main checkout stays on a detached HEAD so that every level branch is free to be checked out in a
    worktree (git refuses to check out one branch in two places).
    """

    def __init__(self, path: Path, worktrees: Path, ladder: LevelLadder, git: Git) -> None:
        self.path = path
        self.git = git
        self._worktrees = worktrees
        self._ladder = ladder

    @classmethod
    def create(
        cls, path: Path, worktrees: Path, ladder: LevelLadder, git: Git, seed: Path | None = None
    ) -> Self:
        """Initialise a repository whose level branches start at one commit: empty, or the seed's files."""
        path.mkdir(parents=True, exist_ok=True)
        git.run(path, "init", "--quiet", "--initial-branch", ladder.base)
        if seed is not None:
            shutil.copytree(seed, path, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
            git.run(path, "add", "--all")
        git.run(path, "commit", "--quiet", "--allow-empty", "-m", "Initial commit")
        for level in ladder.branch_levels[1:]:
            git.run(path, "branch", level)
        git.run(path, "checkout", "--quiet", "--detach")
        return cls(path, worktrees, ladder, git)

    @classmethod
    def open_or_create(
        cls, path: Path, worktrees: Path, ladder: LevelLadder, git: Git, seed: Path | None = None
    ) -> Self:
        if (path / ".git").exists():
            return cls(path, worktrees, ladder, git)
        return cls.create(path, worktrees, ladder, git, seed)

    def has_branch(self, branch: str) -> bool:
        return (
            self.git.attempt(self.path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode
            == 0
        )

    def tip(self, branch: str) -> str:
        return self.git.run(self.path, "rev-parse", f"refs/heads/{branch}")

    def first_parent_commits(self, since: str, until: str) -> list[str]:
        """The commits on `until`'s first-parent line that `since` doesn't have, oldest first."""
        out = self.git.run(self.path, "rev-list", "--first-parent", "--reverse", f"{since}..{until}")
        return out.splitlines() if out else []

    def fast_forward(self, branch: str, commit: str, expected: str) -> None:
        """Move `branch` to `commit`, only if it still points to `expected` and `commit` descends from it."""
        if self.git.attempt(self.path, "merge-base", "--is-ancestor", expected, commit).returncode != 0:
            raise GitError(("fast-forward", branch, commit), 1, f"{commit} does not descend from {expected}")
        self.git.run(self.path, "update-ref", f"refs/heads/{branch}", commit, expected)

    def contains(self, commit: str, ancestor: str) -> bool:
        """Whether `ancestor` is `commit` or one of its ancestors."""
        return self.git.attempt(self.path, "merge-base", "--is-ancestor", ancestor, commit).returncode == 0

    def ensure_branch(self, artifact: Artifact) -> None:
        """Create the artifact's branch from the base level if it doesn't exist yet."""
        if not self.has_branch(artifact.branch):
            self.git.run(self.path, "branch", artifact.branch, self._ladder.base)

    def diff_stat(self, artifact: Artifact, merge_commit: str | None = None) -> str:
        """The artifact's own change: since it forked from the base level, or within its merge commit."""
        if merge_commit is not None:
            return self.git.run(self.path, "diff", "--stat", *self.merged_change(merge_commit))
        if not self.has_branch(artifact.branch):
            return ""
        return self.git.run(self.path, "diff", "--stat", f"{self._ladder.base}...{artifact.branch}")

    def diff(self, base: str, head: str) -> str:
        return self.git.run(self.path, "diff", base, head)

    @staticmethod
    def merged_change(merge_commit: str) -> tuple[str, str]:
        """The (base, head) of the change a merge commit brought into its level: first parent to the merge.

        Not first parent to second: when other work landed after the branch forked, the branch lacks it,
        and comparing the parents would show that work as deleted by this change (CODEX_AUDIT.md F05).
        """
        return f"{merge_commit}^1", merge_commit

    def checkout_merged(self, merge_commit: str, name: str) -> Worktree:
        """A read-only look at an artifact after it joined a shared level: detached at its merge commit."""
        return self.checkout_detached(merge_commit, name)

    def checkout(self, branch: str, name: str) -> Worktree:
        """A fresh worktree of `branch` at worktrees/<name>. Commits in it move the branch."""
        path = self._fresh_worktree_path(name)
        self.git.run(self.path, "worktree", "add", "--quiet", str(path), branch)
        return Worktree(path, branch, self, self._git_dir(path))

    def checkout_detached(self, commit: str, name: str) -> Worktree:
        """A fresh worktree at `commit` on a detached HEAD: nothing done in it moves any branch."""
        path = self._fresh_worktree_path(name)
        self.git.run(self.path, "worktree", "add", "--quiet", "--detach", str(path), commit)
        return Worktree(path, commit, self, self._git_dir(path))

    def _git_dir(self, path: Path) -> str:
        """The new worktree's Git directory, read while nothing else has touched the checkout."""
        return self.git.run(path, "rev-parse", "--absolute-git-dir")

    def _fresh_worktree_path(self, name: str) -> Path:
        """worktrees/<name>, with any stale worktree left there by a crash removed."""
        path = self._worktrees / name
        if path.exists():
            self.git.attempt(self.path, "worktree", "remove", "--force", str(path))
            shutil.rmtree(path, ignore_errors=True)
        self.git.run(self.path, "worktree", "prune")
        self._worktrees.mkdir(parents=True, exist_ok=True)
        return path

    def checkout_artifact(self, artifact: Artifact) -> Worktree:
        self.ensure_branch(artifact)
        return self.checkout(artifact.branch, artifact.id)

    def checkout_level(self, level: Level) -> Worktree:
        return self.checkout(level, f"_level-{level}")

    def remove_worktree(self, worktree: Worktree) -> None:
        self.git.run(self.path, "worktree", "remove", "--force", str(worktree.path))

    def files_at(self, branch: str) -> list[str]:
        out = self.git.run(self.path, "ls-tree", "-r", "--name-only", branch)
        return out.splitlines() if out else []
