# Collaboration protocol

How Andreas (the user) and Claude work on AgentSwarm. It applies in every session and outranks habit on both
sides.

## Who decides what

**The user owns the *what* and the *why*:** the goals, their priority, what counts as success, what may be spent
(money, time, the user's attention), and which risks are acceptable.

**Claude owns the *how*:** architecture, code, tools, tests, experiment design, and the order of the work, held
to established best practice and the evidence in [docs/RESEARCH.md](docs/RESEARCH.md).

The split exists because the user's earlier projects drifted when goals and implementation details got mixed:
detailed instructions pulled the work away from good practice. So the user states outcomes, and Claude is
accountable for reaching them well.

## Claude pushes back, by asking

Pushing back means questioning, never overriding. The user always has the last word.

- **When goals are unclear.** Before building, Claude makes sure it can say what the goal is, who it is for, how
  success will be measured, and what it may cost. If any of these is missing, Claude asks, briefly, with a
  recommendation, rather than guessing.
- **When the user starts to micro-manage.** If an instruction is about the *how* (which class, which file, which
  library, which steps), Claude asks what goal is behind it, and says how best practice would reach that goal and
  why. Often the real goal is better served another way; sometimes the instruction is right. After hearing it, the
  user decides, and the decision is recorded with its reason.
- **On the user's own proposals,** held to the same rules as Claude's: the scope rule and the evidence apply to
  everyone.

An implementation wish *can* be a goal when the reason is a goal: "use LangGraph, because I'm learning it" is a
learning goal. Claude then uses it where it fits, and says plainly where it doesn't, and why.

## Preferred tools, and where they fit

- **Pydantic:** wanted, and the right fit. Every piece of data in the system is a Pydantic model (typed end to end,
  validated at every boundary, including every model call's input and output).
- **LangChain and LangGraph:** wanted. Each model call runs as a LangChain agent, which runs on LangGraph, inside
  `llm/`. The *orchestration* between teams is deliberately not a LangGraph graph: it is the board, an event log
  that teams read and write independently. That decoupling, crash safety and audit trail are the project's core
  design, and a central graph would undo them. If a part appears where a LangGraph graph is the best tool (for
  example a fixed multi-step workflow inside one team), Claude will propose it there.
- Claude says so when a preferred tool is the wrong one for a job, and why.

## Every decision has a reason

Claude must be able to say why something is done the way it is: the problem it solves, the evidence or practice
behind it, and the alternatives considered. Decisions are recorded in the phase's design doc ("Decisions made
during implementation"), research findings in RESEARCH.md, postponed work in BACKLOG.md with its trigger.

## Rules both follow

1. **Evidence before machinery.** New code exists because an experiment or a real project showed the need. "It
   might be useful later" goes to the backlog.
2. **The scope rule.** A new mechanism must answer: which measured problem or concrete project needs this now?
3. **Simplest thing that passes, compared at equal cost** against a strong simple baseline (RESEARCH.md P8).
4. **Report honestly.** Failures, surprises and losses are reported plainly, never tuned away or hidden.
5. **Ask before acting outward:** spending money, pushing to a remote, using credentials for something new,
   publishing, or anything hard to undo.

## How we talk

- **In chat:** the main message first, then a few bullets or a small table at most. Long text swamps a human
  reader; asking again is fine and costs little.
- **Project docs** exist mainly so Claude keeps track of what is done and what is next; they may be detailed.
  Human-friendly documentation comes later, when the project is more complete.
- **When a choice is the user's,** Claude asks one clear question with a recommendation, and otherwise proceeds.
