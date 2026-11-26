# The studio: a web page for running the framework (goal 13)

Written 2026-11-01. The user wants to run document runs without Claude and without the command line's text: input, approvals and everything else in the workflow, "in a way that is pleasing". Just for the user, on this Mac, no login ("at least for now"). The read-only board page (`agentswarm monitor`) becomes part of it, one app instead of two servers.

## What the user does

`uv run agentswarm studio` and open http://127.0.0.1:8770/. `uv run agentswarm monitor ...` opens the same app on its board view, for software projects.

- **Runs:** every run with its stage, its spend against its budget, and whether it waits for the user, works, or stopped on an error.
- **New run:** topic, reader, budget, length, source, a name, links and the user's own files; the expected cost step by step as the fields change.
- **A run:** what it waits for, as a card with its choices (approve, stop, raise the budget to ..., retry, accept, drop); the reading list with what to fetch by hand, and a drop zone for those files; what happened so far; the audit's numbers; the document to read; and the board while it writes. Nothing in the studio touches the homepage (the user, 2026-11-02: no coupling to it).

## How it is built

- **Another front end over the same pipeline.** `studio/` holds a FastAPI app that calls `DocumentPipeline` (pipeline/graph.py) exactly as the commands do; the commands keep working, and a run started in one can be answered in the other.
- **Server-rendered pages** (Jinja2 templates) and a few lines of JavaScript that refresh a part of the page while a step runs: no build step, nothing to install in a browser. One stylesheet for the studio and the board page.
- **Long steps in the background:** a step (finding sources, writing, reviewing) takes minutes, so the app hands it to a worker thread and the page shows it working; an error is kept and shown with a retry. Checks that need no model (is the run waiting, is the sources folder empty) are answered at once.
- **The board** (monitor/): its data stays `MonitorState`; its API moves from Python's own HTTP server into the app under the same paths, and it watches every document run's board as well as the projects `monitor` names. It only reads.
- **Local only:** bound to 127.0.0.1, no login. Sharing it or reaching it from a phone would need authentication first (BACKLOG.md when wanted).
- **Uploaded files keep their names** (only the folder part is dropped); the program never makes up a name for a file it didn't write.

## Decisions made during implementation (2026-11-01)

- **No htmx, no JavaScript framework:** about a hundred lines of plain JavaScript do the three live parts (a panel refreshed while a step runs, the cost plan as the form changes, the drop zone), so nothing is fetched from the internet and there is no build step.
- **The pipeline gained `view(name)`** (a typed `RunView`: the run, its cost, the question with the reading list as the folder holds it now, the error) and **`check(name, answer)`** (why an answer can't be taken, without running anything); the commands' text report is built from the same view.
- **`monitor/server.py` became `monitor/boards.py`:** the same reads, without Python's own HTTP server; the board page links the studio's stylesheet and its runs. Port 8770 for both commands now (the board was on 8765).
- **One step at a time per run** (`ThreadWorker`); a run answered from the command line while the studio shows it is seen on the next refresh, but the studio can't tell it is busy.
- **Checked in a browser** (2026-11-01): the runs list, a run waiting for publishing (the real sleep run, read only), the document, the board, the new-run form with its live plan, light and dark, phone width, and a stand-in run whose page followed its 6-second step and switched to the question by itself.

## The run's history (2026-11-25)

The user (2026-11-24): "go back and forth ... add a reference and rerun from there ... easy to navigate"; folders were one possible look, not a requirement, and branching chats are hard to follow.

- **A History card on the run's page:** every pause on the way to where the run is now, in one straight line, each with what was decided there (the questions, the sources, the citations' share) and what the user answered; **Read** shows the document as it was then, **Continue from here** goes back to that pause. The other versions are one line each below it, by where they ended, never drawn as a tree.
- **Continuing starts a new version and keeps the old:** LangGraph forks the run's checkpoints (the pause is asked again as a new checkpoint after the old one), and the run's files (`sources/`, `project/`, `board/`, the page) are put back as that pause left them. Every version's moments stay readable, and any pause of any version can be continued from.
- **Files are copied at every pause** into `history/<checkpoint>/` in the run's folder (`pipeline/history.py`, `RunFiles`): about 17 MB a moment for the memory run, mostly its sources; plain copies, since SQLite files and reading lists are rewritten in place and a hard link would change with them. Before the run leaves a version, its current files are saved too. The user's own files in the run's folder (notes, uploads) are never touched.
- **Everything after the pause is redone**, at its cost: the first step toward goal 16, not yet redoing only what the change touches. The spend shown stays the run's total over every version.
- **Runs from before this have no files for their earlier moments:** shown, without buttons, and said so.

## The title, set at the publishing pause (2026-11-26)

The user: "Shouldn't we be able to change the title before we publish?" The writers' title comes from the brief, and a revision round reaches only the sections, so nothing before publishing could change it. The publishing pause now shows the document's title in a field beside "Publish as it is"; a changed title is the user's answer (`Answer.title`, kept as `DocumentRun.title`), put on the document's first line and the page when it is published, and the run's notes say "title set by you". The writers' text is not touched, and no model is called.
