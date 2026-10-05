from agentswarm.cli import Cli
from agentswarm.judging.judges import JudgeCatalog
from agentswarm.llm.workers import Prompts
from agentswarm.prompts import PromptCatalogue
from agentswarm.teams.catalog import TeamCatalog
from tests.support.paths import RepoPaths


class TestPromptCatalogue:
    def test_the_document_is_current(self) -> None:
        document = (RepoPaths.ROOT / PromptCatalogue.DOCUMENT).read_text()
        assert document == PromptCatalogue.standard().render(), (
            f"{PromptCatalogue.DOCUMENT} is out of date; run `{PromptCatalogue.REGENERATE}`"
        )

    def test_every_team_that_calls_a_model_is_shown_once(self) -> None:
        uses = [u for f in PromptCatalogue.standard().families for u in f.uses()]
        teams = set(TeamCatalog.standard().names()) - PromptCatalogue.NOT_MODEL_TEAMS
        assert sorted(uses) == sorted(teams)

    def test_shows_every_judge_and_the_instructions_verbatim(self) -> None:
        catalogue = PromptCatalogue.standard()
        text = catalogue.render()
        for judge in JudgeCatalog.standard().judges():
            assert f"### {judge.name}\n\nVersion `{judge.version}`" in text
            for question in judge.questions:
                assert question.ask.instructions in text
        for family in catalogue.families:
            for caller in family.callers:
                assert f"```text\n{caller.instructions}\n```" in text
            assert Prompts.data(family.payload) in text

    def test_the_cli_prints_it(self) -> None:
        shown: list[str] = []
        assert Cli(show=shown.append).dispatch(["prompts"]) == 0
        assert shown[0] + "\n" == PromptCatalogue.standard().render()
