from pathlib import Path


def test_repo_packages_conductor_skill_and_install_docs():
    root = Path(__file__).resolve().parents[1]
    skill = root / "skills" / "agent-control-room" / "SKILL.md"
    readme = root / "README.md"

    assert skill.exists()
    text = skill.read_text(encoding="utf-8")
    legacy_cli = "agent" + "ctl"
    assert "name: agent-control-room" in text
    assert "conductor activity" in text
    assert "conductor pr AGT-0001 --auto" in text
    assert 'conductor mark AGT-0001 needs-human --question "<one specific question?>"' in text
    assert "AGENT_QUESTION: <one specific question?>" in text
    assert "cmux codex-teams" in text
    assert "Only the human may mark `done`" in text
    assert "uv run conductor" in text
    assert legacy_cli not in text

    readme_text = readme.read_text(encoding="utf-8")
    assert "Agent Control Room Skill" in readme_text
    assert "setup.sh` installs the Codex skill copy automatically" in readme_text
    assert "skills/agent-control-room" in readme_text
    assert "conductor pr AGT-0001 --auto" in readme_text
    assert 'conductor mark AGT-0001 needs-human --question "<one specific question?>"' in readme_text
