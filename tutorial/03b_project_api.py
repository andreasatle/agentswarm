"""Step 3b: the same first run as step 3, from Python, at the project level.

    uv run tutorial/03b_project_api.py

Steps 1 and 2 showed the board's basic operations. A real project never calls them by hand:
`Project.open` builds everything from the YAML, and `run_until_idle` runs every team, gate and controller
until nothing more happens. Every value is built on its own line and named, so you can inspect any of them.
"""

import asyncio
import os
import shutil
from pathlib import Path

from agentswarm.board.blackboard import BoardViews
from agentswarm.core.config import ProjectConfig
from agentswarm.core.domain import Artifact
from agentswarm.project import Project


def print_history(views: BoardViews, artifact: Artifact) -> None:
    history = views.history(artifact.id)
    kinds = [type(event).__name__ for event in history]
    print(artifact.id, "history:", kinds)  # created, claimed, commit, released, claimed, promoted, released


# Where things are: the example config in this repository, and this step's own project folder.
here = Path(__file__).resolve()
repository = here.parents[1]
config_path = repository / "examples" / "quickstart" / "project.yaml"
home = Path.home()
default_runs = home / "agentswarm-runs"
runs_setting = os.environ.get("AGENTSWARM_RUNS", str(default_runs))
runs = Path(runs_setting)
root = runs / "tutorial" / "step03b-project"
shutil.rmtree(root, ignore_errors=True)  # start this step's own folder afresh

# Load and check the configuration, then open the project: the log and views, git, sandbox, teams, gates.
config = ProjectConfig.load(config_path)
project = Project.open(config, root)
try:
    # One call builds the Artifact, picks its id and appends ArtifactCreated.
    first = project.create_artifact("Write a note")
    second = project.create_artifact("Write another note")
    print("created:", first.id, second.id)

    # Run until a round takes no action. Teams run asynchronously, hence asyncio.run.
    run = project.run_until_idle()
    rounds = asyncio.run(run)
    print("rounds that took action:", rounds)

    # Read the state: the views, as in step 1.
    views = project.views()
    first_now = views.artifact(first.id)
    second_now = views.artifact(second.id)
    print(first.id, "is at", first_now.level)  # experimental
    print(second.id, "is at", second_now.level)  # experimental

    print_history(views, first)
    print_history(views, second)

    # The content is in git: the level branch now holds both notes.
    repository_files = project.repository.files_at("experimental")
    print("files on experimental:", repository_files)
finally:
    project.close()  # releases the project's lock and closes its databases
