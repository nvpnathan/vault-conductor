from pathlib import Path


def test_repo_packages_conductor_skill_and_install_docs():
    root = Path(__file__).resolve().parents[1]
    skill = root / "skills" / "agent-control-room" / "SKILL.md"
    readme = root / "README.md"

    assert skill.exists()
    text = skill.read_text(encoding="utf-8")
    legacy_cli = "agent" + "ctl"
    assert "name: agent-control-room" in text
    assert "uv run conductor" in text
    assert legacy_cli not in text

    readme_text = readme.read_text(encoding="utf-8")
    assert "Install the Codex Skill" in readme_text
    assert "skills/agent-control-room" in readme_text
