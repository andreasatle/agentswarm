import json
from pathlib import Path

from agentswarm.evaluation.toml import TomlImport


class Suite:
    """A toml-test checkout in miniature: six valid integer files, one invalid one, and the spec."""

    def __init__(self, root: Path) -> None:
        self.root = root
        tests = root / "tests"
        listed: list[str] = []
        for n in range(1, 7):
            (tests / "valid" / "integer").mkdir(parents=True, exist_ok=True)
            (tests / "valid" / "integer" / f"i{n}.toml").write_text(f"a = {n}\n")
            (tests / "valid" / "integer" / f"i{n}.json").write_text(
                json.dumps({"a": {"type": "integer", "value": str(n)}})
            )
            listed += [f"valid/integer/i{n}.toml", f"valid/integer/i{n}.json"]
        (tests / "invalid" / "encoding").mkdir(parents=True)
        (tests / "invalid" / "encoding" / "bad.toml").write_bytes(b"a = '\xff'\n")
        listed.append("invalid/encoding/bad.toml")
        (tests / "valid" / "spec-1.0.0").mkdir(parents=True)
        (tests / "valid" / "spec-1.0.0" / "s.toml").write_text("")
        (tests / "valid" / "spec-1.0.0" / "s.json").write_text("{}")
        listed.append("valid/spec-1.0.0/s.toml")
        (tests / "files-toml-1.0.0").write_text("\n".join(listed) + "\n")
        (root / "specs").mkdir()
        (root / "specs" / "v1.0.0.md").write_text("# TOML v1.0.0\n")


class TestTomlImport:
    def test_one_file_per_group_with_every_fifth_held_out_and_the_spec_as_docs(self, tmp_path: Path) -> None:
        summary = TomlImport(Suite(tmp_path / "suite").root, tmp_path / "out").write()
        assert (summary.groups, summary.visible, summary.held_out) == (3, 7, 1)
        tests = sorted(p.name for p in (summary.seed / "tests" / "acceptance").glob("test_*.py"))
        assert tests == [  # valid groups first; names fit Python modules
            "test_toml_01_valid_integer.py",
            "test_toml_02_valid_spec_1_0_0.py",
            "test_toml_03_invalid_encoding.py",
        ]
        assert [p.name for p in summary.held_out_tests.iterdir()] == ["test_held_out_01_valid_integer.py"]
        assert (summary.seed / "docs" / "toml-v1.0.0.md").read_text() == "# TOML v1.0.0\n"
        invalid = (summary.seed / "tests" / "acceptance" / "test_toml_03_invalid_encoding.py").read_text()
        assert "b\"a = '\\xff'\\n\"" in invalid  # bytes, since the file isn't valid UTF-8
        for path in (summary.seed / "tests" / "acceptance").glob("*.py"):
            compile(path.read_text(), str(path), "exec")
        assert "return {}" in (summary.seed / "tomlparse" / "__init__.py").read_text()  # starts near 0%
