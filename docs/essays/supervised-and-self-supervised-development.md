# Supervised and self-supervised software development

October 11, 2026 · Andreas Atle

When AI agents write software, tests play the role that labels play in machine learning: they tell the agent whether it got the answer right. Borrowing the rest of the vocabulary, train and test sets, overfitting, self-supervision, gives a practical way to build software with agents you can trust.

## The analogy at a glance

Nothing is trained here: the models stay as they are. What improves is the program the agents write, and the tests are the signal that steers it.

| Machine learning | Software development with agents |
| --- | --- |
| Model parameters | The program's source code |
| Training | Agents editing the code until tests pass |
| Labeled example (input, correct output) | A test: an input and the output it must give |
| Training set | The tests the agents can see and run |
| Test set | Held-out tests no agent ever sees |
| Generalization | The program also passes tests it never saw |
| Overfitting | Code that passes the visible tests without solving the problem |
| Loss | The share of tests that fail |
| Self-supervision | Checks the program supplies itself: round trips, properties, comparisons |

## Supervised development: labeled data, split in two

Supervised development needs examples with known answers: an input, and the exact output a correct program gives for it. Each example becomes a test.

Where such data comes from:

- **A specification with worked examples.** The CommonMark spec for Markdown shows 652 inputs with their exact HTML.
- **An official conformance suite.** The toml-test suite lists valid TOML files with their expected values, and invalid files a parser must reject.
- **A reference implementation.** Run an existing, trusted program on many inputs and record its outputs.
- **Past bug fixes.** Each fixed bug is an input whose correct output is now known; benchmarks like SWE-bench are built this way.
- **Acceptance tests written from the task,** before any code exists, by someone other than the builder.

Then split it, exactly as for training a model:

1. **Shuffle out a held-out part before any work starts.** A fixed rule works well and is easy to audit: every fifth example, per section.
2. **Give the agents the rest as runnable tests.** They read them, run them, and change the code until they pass.
3. **Keep the held-out tests out of reach.** Not in the repository, not in any document the agents read, used only to score.
4. **Report both numbers, every time.** The share of visible tests that pass measures progress; the share of held-out tests that pass measures whether that progress is real.

## A worked example: two parsers built by agents

In both projects below, a swarm of AI agents (AgentSwarm) started from an empty stub and worked only against the visible tests; the held-out tests were scored but never shown.

| Project | Labeled data | Visible / held out | Visible tests passing | Held-out tests passing | Model cost |
| --- | --- | --- | --- | --- | --- |
| CommonMark (Markdown to HTML), midway | 652 spec examples | 522 / 130 | 43% | 45% | about $24 |
| CommonMark, after a planned restructuring | same | 522 / 130 | 89% | 89% | about $38 in all |
| TOML 1.0.0 parser | 679 files of the toml-test suite | 555 / 124 | 100% | 100% | $2.44 |

Costs are the model calls recorded since October 8, 2026; CommonMark's first 40% was built before that, at a cost not counted here.

The two percentages track each other closely. That is the point of the split: had the agents fitted their code to the examples they could see, the visible number would have climbed while the held-out number stayed behind. Here, what was learned from the visible tests carried over to tests nobody showed them.

## Overfitting to tests, and keeping the test set honest

Code can pass a test without doing what the test stands for. In AI research this is called specification gaming or reward hacking; in classrooms, teaching to the test. Goodhart's law sums it up: when a measure becomes a target, it stops being a good measure. Agents are known to do it, from special-casing an input to editing the test itself.

What it looks like in code:

```python
def to_html(markdown: str) -> str:
    if markdown == "*foo*\n":  # the visible test's exact input
        return "<p><em>foo</em></p>\n"
    ...
```

Three defences, in layers:

- **Held-out tests detect it.** They can't stop a single special case, but they show overfitting in aggregate: visible numbers rising while held-out numbers lag.
- **Protected tests prevent the crudest form.** The builder can read the acceptance tests but not change or delete them; a gate restores them if it tries.
- **Review catches it per change.** A reviewer, human or another model, looks for hard-coded answers in each change.

The split only works if the test set stays unseen. Keep it outside the repository the agents work in, strip its examples from any text they read (the CommonMark spec contains every example), and never let a fix be guided by a held-out failure: once a held-out test steers the work, it has become training data.

The same rule applies one level up. Whoever tunes the agents' setup on a project turns that project into training data for the setup; the honest test of the setup is a new problem it was not tuned on.

## Self-supervised development: checks the program supplies itself

Most real work comes without a labeled test suite. Self-supervised learning has the same problem and the same answer: take the training signal from the data itself, as a language model learns by predicting a hidden word from its context. In software, the checks come from the program and its specification, with no one writing down expected outputs. Testing research calls this the oracle problem, and has five well-tried answers.

**1. Round trips.** Two operations that undo each other must give back what went in. Nobody needs to know the right output for any input.

```python
from hypothesis import given, strategies as st

@given(st.dictionaries(st.text(), st.integers()))
def test_round_trip(table):
    assert loads(dumps(table)) == table
```

**2. Properties.** Statements that hold for every input, checked on thousands of generated ones (property-based testing).

```python
@given(st.text())
def test_never_crashes_and_stays_well_formed(markdown):
    html = to_html(markdown)          # must not raise
    assert is_well_formed(html)       # balanced tags, escaped text
```

**3. Metamorphic relations.** How outputs must relate when inputs change in a known way: one input checks another.

```python
def test_more_blank_lines_change_nothing():
    assert to_html("a\n\nb") == to_html("a\n\n\n\nb")
```

**4. Differential testing.** Two independently written implementations of the same thing; wherever they disagree, at least one is wrong. With agents this is cheap: two teams, on different models, write the same function.

**5. Mutation testing.** The check of the checks: plant small bugs in the program on purpose and see whether the tests notice. Tests that miss them are too weak to trust, however many pass.

## Where self-supervision stops: the semi-supervised recipe

Self-derived checks catch a program that disagrees with itself. They cannot catch a program that consistently does the wrong thing. A round trip passes if both directions share the same misunderstanding; two implementations can agree on the same mistake, and they are more likely to when similar models wrote them, since such models share their errors.

So the working recipe borrows from semi-supervised learning: many cheap self-derived checks, anchored by a few examples with known answers.

| Kind of check | What it catches | What it costs | What it can't do |
| --- | --- | --- | --- |
| A few labeled examples | Wrong understanding of the goal | Someone must write the answers | Cover much ground |
| Self-derived checks | Crashes, inconsistencies, broken invariants | Almost nothing; agents can write them | Prove the goal is met |
| Held-out examples | Overfitting to the visible checks | Setting some labeled examples aside | Steer the work |
| A person at the end | What no check captured | Attention | Scale |

The weaker the checks, the more careful the process should be: smaller steps that are easier to judge, several independent reviewers on different models, earlier escalation to a person, and more modest claims of success.

## A checklist for your own project

1. **Find the labeled data first.** A spec with examples, a conformance suite, a reference implementation, past bug fixes. If there is none, write a few acceptance tests from the task before any code exists.
2. **Split before you start.** Set every fifth example aside, store it outside the agents' reach, and remove it from any text they read.
3. **Protect what the builders can see.** They may read the acceptance tests, never change or delete them.
4. **Add self-derived checks.** Round trips, properties, metamorphic relations; where it matters, a second implementation to compare against.
5. **Test the tests.** Plant bugs and make sure the checks notice.
6. **Report two numbers.** Visible and held-out pass rates, side by side. A gap between them means overfitting.
7. **Change the setup, then test it elsewhere.** A setup tuned on one project is proven only on the next one.
8. **Keep a person at the end** wherever the checks can't say whether the goal is met.
