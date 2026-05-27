import yaml

from vault_conductor.config import load_config


def test_load_config_upgrades_legacy_builtin_agent_templates(tmp_path):
    vault = tmp_path / "Agent Control Room"
    system = vault / "90 System"
    system.mkdir(parents=True)
    (system / "control-room.config.yml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "vault_path": str(vault),
                "repos_root": str(tmp_path / "repos"),
                "agents": {
                    "codex": {
                        "enabled": True,
                        "type": "command",
                        "command": "codex",
                        "mode": "exec",
                        "args": ["exec", "--cd", "{{worktree}}", "{{prompt}}"],
                        "env": {},
                    },
                    "claude": {
                        "enabled": True,
                        "type": "command",
                        "command": "claude",
                        "mode": "interactive",
                        "args": ["{{prompt}}"],
                        "env": {},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(vault=vault, repos=tmp_path / "repos", runtime_root=tmp_path / "runtime")

    assert config.agents["codex"].command == "cmux"
    assert config.agents["codex"].args == ["codex-teams"]
    assert config.agents["claude"].command == "cmux"
    assert config.agents["claude"].args == ["claude-teams"]
