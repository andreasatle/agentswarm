from agentswarm.core.config import JevConfig, JevMode
from agentswarm.judging.judgement import ModeTable


class TestModeTable:
    def test_points_named_by_a_pattern_are_active_the_rest_use_the_default(self) -> None:
        modes = ModeTable.of(JevConfig(mode=JevMode.SHADOW, active=("gate:raw->experimental", "relevance:*")))
        assert modes.mode_for("gate:raw->experimental") is JevMode.ACTIVE
        assert modes.mode_for("relevance:red_team") is JevMode.ACTIVE
        assert modes.mode_for("size:raw") is JevMode.SHADOW
        assert modes.mode_for("gate:experimental->candidate") is JevMode.SHADOW

    def test_an_active_default_makes_every_point_active(self) -> None:
        assert ModeTable(JevMode.ACTIVE).mode_for("drift") is JevMode.ACTIVE
