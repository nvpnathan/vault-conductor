# vault-conductor

`vault-conductor` is the Python CLI for an Obsidian Agent Control Room. The durable source of truth is Markdown in `~/Agent Control Room`; `conductor` owns task notes, the Kanban board, worktrees, run notes, prompts, logs, and cmux live sessions.

## Quick Start

Prerequisites: Python 3.10+, `uv`, Git, cmux, Obsidian with the Kanban plugin, and optionally `codex`, `claude`, and `gh`.

```bash
git clone <vault-conductor-repo-url>
cd vault-conductor
bash setup.sh

uv run conductor init --vault "$HOME/Agent Control Room" --repos "$HOME/repos" --no-open
uv run conductor doctor --json
uv run conductor scan
uv run conductor new --repo my-repo --title "Fix failing tests" --agent codex --status ready
uv run conductor start AGT-0001
```

Codex and Claude use cmux providers by default:

- `codex`: `cmux codex-teams`
- `claude`: `cmux claude-teams`

Custom providers can be configured in `90 System/control-room.config.yml`.

## Install the Codex Skill

This repo includes an optional Codex skill for agents working with conductor-managed vaults.

```bash
cd vault-conductor
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/agent-control-room"
cp -R skills/agent-control-room "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Restart Codex after installing the skill so it is discovered in future sessions.

## Vault Layout

```text
00 Control Room/        Kanban board and dashboards
10 Projects/            Repo/project notes
20 Agent Tasks/         One Markdown note per task
30 Agent Runs/          One Markdown note per run attempt
40 Decisions/           Human decisions
50 Templates/           Generated templates
90 System/              Config, repo registry, state
```

Runtime files live under `~/.agent-control-room/`:

```text
worktrees/<repo>/<task-id>/  Agent worktrees
logs/                        Transcript and test logs
prompts/                     Prompt files and follow-ups
state/sessions.json          Live cmux session registry
```

Use `--runtime-root <path>` to isolate those runtime files for testing or alternate control rooms.

## Commands

```bash
uv run conductor init --no-open
uv run conductor doctor --json
uv run conductor scan
uv run conductor new --repo my-repo --title "Implement feature" --status ready
uv run conductor status
uv run conductor start AGT-0001
uv run conductor send AGT-0001 "Please add a regression test"
uv run conductor log AGT-0001 --tail 100
uv run conductor diff AGT-0001 --stat --save
uv run conductor test AGT-0001
uv run conductor pr AGT-0001 --commit --yes
uv run conductor stop AGT-0001 --park
uv run conductor --dry-run cleanup AGT-0001 --yes
uv run conductor sync
uv run conductor watch
uv run conductor dashboard
```

Only a human may mark a task done:

```bash
uv run conductor mark AGT-0001 done --human
```

## Statuses

`conductor` preserves these status slugs and board columns:

| Status | Column |
| --- | --- |
| `backlog` | Backlog |
| `ready` | Ready |
| `running` | Running |
| `needs-human` | Needs Human |
| `review-diff` | Review Diff |
| `needs-revision` | Needs Revision |
| `pr-opened` | PR Opened |
| `done` | Done |
| `failed` | Failed / Parked |
| `parked` | Failed / Parked |

Task note frontmatter is authoritative. `conductor sync --board-wins` is available when a human intentionally wants board placement to override task note status.

## Development

```bash
uv sync --dev
uv run pytest
uv run conductor doctor --json
```
